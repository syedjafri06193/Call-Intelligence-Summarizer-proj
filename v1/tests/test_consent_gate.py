"""The consent gate (design.md sections 3.1-3.4).

The design document calls this "the most important test file" in the
repository, and the reason is that every failure here is a legal failure
rather than a functional one. A gate that is wrong does not produce a bad
summary; it produces a recording that should not exist, with statutory damages
attached per violation and class-action exposure behind them.

So the tests are written as the legal positions they encode, not as coverage
of the functions.
"""

from __future__ import annotations

import datetime as dt

import pytest

from cis.consent.detect import (
    CONSENT_WINDOW_MS,
    DeletionRequired,
    classify_response,
    detect_consent,
    find_announcement,
)
from cis.consent.gate import (
    Blocker,
    ConsentDenied,
    ConsentMode,
    ConsentPolicy,
    may_process,
    roster_fingerprint,
    verify_decision_still_valid,
)
from cis.consent.model import (
    Call,
    ConsentLedger,
    ConsentMethod,
    ConsentRecord,
    Participant,
)

from .conftest import NOW, segment
from cis.transcript.model import Transcript


# ----------------------------------------------------------- default deny


class TestDefaultDeny:
    """`unknown` is not `consented`, everywhere (section 3.1)."""

    def test_an_empty_ledger_denies(self, call):
        decision = may_process(call, ConsentLedger(), now=NOW)
        assert not decision.allowed
        assert len(decision.blocks) == 3
        assert all(b.blocker is Blocker.NO_CONSENT_RECORD for b in decision.blocks)

    def test_the_default_policy_is_all_party(self):
        # Section 3.2: "the operationally correct default is to treat every
        # call as all-party", because of the interstate rule and BIPA's
        # residency trigger.
        assert ConsentPolicy().mode is ConsentMode.ALL_PARTY

    def test_host_consent_alone_is_not_enough(self, call):
        # The specific allegation in the Otter litigation: consent obtained
        # "at most, from the host who added the assistant". Section 2.2 says
        # flatly that single-host consent is not a defence.
        ledger = ConsentLedger(
            [
                ConsentRecord(
                    call_id=call.call_id,
                    participant_id="p_rep",
                    method=ConsentMethod.VERBAL_ACKNOWLEDGED,
                    evidence_ref="s1:0-20@v1",
                    recorded_at=NOW,
                )
            ]
        )
        decision = may_process(call, ledger, now=NOW)
        assert not decision.allowed
        assert {b.participant_id for b in decision.blocks} == {"p_dana", "p_marco"}

    def test_a_declined_participant_blocks_the_call(self, consented_ledger, call):
        consented_ledger.append(
            ConsentRecord(
                call_id=call.call_id,
                participant_id="p_marco",
                method=ConsentMethod.DECLINED,
                evidence_ref="s3:0-15@v1",
                recorded_at=NOW + dt.timedelta(seconds=1),
            )
        )
        decision = may_process(call, consented_ledger, now=NOW)
        assert not decision.allowed
        assert decision.blocks[0].blocker is Blocker.DECLINED

    def test_an_empty_roster_denies_rather_than_trivially_allowing(self, call):
        # A call with nobody on it passes an "all participants consented" test
        # vacuously. An empty roster almost always means the platform metadata
        # failed to load, and processing anyway would be processing a call we
        # know nothing about.
        empty = Call(
            call_id=call.call_id,
            external_id=call.external_id,
            participants=(),
            scheduled_at=call.scheduled_at,
        )
        decision = may_process(empty, ConsentLedger(), now=NOW)
        assert not decision.allowed
        assert decision.blocks[0].blocker is Blocker.NO_PARTICIPANTS

    def test_a_fully_consented_call_is_allowed(self, call, consented_ledger):
        decision = may_process(call, consented_ledger, now=NOW)
        assert decision.allowed
        assert set(decision.consenting_participants) == {
            "p_rep",
            "p_dana",
            "p_marco",
        }


# ------------------------------------------------------- evidence required


class TestEvidence:
    """Every consenting record points at something verifiable (section 3.1)."""

    @pytest.mark.parametrize(
        "method",
        [
            ConsentMethod.VERBAL_ACKNOWLEDGED,
            ConsentMethod.PLATFORM_CONSENT,
            ConsentMethod.WRITTEN_PRIOR,
        ],
    )
    def test_a_consenting_record_without_evidence_is_rejected(self, method):
        with pytest.raises(ValueError, match="evidence_ref"):
            ConsentRecord(
                call_id="call_001",
                participant_id="p_dana",
                method=method,
                evidence_ref=None,
                recorded_at=NOW,
            )

    def test_a_refusal_needs_no_evidence(self):
        # Asymmetric on purpose. Permitting processing requires evidence;
        # stopping it does not, because refusing to honour a refusal on
        # documentation grounds is not a defensible position.
        record = ConsentRecord(
            call_id="call_001",
            participant_id="p_dana",
            method=ConsentMethod.DECLINED,
            evidence_ref=None,
            recorded_at=NOW,
        )
        assert not record.permits_processing

    def test_a_naive_timestamp_is_rejected(self):
        with pytest.raises(ValueError, match="timezone-aware"):
            ConsentRecord(
                call_id="call_001",
                participant_id="p_dana",
                method=ConsentMethod.DECLINED,
                evidence_ref=None,
                recorded_at=dt.datetime(2026, 9, 18, 15, 0),
            )


# ------------------------------------------------------------ append-only


class TestAppendOnly:
    """Withdrawal is a new record, not an edit (section 3.1)."""

    def test_withdrawal_supersedes_without_erasing(self, call, consented_ledger):
        before = len(consented_ledger.history(call.call_id, "p_dana"))
        consented_ledger.withdraw(
            call.call_id, "p_dana", at=NOW + dt.timedelta(hours=1)
        )

        history = consented_ledger.history(call.call_id, "p_dana")
        assert len(history) == before + 1
        # The original record is still there, unchanged. That history is the
        # artifact that answers "what did we believe, and when".
        assert history[0].method is ConsentMethod.PLATFORM_CONSENT
        assert history[-1].method is ConsentMethod.WITHDRAWN

        assert (
            consented_ledger.effective(call.call_id, "p_dana")
            is ConsentMethod.WITHDRAWN
        )

    def test_a_withdrawal_blocks_further_processing(self, call, consented_ledger):
        consented_ledger.withdraw(
            call.call_id, "p_dana", at=NOW + dt.timedelta(hours=1)
        )
        decision = may_process(call, consented_ledger, now=NOW)
        assert not decision.allowed
        assert decision.blocks[0].blocker is Blocker.WITHDRAWN

    def test_latest_record_wins_regardless_of_insertion_order(self, call):
        # Records can arrive out of order from a platform webhook.
        ledger = ConsentLedger()
        ledger.append(
            ConsentRecord(
                call_id=call.call_id,
                participant_id="p_dana",
                method=ConsentMethod.WITHDRAWN,
                evidence_ref=None,
                recorded_at=NOW + dt.timedelta(hours=2),
            )
        )
        ledger.append(
            ConsentRecord(
                call_id=call.call_id,
                participant_id="p_dana",
                method=ConsentMethod.PLATFORM_CONSENT,
                evidence_ref="evt_1",
                recorded_at=NOW,
            )
        )
        assert ledger.effective(call.call_id, "p_dana") is ConsentMethod.WITHDRAWN


# ---------------------------------------------------------- late joiners


class TestLateJoiners:
    """Someone who missed the announcement did not hear it (section 3.4)."""

    def test_a_late_joiner_is_blocked_even_with_a_verbal_record(self, call):
        roster = tuple(
            Participant(
                p.participant_id,
                p.display_name,
                p.email,
                p.role,
                joined_late=(p.participant_id == "p_marco"),
            )
            for p in call.participants
        )
        late_call = Call(
            call_id=call.call_id,
            external_id=call.external_id,
            participants=roster,
            scheduled_at=call.scheduled_at,
        )

        ledger = ConsentLedger()
        for p in roster:
            ledger.append(
                ConsentRecord(
                    call_id=call.call_id,
                    participant_id=p.participant_id,
                    method=ConsentMethod.VERBAL_ACKNOWLEDGED,
                    evidence_ref="s1:0-20@v1",
                    recorded_at=NOW,
                )
            )

        decision = may_process(late_call, ledger, now=NOW)
        assert not decision.allowed
        assert decision.blocks[0].blocker is Blocker.LATE_JOINER

    def test_a_late_joiner_with_platform_consent_is_fine(self, call):
        # The platform prompt fires for whoever joins, whenever they join, so
        # it covers a late arrival in a way an announcement they missed cannot.
        roster = tuple(
            Participant(
                p.participant_id, p.display_name, p.email, p.role,
                joined_late=(p.participant_id == "p_marco"),
            )
            for p in call.participants
        )
        late_call = Call(
            call_id=call.call_id,
            external_id=call.external_id,
            participants=roster,
            scheduled_at=call.scheduled_at,
        )
        ledger = ConsentLedger()
        for p in roster:
            ledger.append(
                ConsentRecord(
                    call_id=call.call_id,
                    participant_id=p.participant_id,
                    method=ConsentMethod.PLATFORM_CONSENT,
                    evidence_ref=f"evt_{p.participant_id}",
                    recorded_at=NOW,
                )
            )
        assert may_process(late_call, ledger, now=NOW).allowed


# ------------------------------------------------------ relaxing the default


class TestOneParty:
    """Relaxing all-party is deliberately awkward (section 3.2)."""

    def test_one_party_mode_requires_a_named_authoriser(self):
        with pytest.raises(ValueError, match="authorised_by"):
            ConsentPolicy(mode=ConsentMode.ONE_PARTY)

    def test_one_party_still_needs_someone_to_consent(self, call):
        policy = ConsentPolicy(
            mode=ConsentMode.ONE_PARTY,
            authorised_by="legal@vendor.example",
            authorised_at=NOW,
        )
        decision = may_process(call, ConsentLedger(), policy, now=NOW)
        assert not decision.allowed, "'one party' means one consented, not none"

    def test_one_party_allows_with_a_single_record_and_names_the_authoriser(
        self, call
    ):
        ledger = ConsentLedger(
            [
                ConsentRecord(
                    call_id=call.call_id,
                    participant_id="p_rep",
                    method=ConsentMethod.VERBAL_ACKNOWLEDGED,
                    evidence_ref="s1:0-20@v1",
                    recorded_at=NOW,
                )
            ]
        )
        policy = ConsentPolicy(
            mode=ConsentMode.ONE_PARTY,
            authorised_by="legal@vendor.example",
            authorised_at=NOW,
        )
        decision = may_process(call, ledger, policy, now=NOW)
        assert decision.allowed
        # The authoriser appears in the decision, so the audit trail carries
        # who relaxed the default rather than just that it was relaxed.
        assert "legal@vendor.example" in decision.reason
        # And the participants who never consented are still reported.
        assert len(decision.blocks) == 2


# ------------------------------------------------ the gate cannot be bypassed


class TestGateIntegrity:
    """The decision cannot outlive the facts it was computed from."""

    def test_require_raises_rather_than_returning_false(self, call):
        decision = may_process(call, ConsentLedger(), now=NOW)
        with pytest.raises(ConsentDenied):
            decision.require()

    def test_a_participant_joining_after_the_gate_invalidates_it(
        self, call, consented_ledger
    ):
        # The window between the gate running and the fetch starting is real
        # -- minutes, in a durable workflow. Someone joining in that window is
        # exactly what the gate exists to catch.
        decision = may_process(call, consented_ledger, now=NOW)
        assert decision.allowed

        joined = Call(
            call_id=call.call_id,
            external_id=call.external_id,
            participants=call.participants
            + (Participant("p_late", "Priya Raman", "priya@acme.example"),),
            scheduled_at=call.scheduled_at,
        )
        with pytest.raises(ConsentDenied, match="roster changed"):
            verify_decision_still_valid(decision, joined)

    def test_a_decision_from_another_call_is_rejected(self, call, consented_ledger):
        decision = may_process(call, consented_ledger, now=NOW)
        other = Call(
            call_id="call_999",
            external_id="zoom_other",
            participants=call.participants,
            scheduled_at=call.scheduled_at,
        )
        with pytest.raises(ConsentDenied, match="was made for call"):
            verify_decision_still_valid(decision, other)

    def test_the_fingerprint_ignores_participant_order(self, call):
        forward = roster_fingerprint(call.participants)
        backward = roster_fingerprint(tuple(reversed(call.participants)))
        assert forward == backward, "platforms report participants in any order"

    def test_the_fingerprint_notices_a_late_joiner_flag_change(self, call):
        before = roster_fingerprint(call.participants)
        after = roster_fingerprint(
            tuple(
                Participant(
                    p.participant_id, p.display_name, p.email, p.role,
                    joined_late=(p.participant_id == "p_marco"),
                )
                for p in call.participants
            )
        )
        assert before != after


# ------------------------------------------------- verbal consent detection


class TestVerbalDetection:
    """The narrowly-scoped pass (section 3.3)."""

    def test_it_finds_the_announcement_and_the_responses(
        self, call, opening_transcript
    ):
        run = detect_consent(call, opening_transcript, now=NOW)

        assert run.announcement is not None
        assert run.announcement.segment_id == "s1"

        by_participant = {r.participant_id: r.method for r in run.records}
        assert by_participant["p_dana"] is ConsentMethod.VERBAL_ACKNOWLEDGED
        assert by_participant["p_marco"] is ConsentMethod.VERBAL_ACKNOWLEDGED
        # The person who made the announcement consented by making it.
        assert by_participant["p_rep"] is ConsentMethod.VERBAL_ACKNOWLEDGED
        assert run.unresolved_participants == ()

    def test_every_record_cites_a_transcript_span(self, call, opening_transcript):
        run = detect_consent(call, opening_transcript, now=NOW)
        for record in run.records:
            if record.permits_processing:
                assert record.evidence_ref
                assert "@v1" in record.evidence_ref

    def test_silence_produces_unresolved_not_consent(self, call):
        # Section 3.4, the whole of it: Marco says nothing.
        transcript = Transcript(
            "call_001",
            [
                segment(
                    "s1",
                    "p_rep",
                    "Before we start, I'm recording this call. "
                    "Is everyone okay with that?",
                    0,
                ),
                segment("s2", "p_dana", "Yes, that's fine.", 6_000),
                segment("s3", "p_rep", "Great, let's dive in.", 9_000),
            ],
        )
        run = detect_consent(call, transcript, now=NOW)
        assert "p_marco" in run.unresolved_participants
        assert not any(r.participant_id == "p_marco" for r in run.records)

        # And the gate then blocks, which is the point of the whole exercise.
        ledger = ConsentLedger(run.records)
        assert not may_process(call, ledger, now=NOW).allowed

    def test_no_announcement_demands_deletion(self, call):
        transcript = Transcript(
            "call_001",
            [
                segment("s1", "p_rep", "Hey, good to see you. How was the trip?", 0),
                segment("s2", "p_dana", "Long, but fine, thanks.", 4_000),
            ],
        )
        with pytest.raises(DeletionRequired, match="no recording announcement"):
            detect_consent(call, transcript, now=NOW)

    def test_host_only_consent_demands_deletion(self, call):
        # The announcer consents to their own recording by announcing it, so a
        # pass that finds only the announcement always produces one permitting
        # record. Treating that as success would mean the narrow exception --
        # ninety seconds of audio from people who had not consented -- bought
        # nothing and we kept the audio anyway.
        #
        # That is host-only consent, which section 2.2 says is not a defence
        # and is the specific allegation in the Otter litigation.
        transcript = Transcript(
            "call_001",
            [
                segment("s1", "p_rep", "I'm recording this call, by the way.", 0),
                segment("s2", "p_dana", "So about the integration timeline.", 5_000),
            ],
        )
        with pytest.raises(DeletionRequired, match="other than the announcer"):
            detect_consent(call, transcript, now=NOW)

    def test_one_other_participant_responding_is_enough_to_keep_going(self, call):
        # Partial consent is a real result, not a failure. The gate still
        # blocks on Marco, the rep re-asks, and the call can be re-gated --
        # deleting here would throw away Dana's genuine acknowledgment.
        transcript = Transcript(
            "call_001",
            [
                segment("s1", "p_rep", "I'm recording this call. Everyone okay?", 0),
                segment("s2", "p_dana", "Yes, that's fine.", 5_000),
                segment("s3", "p_marco", "So about the integration timeline.", 9_000),
            ],
        )
        run = detect_consent(call, transcript, now=NOW)
        assert "p_marco" in run.unresolved_participants

        decision = may_process(call, ConsentLedger(run.records), now=NOW)
        assert not decision.allowed
        assert [b.participant_id for b in decision.blocks] == ["p_marco"]

    def test_the_pass_only_sees_the_opening_window(self, call):
        # The scope is the entire justification for doing this at all. A
        # consent detected at minute twenty is not a consent that preceded
        # twenty minutes of recording.
        late = CONSENT_WINDOW_MS + 60_000
        transcript = Transcript(
            "call_001",
            [
                segment("s1", "p_rep", "Hi everyone, thanks for joining.", 0),
                segment(
                    "s2", "p_rep", "Oh, I should say I'm recording this call.", late
                ),
                segment("s3", "p_dana", "Sure, that's fine.", late + 4_000),
            ],
        )
        assert find_announcement(transcript) is None
        with pytest.raises(DeletionRequired):
            detect_consent(call, transcript, now=NOW)

    def test_the_run_records_what_it_examined(self, call, opening_transcript):
        # So the narrow scope is demonstrable rather than asserted.
        run = detect_consent(call, opening_transcript, now=NOW)
        assert run.window_ms == CONSENT_WINDOW_MS
        assert run.segments_examined == len(opening_transcript.window(0, CONSENT_WINDOW_MS))
        assert run.detector_version == "1"

    def test_agreement_before_the_announcement_does_not_count(self, call):
        # "Sure" in the small talk is not a response to a question that has
        # not been asked yet.
        transcript = Transcript(
            "call_001",
            [
                segment("s0", "p_dana", "Sure, sounds good, let's do it.", 0),
                segment("s1", "p_rep", "I'm recording this call. Okay with everyone?", 4_000),
                segment("s2", "p_marco", "Yep, fine by me.", 9_000),
            ],
        )
        run = detect_consent(call, transcript, now=NOW)
        assert "p_dana" in run.unresolved_participants


class TestResponseClassification:
    """Ambiguity resolves to UNKNOWN, never to consent."""

    @pytest.mark.parametrize(
        "text",
        [
            "Yes, that's fine.",
            "Sure, go ahead.",
            "Yeah no problem.",
            "Okay with me.",
            "Absolutely.",
            "That sounds fine.",
            "I'm fine with that.",
        ],
    )
    def test_affirmatives(self, text):
        assert classify_response(text) is ConsentMethod.VERBAL_ACKNOWLEDGED

    @pytest.mark.parametrize(
        "text",
        [
            "I'd rather you didn't.",
            "Please don't record this.",
            "I'm not comfortable with that.",
            "Can we not record today?",
            "No, I don't consent to that.",
            "Turn it off, please.",
        ],
    )
    def test_refusals(self, text):
        assert classify_response(text) is ConsentMethod.DECLINED

    @pytest.mark.parametrize(
        "text",
        [
            "Hmm.",
            "Let me just grab my notes.",
            "Can you hear me now?",
            "So where were we?",
            "I think Marco is still joining.",
        ],
    )
    def test_ambiguous_text_is_unknown(self, text):
        assert classify_response(text) is ConsentMethod.UNKNOWN

    def test_a_refusal_that_opens_with_no_is_not_read_as_consent(self):
        # "No, I'd rather not" and "No, that's fine" both open with "no".
        # Treating an ambiguous utterance as consent is the failure that
        # matters, so refusals are checked first and written specifically.
        assert classify_response("No, I'd rather not.") is ConsentMethod.DECLINED
        assert classify_response("No, please don't.") is ConsentMethod.DECLINED
        assert (
            classify_response("No, that's fine, go ahead.")
            is ConsentMethod.VERBAL_ACKNOWLEDGED
        )


class TestRelaxationsNeedAnAuthoriser:
    """Section 3.2: relaxing the default needs a named authoriser, logged.

    `ONE_PARTY` was guarded from the start. `late_joiners_require_consent`
    relaxes section 3.4 -- a participant who missed the announcement is
    UNKNOWN -- and is exactly the same kind of decision.
    """

    def test_turning_off_the_late_joiner_rule_needs_authorisation(self):
        with pytest.raises(ValueError, match="authorised_by"):
            ConsentPolicy(late_joiners_require_consent=False)

    def test_with_an_authoriser_it_is_permitted_and_recorded(self):
        policy = ConsentPolicy(
            late_joiners_require_consent=False, authorised_by="counsel@acme.example"
        )
        assert policy.authorised_by == "counsel@acme.example"

    def test_the_default_needs_nothing(self):
        assert ConsentPolicy().late_joiners_require_consent is True


class TestOnePartyDoesNotOverrideARefusal:
    """"One party consented" answers "did anyone say yes", not "did anyone
    say no". docs/legal.md commits to withdrawal being as easy as consent,
    and a policy switch that silently outranks a withdrawal would break that.
    """

    def _policy(self):
        return ConsentPolicy(
            mode=ConsentMode.ONE_PARTY, authorised_by="counsel@acme.example"
        )

    def test_a_declining_participant_stops_processing(self, call, participants):
        ledger = ConsentLedger(
            [
                ConsentRecord(
                    call_id=call.call_id,
                    participant_id="p_rep",
                    method=ConsentMethod.PLATFORM_CONSENT,
                    evidence_ref="evt_1",
                    recorded_at=NOW,
                ),
                ConsentRecord(
                    call_id=call.call_id,
                    participant_id="p_dana",
                    method=ConsentMethod.DECLINED,
                    evidence_ref=None,
                    recorded_at=NOW,
                ),
            ]
        )
        decision = may_process(call, ledger, self._policy(), now=NOW)
        assert not decision.allowed
        assert "explicit refusal" in decision.reason
        assert "p_dana" in decision.reason

    def test_a_withdrawal_stops_processing(self, call, consented_ledger):
        consented_ledger.append(
            ConsentRecord(
                call_id=call.call_id,
                participant_id="p_marco",
                method=ConsentMethod.WITHDRAWN,
                evidence_ref=None,
                recorded_at=NOW + dt.timedelta(minutes=5),
            )
        )
        decision = may_process(call, consented_ledger, self._policy(), now=NOW)
        assert not decision.allowed
        assert "p_marco" in decision.reason

    def test_silence_still_does_not_stop_a_one_party_policy(self, call):
        # UNKNOWN is the case one-party mode exists for. Only an explicit
        # refusal blocks.
        ledger = ConsentLedger(
            [
                ConsentRecord(
                    call_id=call.call_id,
                    participant_id="p_rep",
                    method=ConsentMethod.PLATFORM_CONSENT,
                    evidence_ref="evt_1",
                    recorded_at=NOW,
                )
            ]
        )
        decision = may_process(call, ledger, self._policy(), now=NOW)
        assert decision.allowed
