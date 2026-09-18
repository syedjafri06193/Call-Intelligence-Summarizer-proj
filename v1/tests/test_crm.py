"""CRM writeback (design.md section 9).

    "This is where trust is won or lost, permanently."

The tests that matter most here are the negative ones. A writeback layer is
judged by what it refuses to do: overwrite a rep's note, create a task nobody
confirmed, duplicate a record on reprocessing, or fail silently.
"""

from __future__ import annotations

import datetime as dt

import pytest

from cis.consent.model import Call, Participant, ParticipantRole
from cis.crm.idempotency import (
    Provenance,
    identity_key,
    task_fingerprint,
    upsert,
)
from cis.crm.tasks import (
    TASK_THRESHOLD,
    ProposedTask,
    TaskMustBeConfirmed,
    propose_tasks,
    resolve_due,
)
from cis.crm.writeback import (
    AUTO_PRECISION_FLOOR,
    FieldPolicy,
    PolicyError,
    RateLimited,
    WriteAction,
    WriteMode,
    WritebackJournal,
    with_backoff,
    write_field,
    write_payload,
)
from cis.extract.grounded import Field, GroundedClaim
from cis.transcript.model import Transcript

from .conftest import segment


class FakeCRM:
    """A CRM with provenance, which is the part that matters here."""

    def __init__(self, fields: dict[str, str] | None = None, ai_fields: set[str] | None = None):
        self.fields = dict(fields or {})
        self.ai_authored = set(ai_fields or ())
        self.records: dict[str, dict] = {}
        self.creates = 0
        self.updates = 0
        self.fail_with: Exception | None = None

    def get_field(self, record_id: str, field: str) -> str | None:
        return self.fields.get(field)

    def is_ai_authored(self, record_id: str, field: str) -> bool:
        return field in self.ai_authored

    def find_by_external_key(self, key: str):
        for record in self.records.values():
            if record.get("external_key") == key:
                return record
        return None

    def create(self, payload, *, external_key):
        if self.fail_with:
            raise self.fail_with
        self.creates += 1
        record = dict(payload) | {"id": f"r{len(self.records) + 1}", "external_key": external_key}
        self.records[record["id"]] = record
        return record

    def update(self, record_id, payload):
        if self.fail_with:
            raise self.fail_with
        self.updates += 1
        if record_id in self.records:
            self.records[record_id].update(payload)
        self.fields.update({k: v for k, v in payload.items() if isinstance(v, str)})
        return self.records.get(record_id, dict(payload))


@pytest.fixture
def transcript() -> Transcript:
    return Transcript(
        "call_001",
        [
            segment("s0", "p_rep", "I'll send the security questionnaire by Friday.", 0),
            segment("s1", "p_dana", "Priya approves anything over fifty thousand.", 5_000),
        ],
    )


def claim(transcript, field, value, seg, text, *, confidence=0.9, subject=None):
    start = transcript.segment(seg).text.index(text)
    span = transcript.make_span(seg, start, start + len(text))
    return GroundedClaim(
        field=field,
        value=value,
        spans=(span,),
        confidence=confidence,
        subject_participant_id=subject,
    )


AUTO = FieldPolicy("ai_summary__c", WriteMode.AUTO, measured_precision=0.97)


# ------------------------------------------------------- never overwrite


class TestHumanContentIsNeverReplaced:
    def test_a_human_note_is_suggested_not_clobbered(self, transcript):
        crm = FakeCRM(fields={"next_steps": "Call Dana Thursday. Bring Priya in."})
        result = write_field(
            crm,
            "r1",
            "next_steps",
            "Send the security questionnaire",
            claim(transcript, Field.NEXT_STEP, "Send the questionnaire", "s0", "security questionnaire"),
            FieldPolicy("next_steps", WriteMode.AUTO, measured_precision=0.99),
        )
        assert result.action is WriteAction.SUGGESTED
        assert result.reason == "field contains human-entered content"
        assert result.suggestion == "Send the security questionnaire"
        assert crm.updates == 0

    def test_a_previously_ai_authored_field_may_be_updated(self, transcript):
        crm = FakeCRM(fields={"ai_summary__c": "old summary"}, ai_fields={"ai_summary__c"})
        result = write_field(
            crm,
            "r1",
            "ai_summary__c",
            "new summary",
            claim(transcript, Field.PAIN, "x", "s1", "Priya approves"),
            AUTO,
        )
        assert result.action is WriteAction.WRITTEN
        assert crm.fields["ai_summary__c"] == "new summary"

    def test_a_dedicated_ai_field_cannot_collide(self):
        assert FieldPolicy("ai_next_steps__c", WriteMode.REVIEW).is_dedicated_ai_field
        assert not FieldPolicy("next_steps", WriteMode.REVIEW).is_dedicated_ai_field

    def test_append_keeps_the_human_text_and_marks_its_own(self, transcript):
        crm = FakeCRM(fields={"notes": "Rep's own notes."}, ai_fields={"notes"})
        result = write_field(
            crm,
            "r1",
            "notes",
            "Prospect loses 40 hours a week.",
            claim(transcript, Field.PAIN, "40h/wk", "s1", "Priya approves"),
            FieldPolicy("notes", WriteMode.AUTO, measured_precision=0.96, append=True),
            call_id="call_001",
        )
        assert result.action is WriteAction.APPENDED
        body = crm.fields["notes"]
        assert body.startswith("Rep's own notes.")
        assert "[AI note from call call_001]" in body

    def test_nothing_is_ever_silently_skipped(self, transcript):
        # Every non-writing path returns the suggestion, so the rep sees what
        # the pipeline would have written.
        crm = FakeCRM(fields={"next_steps": "human text"})
        for mode in (WriteMode.REVIEW, WriteMode.SUGGEST_ONLY):
            result = write_field(
                crm,
                "r1",
                "next_steps",
                "proposed",
                claim(transcript, Field.NEXT_STEP, "x", "s0", "security questionnaire"),
                FieldPolicy("next_steps", mode),
            )
            assert result.suggestion == "proposed"


class TestAutoWriteIsEarned:
    def test_auto_without_a_measured_precision_does_not_construct(self):
        # "It seems good" is not a threshold, and here it is not a value.
        with pytest.raises(PolicyError, match="measured_precision"):
            FieldPolicy("ai_summary__c", WriteMode.AUTO)

    def test_auto_below_the_floor_does_not_construct(self):
        with pytest.raises(PolicyError, match="below the auto-write floor"):
            FieldPolicy(
                "ai_summary__c",
                WriteMode.AUTO,
                measured_precision=AUTO_PRECISION_FLOOR - 0.01,
            )

    def test_review_mode_needs_no_number(self):
        assert FieldPolicy("ai_summary__c", WriteMode.REVIEW).measured_precision is None

    def test_a_low_confidence_claim_is_suggested_even_under_auto(self, transcript):
        crm = FakeCRM()
        result = write_field(
            crm,
            "r1",
            "ai_summary__c",
            "value",
            claim(transcript, Field.PAIN, "x", "s1", "Priya approves", confidence=0.4),
            FieldPolicy("ai_summary__c", WriteMode.AUTO, min_confidence=0.8, measured_precision=0.99),
        )
        assert result.action is WriteAction.SUGGESTED
        assert crm.updates == 0

    def test_a_field_requiring_a_span_refuses_without_one(self):
        crm = FakeCRM()
        result = write_field(crm, "r1", "ai_summary__c", "value", None, AUTO)
        assert result.action is WriteAction.SKIPPED
        assert result.suggestion == "value"


# ------------------------------------------------------------ idempotency


class TestIdempotency:
    def test_reprocessing_with_a_better_model_does_not_duplicate(self):
        """The divergence from section 9.4, and why it exists.

        The document's key embeds the transcript and extractor versions, so a
        better model changes the key, misses the lookup, and creates a second
        record -- which is the exact outcome the section says the mechanism
        prevents. A stable key updates instead.
        """
        crm = FakeCRM()
        key = identity_key("call_001", "summary")
        upsert(crm, key, {"body": "v1"}, provenance=Provenance("call_001", 1, "1.0.0"))
        upsert(crm, key, {"body": "v2"}, provenance=Provenance("call_001", 2, "2.0.0"))
        assert crm.creates == 1
        assert crm.updates == 1
        record = crm.find_by_external_key(key)
        assert record["body"] == "v2"
        # The versions are still recorded -- as provenance, where they answer
        # "what produced this", rather than in the key.
        assert record["ai_extractor_version"] == "2.0.0"
        assert record["ai_transcript_version"] == 2

    def test_the_same_commitment_extracted_twice_is_one_task(self):
        assert task_fingerprint("Send the security questionnaire") == task_fingerprint(
            "send the  security questionnaire."
        )

    def test_two_different_commitments_are_two_tasks(self):
        assert task_fingerprint("Send the questionnaire") != task_fingerprint(
            "Send the pricing"
        )

    def test_a_key_needs_a_call_and_a_kind(self):
        with pytest.raises(ValueError):
            identity_key("", "summary")


class TestFailureIsVisible:
    def test_a_failed_write_lands_in_the_journal(self):
        # "A call that processed successfully but silently failed to write is
        # the worst failure mode."
        crm = FakeCRM()
        crm.fail_with = RuntimeError("500 from the CRM")
        journal = WritebackJournal()
        entry = write_payload(crm, journal, "call_001", "summary", {"body": "x"})
        assert not entry.done
        assert "500" in entry.last_error
        assert journal.pending() == (entry,)
        assert not journal.all_landed

    def test_a_successful_write_clears_the_journal(self):
        journal = WritebackJournal()
        write_payload(FakeCRM(), journal, "call_001", "summary", {"body": "x"})
        assert journal.pending() == ()
        assert journal.all_landed

    def test_rate_limits_are_retried_with_backoff(self):
        slept: list[float] = []
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] < 3:
                raise RateLimited(retry_after=0.25)
            return "ok"

        assert with_backoff(flaky, sleep=slept.append) == "ok"
        assert calls["n"] == 3
        assert slept == [0.5, 1.0]  # backs off, never below the base delay

    def test_a_permanent_rate_limit_still_surfaces(self):
        journal = WritebackJournal()
        crm = FakeCRM()
        crm.fail_with = RateLimited(retry_after=0.1)
        entry = write_payload(
            crm, journal, "call_001", "summary", {"body": "x"}, sleep=lambda _: None
        )
        assert entry.failed
        assert entry.attempts == 5
        assert journal.failures() == (entry,)


# ------------------------------------------------------------------ tasks


@pytest.fixture
def call() -> Call:
    return Call(
        call_id="call_001",
        external_id="zoom_1",
        participants=(
            Participant("p_rep", "Sam Rivera", "sam@vendor.example", ParticipantRole.REP),
            Participant("p_dana", "Dana Chen", "dana@acme.example", ParticipantRole.PROSPECT),
        ),
        # A Wednesday.
        scheduled_at=dt.datetime(2026, 9, 16, 15, 0, tzinfo=dt.timezone.utc),
        source_type="zoom_cloud",
    )


class TestTasksAreAlwaysConfirmed:
    def test_the_flag_cannot_be_turned_off(self, transcript):
        span = transcript.make_span("s0", 0, 10)
        with pytest.raises(TaskMustBeConfirmed):
            ProposedTask(
                title="x",
                evidence=(span,),
                confidence=1.0,
                requires_confirmation=False,
            )

    def test_a_task_needs_evidence(self):
        with pytest.raises(ValueError, match="hallucinated"):
            ProposedTask(title="x", evidence=(), confidence=1.0)

    def test_every_proposed_task_requires_confirmation(self, transcript, call):
        claims = (
            claim(
                transcript,
                Field.COMMITMENT,
                "Send the security questionnaire by Friday",
                "s0",
                "send the security questionnaire by Friday",
                subject="p_rep",
            ),
        )
        tasks = propose_tasks(claims, call)
        assert len(tasks) == 1
        assert all(t.requires_confirmation for t in tasks)


class TestTaskGates:
    def test_below_threshold_is_not_proposed(self, transcript, call):
        claims = (
            claim(
                transcript,
                Field.COMMITMENT,
                "Send it",
                "s0",
                "security questionnaire",
                confidence=TASK_THRESHOLD - 0.01,
            ),
        )
        assert propose_tasks(claims, call) == ()

    def test_an_unattributed_transcript_produces_no_tasks(self, transcript, call):
        # A task is a claim about who committed to what, and a transcript with
        # no speakers cannot support one.
        claims = (
            claim(transcript, Field.COMMITMENT, "Send it", "s0", "security questionnaire"),
        )
        assert propose_tasks(claims, call, attributed=False) == ()

    def test_a_non_commitment_field_is_not_a_task(self, transcript, call):
        claims = (claim(transcript, Field.PAIN, "slow", "s1", "Priya approves"),)
        assert propose_tasks(claims, call) == ()

    def test_an_unresolvable_owner_is_left_for_the_rep(self, transcript, call):
        # Guessing "probably the rep" is how a task lands on the wrong list.
        claims = (
            claim(
                transcript,
                Field.COMMITMENT,
                "Send the questionnaire",
                "s0",
                "security questionnaire",
                subject="p_stranger",
            ),
        )
        task = propose_tasks(claims, call)[0]
        assert task.owner_participant_id is None
        assert task.owner_email is None

    def test_the_owner_comes_from_the_roster(self, transcript, call):
        claims = (
            claim(
                transcript,
                Field.COMMITMENT,
                "Send the questionnaire",
                "s0",
                "security questionnaire",
                subject="p_rep",
            ),
        )
        task = propose_tasks(claims, call)[0]
        assert task.owner_email == "sam@vendor.example"


class TestRelativeDatesResolveAgainstTheCall:
    """Section 9.3: against the call date, not the processing date.

    The call is Wednesday 16 September 2026. A call processed the following
    Monday must not produce a task due in the past, which is what happens the
    moment anything in this path reads the clock.
    """

    CALL_DATE = dt.date(2026, 9, 16)

    @pytest.mark.parametrize(
        "phrase,expected",
        [
            ("by tomorrow", dt.date(2026, 9, 17)),
            ("by Friday", dt.date(2026, 9, 18)),
            ("end of the week", dt.date(2026, 9, 18)),
            ("end of next week", dt.date(2026, 9, 25)),
            ("in two weeks", dt.date(2026, 9, 30)),
            ("in 3 days", dt.date(2026, 9, 19)),
            ("end of the month", dt.date(2026, 9, 30)),
            ("end of quarter", dt.date(2026, 9, 30)),
            ("next month", dt.date(2026, 10, 1)),
        ],
    )
    def test_phrases(self, phrase, expected):
        due, note = resolve_due(f"I'll send it {phrase}.", self.CALL_DATE)
        assert due == expected
        assert note is None

    def test_next_weekday_means_the_following_calendar_week(self):
        # Said on a Wednesday, "next Tuesday" is the Tuesday of next week.
        due, _ = resolve_due("next Tuesday", self.CALL_DATE)
        assert due == dt.date(2026, 9, 22)

    def test_next_weekday_is_the_following_monday_to_sunday_week(self):
        # Said on a Monday, "next Tuesday" is eight days away, not one.
        monday = dt.date(2026, 9, 14)
        assert resolve_due("next Tuesday", monday)[0] == dt.date(2026, 9, 22)

        # And the consequence of the same rule, stated rather than hidden: on
        # a Sunday the following week starts tomorrow, so "next Monday" is
        # tomorrow. That is what the words mean on a Sunday.
        sunday = dt.date(2026, 9, 20)
        assert resolve_due("next Monday", sunday)[0] == dt.date(2026, 9, 21)

    def test_a_bare_weekday_is_the_next_one(self):
        due, _ = resolve_due("by Tuesday", self.CALL_DATE)
        assert due == dt.date(2026, 9, 22)

    def test_no_date_phrase_gives_no_date_and_no_note(self):
        assert resolve_due("I'll get that over to you.", self.CALL_DATE) == (None, None)

    def test_nothing_in_this_module_reads_the_clock(self):
        import inspect

        import cis.crm.tasks as module

        source = inspect.getsource(module)
        for forbidden in (
            ".now(",
            ".today(",
            ".utcnow(",
            "time.time(",
            "time.monotonic(",
            "time.localtime(",
            "fromtimestamp(",
        ):
            assert forbidden not in source, forbidden
        assert "import time" not in source

    def test_a_task_due_date_uses_the_call_date(self, transcript, call):
        claims = (
            claim(
                transcript,
                Field.COMMITMENT,
                "Send the security questionnaire by Friday",
                "s0",
                "security questionnaire by Friday",
                subject="p_rep",
            ),
        )
        task = propose_tasks(claims, call)[0]
        assert task.due == dt.date(2026, 9, 18)


class TestDatePhrasesThatUsedToBeWrong:
    CALL_DATE = dt.date(2026, 9, 16)  # a Wednesday

    def test_end_of_next_month_is_the_end_not_the_first(self):
        # "end of next month" contains "next month". Taking the shorter
        # alternative returns 1 October for a phrase that means 31 October --
        # thirty days early, silently, with no note.
        assert resolve_due("by the end of next month", self.CALL_DATE)[0] == dt.date(
            2026, 10, 31
        )

    def test_end_of_next_quarter_does_not_overflow_the_month(self):
        assert resolve_due("end of next quarter", dt.date(2026, 11, 3))[0] == dt.date(
            2027, 3, 31
        )

    def test_number_words_past_four(self):
        assert resolve_due("in five days", self.CALL_DATE)[0] == dt.date(2026, 9, 21)
        assert resolve_due("in twelve weeks", self.CALL_DATE)[0] == dt.date(2026, 12, 9)

    @pytest.mark.parametrize(
        "phrase",
        [
            "a week from Thursday",
            "by the 15th",
            "late next month",
            "early next week",
            "in a few weeks",
            "by September",
        ],
    )
    def test_a_date_like_phrase_we_cannot_resolve_produces_a_note(self, phrase):
        # Not a silent None. "No date was mentioned" and "a date was mentioned
        # and I could not work it out" are different states, and the rep needs
        # to be able to tell them apart.
        due, note = resolve_due(f"I'll get it over {phrase}.", self.CALL_DATE)
        assert due is None
        assert note and phrase.lower().lstrip("by ") in note.lower()

    def test_a_weekend_call_does_not_land_next_week_two_weeks_out(self):
        saturday = dt.date(2026, 9, 19)
        assert resolve_due("end of next week", saturday)[0] == dt.date(2026, 9, 25)

    def test_by_friday_said_on_a_friday_means_today(self):
        friday = dt.date(2026, 9, 18)
        assert resolve_due("I'll send it by Friday", friday)[0] == friday

    def test_the_note_reaches_the_task(self, transcript, call):
        claims = (
            claim(
                transcript,
                Field.COMMITMENT,
                "Send the security questionnaire a week from Thursday",
                "s0",
                "security questionnaire",
                subject="p_rep",
            ),
        )
        task = propose_tasks(claims, call)[0]
        assert task.due is None
        assert "could not resolve" in task.due_note


class TestAppendIsTheOneModeThatMayTouchHumanText:
    def test_append_adds_below_a_humans_note_without_replacing_it(self, transcript):
        # Section 9.1's second pattern exists for exactly this situation, and
        # a human-content check placed before it would make it unreachable.
        crm = FakeCRM(fields={"notes": "Rep's own notes."})
        result = write_field(
            crm,
            "r1",
            "notes",
            "Prospect loses 40 hours a week.",
            claim(transcript, Field.PAIN, "40h/wk", "s1", "Priya approves"),
            FieldPolicy("notes", WriteMode.AUTO, measured_precision=0.96, append=True),
            call_id="call_001",
        )
        assert result.action is WriteAction.APPENDED
        assert crm.fields["notes"].startswith("Rep's own notes.")
        assert "Prospect loses 40 hours a week." in crm.fields["notes"]

    def test_a_dedicated_ai_field_needs_no_provenance_api(self, transcript):
        # A CRM that cannot say who authored a field can still auto-write
        # AI_Summary__c, because nothing else ever writes there.
        crm = FakeCRM(fields={"ai_summary__c": "previous run"})
        assert not crm.is_ai_authored("r1", "ai_summary__c")
        result = write_field(
            crm,
            "r1",
            "ai_summary__c",
            "new summary",
            claim(transcript, Field.PAIN, "x", "s1", "Priya approves"),
            AUTO,
        )
        assert result.action is WriteAction.WRITTEN


class TestKeysAreInjective:
    def test_a_colon_in_a_component_is_refused(self):
        # Without this, identity_key("a:b", "task") and identity_key("a",
        # "b:task") name the same record.
        with pytest.raises(ValueError, match="separator"):
            identity_key("a:b", "task")
        with pytest.raises(ValueError, match="separator"):
            identity_key("a", "b:task")
