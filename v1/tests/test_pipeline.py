"""The pipeline end to end (design.md sections 13.2, 10.2, 5.3).

The single most important assertion in this file is the first one: the
platform client is not touched when consent says no. Everything else in the
project is a quality decision; that one is the legal control.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from cis.asr.vocabulary import VocabularyHints, vocabulary_hints
from cis.consent.model import (
    Call,
    ConsentLedger,
    ConsentMethod,
    ConsentRecord,
    Participant,
    ParticipantRole,
)
from cis.extract.grounded import Field
from cis.ingest.tracks import AudioTrack, Attribution
from cis.score.framework import load_framework
from cis.transcript.model import Segment, Transcript
from cis.workflows.pipeline import Outcome, Stage, run

from .conftest import segment

FRAMEWORKS = Path(__file__).resolve().parents[1] / "docs" / "frameworks"
CALL_TIME = dt.datetime(2026, 9, 16, 15, 0, tzinfo=dt.timezone.utc)


@pytest.fixture
def call() -> Call:
    return Call(
        call_id="call_001",
        external_id="zoom_1",
        participants=(
            Participant("p_rep", "Sam Rivera", "sam@vendor.example", ParticipantRole.REP),
            Participant("p_dana", "Dana Chen", "dana@acme.example", ParticipantRole.PROSPECT),
        ),
        scheduled_at=CALL_TIME,
        source_type="zoom_cloud",
        channels=2,
    )


@pytest.fixture
def ledger(call) -> ConsentLedger:
    led = ConsentLedger()
    for participant in call.participants:
        led.append(
            ConsentRecord(
                call_id=call.call_id,
                participant_id=participant.participant_id,
                method=ConsentMethod.PLATFORM_CONSENT,
                evidence_ref=f"zoom_{participant.participant_id}",
                recorded_at=CALL_TIME,
            )
        )
    return led


TRANSCRIPT_LINES = [
    # First name in the body on purpose: a transcript says "Dana", never
    # "Dana Chen", and that is what the name redaction has to catch.
    ("s0", "p_rep", "Dana, how are you handling this at the moment?"),
    (
        "s1",
        "p_dana",
        "Badly. We lose about forty hours a week to it, and it's getting worse.",
    ),
    (
        "s2",
        "p_dana",
        "Priya Raman on the finance side signs off on anything over fifty thousand. "
        "You can reach her on priya@acme.example.",
    ),
    ("s3", "p_rep", "I'll send the security questionnaire by Friday."),
]


class FakePlatform:
    def __init__(self, tracks=None, mixed=None):
        self._tracks = tracks
        self._mixed = mixed
        self.fetches = 0

    def per_speaker_tracks(self, external_id):
        self.fetches += 1
        if self._tracks is None:
            return []
        return self._tracks

    def mixed_track(self, external_id):
        self.fetches += 1
        return self._mixed


def tracks_for(call) -> list[AudioTrack]:
    return [
        AudioTrack(p.participant_id, f"s3://audio/{p.participant_id}", Attribution.PLATFORM_TRACK)
        for p in call.participants
    ]


class FakeTranscriber:
    def __init__(self, lines=None, *, drop_word_timings=False):
        self.lines = lines or TRANSCRIPT_LINES
        self.drop_word_timings = drop_word_timings
        self.hints: VocabularyHints | None = None

    def transcribe(self, tracks, hints):
        self.hints = hints
        segments = []
        for index, (seg_id, participant, text) in enumerate(self.lines):
            built = segment(seg_id, participant, text, index * 6_000)
            if self.drop_word_timings:
                built = Segment(
                    segment_id=built.segment_id,
                    participant_id=built.participant_id,
                    text=built.text,
                    start_ms=built.start_ms,
                    end_ms=built.end_ms,
                    words=(),
                    attribution=built.attribution,
                )
            segments.append(built)
        return Transcript(
            "call_001",
            segments,
            asr_engine="whisper",
            asr_model="large-v3",
            vocabulary_hash=hints.hash,
        )


class RecordingModel:
    """Returns quotes copied from what it was shown, and records the input.

    Copying from the input is what a well-behaved model does, and it is also
    how this fake proves the redaction round trip: if the pipeline redacted on
    the way out, the quote it hands back contains a placeholder, and the span
    it ends up with must still quote the real transcript.
    """

    def __init__(self):
        self.seen: list[str] = []

    def extract(self, chunk_text, fields):
        self.seen.append(chunk_text)
        out = {}
        for line in chunk_text.split("\n"):
            body = line.split("] ", 1)[-1]
            if "forty hours a week" in body:
                out["pain"] = {
                    "value": "losing 40 hours a week",
                    "quotes": ["We lose about forty hours a week to it"],
                    "confidence": 0.9,
                    "subject_participant_id": "p_dana",
                }
            if "signs off" in body:
                # The whole line, including the email address -- so the quote
                # that comes back carries a placeholder and `restore` is
                # actually exercised.
                out["economic_buyer"] = {
                    "value": "Priya Raman",
                    "quotes": [body.strip()],
                    "confidence": 0.88,
                    "subject_participant_id": "p_dana",
                }
            if "security questionnaire" in body:
                out["commitment"] = {
                    "value": "Send the security questionnaire by Friday",
                    "quotes": [body.strip()],
                    "confidence": 0.92,
                    "subject_participant_id": "p_rep",
                }
        return out


class LevelTwoJudge:
    def choose_level(self, prompt):
        return {"level": 2, "rationale": "scripted"}


@pytest.fixture
def framework():
    return load_framework(FRAMEWORKS / "meddicc.yaml")


def run_pipeline(call, ledger, framework, **kwargs):
    client = kwargs.pop("client", None) or FakePlatform(tracks=tracks_for(call))
    transcriber = kwargs.pop("transcriber", None) or FakeTranscriber()
    model = kwargs.pop("model", None) or RecordingModel()
    result = run(
        call,
        ledger,
        client,
        transcriber,
        model,
        LevelTwoJudge(),
        framework,
        now=CALL_TIME,
        **kwargs,
    )
    return result, client, transcriber, model


# ------------------------------------------------------------- the gate


class TestTheGateRunsFirst:
    def test_no_consent_means_the_platform_is_never_called(self, call, framework):
        """The legal control, asserted as a call count.

        Section 3.2: "If you have downloaded the recording, you have already
        arguably intercepted it. Fetching is the action to gate." So the test
        is not that processing stopped -- it is that the fetch never happened.
        """
        client = FakePlatform(tracks=tracks_for(call))
        result, client, _, _ = run_pipeline(
            call, ConsentLedger(), framework, client=client
        )
        assert result.outcome is Outcome.SKIPPED
        assert result.failed_stage is Stage.CONSENT
        assert client.fetches == 0

    def test_one_participant_short_is_enough_to_stop(self, call, ledger, framework):
        partial = ConsentLedger(
            [r for r in ledger.all() if r.participant_id != "p_dana"]
        )
        client = FakePlatform(tracks=tracks_for(call))
        result, client, _, _ = run_pipeline(call, partial, framework, client=client)
        assert result.outcome is Outcome.SKIPPED
        assert client.fetches == 0

    def test_a_skipped_call_is_not_a_failure(self, call, framework):
        result, _, _, _ = run_pipeline(call, ConsentLedger(), framework)
        assert result.outcome is not Outcome.FAILED
        assert result.reason


# --------------------------------------------------------- the happy path


class TestEndToEnd:
    def test_a_consented_call_produces_grounded_claims_and_a_score(
        self, call, ledger, framework
    ):
        result, _, _, _ = run_pipeline(call, ledger, framework)
        assert result.ok
        assert result.extraction is not None
        assert result.score is not None

        fields = {c.field for c in result.extraction.claims}
        assert Field.PAIN in fields
        assert Field.ECONOMIC_BUYER in fields

        # Every claim still quotes the real transcript.
        for claim in result.extraction.claims:
            for span in claim.spans:
                result.transcript.validate_span(span)

    def test_the_score_carries_the_framework_version(self, call, ledger, framework):
        result, _, _, _ = run_pipeline(call, ledger, framework)
        assert result.score.framework_id == "MEDDICC@3"

    def test_judgment_criteria_are_staged_for_review(self, call, ledger, framework):
        result, _, _, _ = run_pipeline(call, ledger, framework)
        keys = {s.criterion_key for s in result.score.needs_review}
        assert keys == {"decision_criteria", "identify_pain", "champion"}

    def test_a_commitment_becomes_a_task_awaiting_confirmation(
        self, call, ledger, framework
    ):
        result, _, _, _ = run_pipeline(call, ledger, framework)
        assert len(result.tasks) == 1
        task = result.tasks[0]
        assert task.requires_confirmation
        assert task.owner_email == "sam@vendor.example"
        # Resolved against the call date (Wednesday 16 September), not today.
        assert task.due == dt.date(2026, 9, 18)

    def test_the_vocabulary_hints_reach_the_recogniser(self, call, ledger, framework):
        result, _, transcriber, _ = run_pipeline(call, ledger, framework)
        assert "Dana Chen" in transcriber.hints.terms
        assert result.transcript.vocabulary_hash == transcriber.hints.hash


class TestRedactionCrossesTheModelBoundary:
    def test_the_model_never_sees_the_email_address(self, call, ledger, framework):
        result, _, _, model = run_pipeline(call, ledger, framework)
        joined = "\n".join(model.seen)
        assert "priya@acme.example" not in joined
        assert "[EMAIL_1]" in joined

    def test_roster_first_names_are_pseudonymised(self, call, ledger, framework):
        """Transcripts say "Dana", not "Dana Chen".

        Matching only the full display name makes the whole name defence
        inert on real transcripts, which is what an earlier version of this
        test failed to notice: it asserted that "Dana Chen" -- a string that
        appears nowhere in the fixture -- was absent.
        """
        result, _, _, model = run_pipeline(call, ledger, framework)
        joined = "\n".join(model.seen)
        assert "Dana" not in joined
        assert "[PERSON_1]" in joined
        # Consistent replacement, not removal: the same person is the same
        # placeholder everywhere.
        assert joined.count("[PERSON_1]") >= 1

    def test_the_judge_is_a_model_too_and_is_redacted(
        self, call, ledger, framework
    ):
        # The evidence block quotes the transcript, and a quote that names who
        # signs off carries an email address along with it.
        class CapturingJudge:
            def __init__(self):
                self.prompts = []

            def choose_level(self, prompt):
                self.prompts.append(prompt)
                return {"level": 2, "rationale": "scripted"}

        judge = CapturingJudge()
        sample_client = FakePlatform(tracks=tracks_for(call))
        run(
            call,
            ledger,
            sample_client,
            FakeTranscriber(),
            RecordingModel(),
            judge,
            framework,
            now=CALL_TIME,
        )
        prompts = "\n".join(judge.prompts)
        assert prompts
        assert "priya@acme.example" not in prompts
        assert "Dana" not in prompts

    def test_the_spans_still_quote_the_real_transcript(self, call, ledger, framework):
        """Both requirements at once, which is the whole point of the design.

        The model saw a redacted chunk and quoted the placeholder back. The
        stored span quotes the words that were actually said, and validates
        against the transcript.
        """
        result, _, _, model = run_pipeline(call, ledger, framework)
        buyer = [c for c in result.extraction.claims if c.field is Field.ECONOMIC_BUYER]
        assert buyer
        # The model was shown a placeholder and quoted it back, so `restore`
        # is what turned that quote into something locatable. Without this
        # assertion the round trip is never exercised.
        assert "[EMAIL_1]" in "\n".join(model.seen)
        assert "priya@acme.example" in buyer[0].quote
        assert "[EMAIL_" not in buyer[0].quote
        assert "[PERSON_" not in buyer[0].quote
        result.transcript.validate_span(buyer[0].spans[0])

    def test_the_count_of_redactions_is_reported(self, call, ledger, framework):
        result, _, _, _ = run_pipeline(call, ledger, framework)
        assert result.redactions > 0

    def test_redaction_can_be_turned_off_for_a_self_hosted_model(
        self, call, ledger, framework
    ):
        result, _, _, model = run_pipeline(
            call, ledger, framework, redact_before_model=False
        )
        assert "priya@acme.example" in "\n".join(model.seen)
        assert result.ok


# ------------------------------------------------------------- refusals


class TestRefusals:
    def test_mixed_audio_stops_before_transcription(self, call, ledger, framework):
        mixed = AudioTrack(None, "s3://audio/mixed", Attribution.NONE, channels=1)
        client = FakePlatform(tracks=[], mixed=mixed)
        transcriber = FakeTranscriber()
        result, _, transcriber, _ = run_pipeline(
            call, ledger, framework, client=client, transcriber=transcriber
        )
        assert result.outcome is Outcome.SKIPPED
        assert result.failed_stage is Stage.INGEST
        assert "diarization is disabled by policy" in result.reason.lower()
        assert transcriber.hints is None

    def test_unattributed_is_allowed_deliberately_and_produces_no_tasks(
        self, call, ledger, framework
    ):
        mixed = AudioTrack(None, "s3://audio/mixed", Attribution.NONE, channels=1)
        client = FakePlatform(tracks=[], mixed=mixed)
        result, _, _, _ = run_pipeline(
            call, ledger, framework, client=client, require_attribution=False
        )
        assert result.ok
        assert result.tasks == ()
        fields = {c.field for c in result.extraction.claims}
        assert Field.COMMITMENT not in fields
        assert Field.ECONOMIC_BUYER not in fields
        assert Field.PAIN in fields, "topic extraction still works"
        assert any("speaker" in w for w in result.warnings)

    def test_a_transcript_without_word_timings_is_refused(
        self, call, ledger, framework
    ):
        result, _, _, _ = run_pipeline(
            call,
            ledger,
            framework,
            transcriber=FakeTranscriber(drop_word_timings=True),
        )
        assert result.outcome is Outcome.FAILED
        assert result.failed_stage is Stage.TRANSCRIBE
        assert "MissingWordTimings" in result.reason

    def test_a_failure_names_its_stage(self, call, ledger, framework):
        class Exploding:
            def extract(self, chunk_text, fields):
                raise RuntimeError("model unavailable")

        result, _, _, _ = run_pipeline(call, ledger, framework, model=Exploding())
        assert result.outcome is Outcome.FAILED
        assert result.failed_stage is Stage.EXTRACT
        assert "model unavailable" in result.reason


class TestHonestReporting:
    def test_a_hallucinated_quote_is_surfaced_as_a_warning(
        self, call, ledger, framework
    ):
        class Fabricating:
            def extract(self, chunk_text, fields):
                return {
                    "champion": {
                        "value": "Dana",
                        "quotes": ["I will personally push this through the board"],
                        "confidence": 0.9,
                    }
                }

        result, _, _, _ = run_pipeline(call, ledger, framework, model=Fabricating())
        assert result.ok
        assert result.extraction.claims == ()
        assert result.stats.hallucination_rate == 1.0
        assert any("hallucination" in w for w in result.warnings)

    def test_a_contradiction_is_surfaced_not_collapsed(self, call, ledger, framework):
        lines = [
            ("s0", "p_dana", "The budget is about fifty thousand for this."),
            ("s1", "p_dana", "Actually it's eighty thousand now that legal is involved."),
        ]

        class BudgetModel:
            def extract(self, chunk_text, fields):
                if "fifty thousand" in chunk_text:
                    return {
                        "budget": {
                            "value": "$50k",
                            "quotes": ["The budget is about fifty thousand for this."],
                            "confidence": 0.8,
                        }
                    }
                return {
                    "budget": {
                        "value": "$80k",
                        "quotes": ["Actually it's eighty thousand now that legal is involved."],
                        "confidence": 0.85,
                    }
                }

        result, _, _, _ = run_pipeline(
            call,
            ledger,
            framework,
            transcriber=FakeTranscriber(lines=lines),
            model=BudgetModel(),
            fields=[Field.BUDGET],
            # One turn per chunk: the contradiction is between chunks, which
            # is where reconciliation has to find it.
            target_tokens=12,
            overlap_turns=0,
        )
        # MEDDICC has no budget criterion, so nothing scores it -- but the
        # contradiction still reaches the rep, which is the point of 6.2.
        assert result.ok
        assert any("contradiction" in w for w in result.warnings)


class TestVocabularyHints:
    def test_participants_come_first_because_truncation_is_real(self, call):
        hints = vocabulary_hints(
            call, competitors=["Salesforce"] * 1, max_hints=3
        )
        assert hints.terms[0] == "Sam Rivera"
        assert hints.truncated > 0
        assert "Salesforce" not in hints.terms

    def test_the_hash_is_order_independent(self, call):
        a = VocabularyHints(("Acme", "Dana Chen"))
        b = VocabularyHints(("Dana Chen", "Acme"))
        assert a.hash == b.hash

    def test_stopwords_do_not_consume_a_slot(self):
        call = Call(
            call_id="c",
            external_id="x",
            participants=(Participant("p1", "The Acme Corp"),),
            scheduled_at=CALL_TIME,
            source_type="zoom_cloud",
        )
        hints = vocabulary_hints(call)
        assert "the" not in {t.lower() for t in hints.terms}
        assert "Acme" in hints.terms
