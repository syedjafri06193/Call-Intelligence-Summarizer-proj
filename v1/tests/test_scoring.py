"""Scoring against a framework (design.md section 7).

Most of what is tested here is a bias mitigation, and each one is tested as a
structural property rather than as a behaviour of the model:

* verbosity -- length is not an input, so a longer call cannot score higher
* sycophancy -- there is no parameter through which a prior score can arrive
* leniency drift -- the anchors and the evidence requirement are in the prompt
* self-inconsistency -- temperature is fixed, and the variance is measured
  separately in eval/consistency.py

A test that asserted "the model did not exhibit verbosity bias" would be
testing the model. These assert that the code could not exhibit it whatever
model is behind the boundary, which is the only version that keeps holding.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from cis.consent.model import Call
from cis.extract.grounded import Field, GroundedClaim
from cis.extract.reconcile import Extraction
from cis.score.anchors import (
    NO_EVIDENCE_RATIONALE,
    CallScore,
    InvalidAnchorLevel,
    build_prompt,
    gather_evidence,
    score_call,
    score_criterion,
)
from cis.score.framework import (
    CriterionKind,
    Framework,
    FrameworkError,
    load_framework,
)
from cis.transcript.model import Transcript

from .conftest import NOW, segment

FRAMEWORKS = Path(__file__).resolve().parents[1] / "docs" / "frameworks"


class ScriptedJudge:
    """Returns what it is told, and records what it was shown.

    The prompts it captures are the actual subject of most of these tests.
    """

    def __init__(self, answers: dict[str, dict] | None = None, default: dict | None = None):
        self.answers = answers or {}
        self.default = (
            {"level": 2, "rationale": "scripted"} if default is None else default
        )
        self.prompts: list[str] = []

    def choose_level(self, prompt: str) -> dict:
        self.prompts.append(prompt)
        for key, answer in self.answers.items():
            if f"CRITERION: {key}" in prompt:
                return answer
        return self.default


@pytest.fixture
def meddicc() -> Framework:
    return load_framework(FRAMEWORKS / "meddicc.yaml")


@pytest.fixture
def bant() -> Framework:
    return load_framework(FRAMEWORKS / "bant.yaml")


@pytest.fixture
def transcript() -> Transcript:
    return Transcript(
        "call_001",
        [
            segment("s0", "p_rep", "What does this cost you today?", 0),
            segment(
                "s1",
                "p_dana",
                "We're losing about forty hours a week across three people.",
                4_000,
            ),
            segment(
                "s2",
                "p_rep",
                "Most customers see a three hundred percent return on this.",
                10_000,
            ),
            segment(
                "s3",
                "p_dana",
                "Priya on the finance side signs off on anything over fifty "
                "thousand.",
                16_000,
            ),
        ],
    )


def claim(field: Field, value: str, transcript: Transcript, seg: str, text: str, **kw):
    start = transcript.segment(seg).text.index(text)
    span = transcript.make_span(seg, start, start + len(text))
    return GroundedClaim(field=field, value=value, spans=(span,), confidence=0.9, **kw)


@pytest.fixture
def extraction(transcript) -> Extraction:
    return Extraction(
        call_id="call_001",
        transcript_version=transcript.version,
        claims=(
            claim(Field.METRIC, "40 hours a week", transcript, "s1", "forty hours a week"),
            claim(
                Field.METRIC,
                "300% return",
                transcript,
                "s2",
                "three hundred percent return",
            ),
            claim(
                Field.ECONOMIC_BUYER,
                "Priya",
                transcript,
                "s3",
                "Priya on the finance side signs off",
            ),
        ),
        contradictions=(),
        extractor_version="1.0.0",
    )


# ------------------------------------------------------- loading the file


class TestFrameworkIsData:
    def test_meddicc_loads(self, meddicc):
        assert meddicc.framework_id == "MEDDICC@3"
        assert "metrics" in meddicc.keys
        assert "champion" in meddicc.keys
        assert len(meddicc.criteria) == 7

    def test_a_second_framework_needs_no_code(self, bant):
        # The point of section 7.1 is that the framework is data. If adding
        # BANT required touching src/, the encoding did not work.
        assert bant.framework_id == "BANT@1"
        assert len(bant.criteria) == 4

    def test_every_criterion_declares_which_fields_feed_it(self, meddicc):
        for criterion in meddicc.criteria:
            assert criterion.fields, criterion.key

    def test_the_field_link_is_declared_not_inferred(self, meddicc):
        # Section 7.2's sketch does `criterion.key in c.field`, which works
        # only while the two vocabularies line up. These two already do not.
        assert meddicc["identify_pain"].fields == (Field.PAIN,)
        assert "identify_pain" not in Field.PAIN.value

    def test_factual_and_judgment_are_split(self, meddicc):
        assert meddicc["economic_buyer"].kind is CriterionKind.FACTUAL
        assert meddicc["champion"].kind is CriterionKind.JUDGMENT
        keys = {c.key for c in meddicc.factual} | {c.key for c in meddicc.judgment}
        assert keys == set(meddicc.keys)

    def test_champion_carries_the_warning_from_the_evaluation_table(self, meddicc):
        # Section 8.2's Champion row: the criterion is what needs work, not
        # the model. Worth saying where someone will read it.
        assert meddicc["champion"].warning
        assert "definition" in meddicc["champion"].warning


class TestLoaderRejectsAmbiguity:
    def write(self, tmp_path: Path, body: str) -> Path:
        path = tmp_path / "f.yaml"
        path.write_text(body)
        return path

    BASE = """
version: 1
name: T
criteria:
  - key: k
    label: K
    kind: factual
    fields: [pain]
    definition: d
    evidence_requires: e
    anchors:
      0: not discussed
      1: some
"""

    def test_a_string_version_is_refused(self, tmp_path):
        body = self.BASE.replace("version: 1", 'version: "1"')
        with pytest.raises(FrameworkError, match="version"):
            load_framework(self.write(tmp_path, body))

    def test_a_gap_in_the_scale_is_refused(self, tmp_path):
        body = self.BASE.replace("      1: some", "      2: some")
        with pytest.raises(FrameworkError, match="contiguously"):
            load_framework(self.write(tmp_path, body))

    def test_anchor_zero_must_mean_absence(self, tmp_path):
        body = self.BASE.replace("      0: not discussed", "      0: weak")
        with pytest.raises(FrameworkError, match="anchor 0"):
            load_framework(self.write(tmp_path, body))

    def test_an_unknown_extraction_field_is_refused(self, tmp_path):
        body = self.BASE.replace("fields: [pain]", "fields: [vibes]")
        with pytest.raises(FrameworkError, match="not an extraction field"):
            load_framework(self.write(tmp_path, body))

    def test_no_fields_is_refused_rather_than_scoring_zero_forever(self, tmp_path):
        body = self.BASE.replace("fields: [pain]", "fields: []")
        with pytest.raises(FrameworkError, match="non-empty"):
            load_framework(self.write(tmp_path, body))

    def test_kind_has_no_default(self, tmp_path):
        # It decides what the CRM may write without a human. A default here
        # would silently pick one.
        body = self.BASE.replace("    kind: factual\n", "")
        with pytest.raises(FrameworkError, match="kind"):
            load_framework(self.write(tmp_path, body))

    def test_duplicate_keys_are_refused(self, tmp_path):
        body = self.BASE + self.BASE.split("criteria:")[1]
        with pytest.raises(FrameworkError, match="duplicate"):
            load_framework(self.write(tmp_path, body))

    def test_a_single_level_scale_is_refused(self, tmp_path):
        body = self.BASE.replace("      1: some\n", "")
        with pytest.raises(FrameworkError, match="at least two"):
            load_framework(self.write(tmp_path, body))

    def test_a_missing_file_fails_at_load_not_at_first_call(self, tmp_path):
        with pytest.raises(FrameworkError, match="no framework file"):
            load_framework(tmp_path / "absent.yaml")


# ------------------------------------------------------------ no evidence


class TestLevelZeroBelongsToCode:
    def test_no_evidence_means_zero_without_asking_a_model(
        self, meddicc, extraction, transcript, call
    ):
        judge = ScriptedJudge()
        evidence = gather_evidence(
            meddicc["competition"], extraction.claims, transcript, call
        )
        score = score_criterion(
            meddicc["competition"], evidence, judge, framework_id=meddicc.framework_id
        )
        assert score.level == 0
        assert score.rationale == NO_EVIDENCE_RATIONALE
        assert score.spans == ()
        assert judge.prompts == [], "asking a model to confirm an absence invites one"

    def test_a_judge_cannot_return_zero_when_evidence_exists(
        self, meddicc, extraction, transcript, call
    ):
        judge = ScriptedJudge(default={"level": 0, "rationale": "weak"})
        evidence = gather_evidence(
            meddicc["economic_buyer"], extraction.claims, transcript, call
        )
        with pytest.raises(InvalidAnchorLevel, match="not discussed"):
            score_criterion(
                meddicc["economic_buyer"],
                evidence,
                judge,
                framework_id=meddicc.framework_id,
            )

    def test_building_a_prompt_with_no_evidence_is_a_bug(self, meddicc):
        from cis.score.anchors import Evidence

        with pytest.raises(ValueError, match="assigned by code"):
            build_prompt(meddicc["champion"], Evidence("champion", ()))


# -------------------------------------------------- whose evidence counts


class TestProspectStatedOnly:
    def test_vendor_asserted_roi_does_not_count(
        self, meddicc, extraction, transcript, call
    ):
        evidence = gather_evidence(
            meddicc["metrics"], extraction.claims, transcript, call
        )
        values = [item.claim.value for item in evidence.items]
        assert values == ["40 hours a week"]
        excluded = [item.claim.value for item, _ in evidence.excluded]
        assert excluded == ["300% return"]

    def test_the_exclusion_reason_is_recorded_not_just_the_exclusion(
        self, meddicc, extraction, transcript, call
    ):
        evidence = gather_evidence(
            meddicc["metrics"], extraction.claims, transcript, call
        )
        _, reason = evidence.excluded[0]
        assert "rep" in reason

    def test_an_unknown_speaker_is_excluded_too(self, meddicc, transcript):
        # Default deny: "we cannot tell who said this" does not establish that
        # the prospect said it.
        anonymous = Transcript(
            "call_001",
            [segment("s0", None, "We lose forty hours a week.", 0, attribution="none")],
        )
        c = claim(Field.METRIC, "40 hours", anonymous, "s0", "forty hours a week")
        empty_call = Call(
            call_id="call_001",
            external_id="x",
            participants=(),
            scheduled_at=NOW,
            source_type="zoom_cloud",
        )
        evidence = gather_evidence(meddicc["metrics"], (c,), anonymous, empty_call)
        assert evidence.items == ()
        assert "could not be established" in evidence.excluded[0][1]

    def test_an_exclusion_is_visible_in_the_score(
        self, meddicc, transcript, call
    ):
        # "We heard a number but the rep said it" is a different state from
        # "nobody mentioned a number", and the rep needs to tell them apart.
        rep_only = (
            claim(
                Field.METRIC,
                "300% return",
                transcript,
                "s2",
                "three hundred percent return",
            ),
        )
        evidence = gather_evidence(meddicc["metrics"], rep_only, transcript, call)
        score = score_criterion(
            meddicc["metrics"],
            evidence,
            ScriptedJudge(),
            framework_id=meddicc.framework_id,
        )
        assert score.level == 0
        assert "ruled out" in score.rationale
        assert score.excluded

    def test_a_criterion_without_the_flag_takes_either_speaker(
        self, meddicc, extraction, transcript, call
    ):
        evidence = gather_evidence(
            meddicc["economic_buyer"], extraction.claims, transcript, call
        )
        assert len(evidence.items) == 1


# ------------------------------------------------------- bias mitigations


class TestVerbosityBias:
    def test_length_is_not_an_input(self, meddicc, extraction, transcript, call):
        """Section 7.4: "Score from extracted claims, not raw length."

        Same claims, two transcripts of very different length. The prompt is
        byte-identical, so the score cannot differ.
        """
        padded = Transcript(
            "call_001",
            transcript.segments
            + tuple(
                segment(f"pad{i}", "p_dana", "And another thing entirely. " * 20, 30_000 + i)
                for i in range(40)
            ),
        )
        short = gather_evidence(
            meddicc["economic_buyer"], extraction.claims, transcript, call
        )
        long = gather_evidence(
            meddicc["economic_buyer"], extraction.claims, padded, call
        )
        assert build_prompt(meddicc["economic_buyer"], short) == build_prompt(
            meddicc["economic_buyer"], long
        )

    def test_only_evidence_gathering_receives_a_transcript(self):
        # `gather_evidence` reads one, and only to map a span's segment to the
        # participant who spoke it. Nothing on the judge's side of the
        # boundary can see one.
        assert "transcript" not in inspect.signature(score_criterion).parameters
        assert "transcript" not in inspect.signature(build_prompt).parameters
        assert "transcript" in inspect.signature(gather_evidence).parameters


class TestSycophancy:
    def test_there_is_no_parameter_a_prior_score_could_arrive_through(self):
        """Section 7.4: "Never include a prior score in the prompt."

        Enforced by the signature rather than by a convention, because a
        convention survives exactly as long as nobody is in a hurry.
        """
        forbidden = {"previous", "prior", "current_score", "suggested", "hint"}
        for fn in (build_prompt, score_criterion, score_call):
            names = set(inspect.signature(fn).parameters)
            assert not (names & forbidden), fn.__name__

    def test_the_prompt_is_identical_on_a_rescore(
        self, meddicc, extraction, transcript, call
    ):
        judge = ScriptedJudge()
        score_call(extraction, meddicc, judge, transcript, call)
        first = list(judge.prompts)
        judge.prompts.clear()
        score_call(extraction, meddicc, judge, transcript, call)
        assert judge.prompts == first


class TestLeniencyDrift:
    def test_the_anchors_and_the_evidence_bar_are_in_the_prompt(
        self, meddicc, extraction, transcript, call
    ):
        criterion = meddicc["economic_buyer"]
        evidence = gather_evidence(criterion, extraction.claims, transcript, call)
        prompt = build_prompt(criterion, evidence)
        assert criterion.evidence_requires in prompt
        for text in criterion.anchors.values():
            assert text in prompt

    def test_the_prompt_never_asks_for_a_number_out_of_ten(
        self, meddicc, extraction, transcript, call
    ):
        criterion = meddicc["metrics"]
        evidence = gather_evidence(criterion, extraction.claims, transcript, call)
        prompt = build_prompt(criterion, evidence).lower()
        assert "out of 10" not in prompt
        assert "out of ten" not in prompt
        assert "rate" not in prompt

    def test_the_judge_is_told_zero_is_off_the_table(
        self, meddicc, extraction, transcript, call
    ):
        criterion = meddicc["economic_buyer"]
        evidence = gather_evidence(criterion, extraction.claims, transcript, call)
        prompt = build_prompt(criterion, evidence)
        assert "already been ruled out" in prompt
        assert "Choose from 1, 2, 3" in prompt


class TestSelfInconsistency:
    def test_temperature_is_pinned_and_recorded(
        self, meddicc, extraction, transcript, call
    ):
        result = score_call(extraction, meddicc, ScriptedJudge(), transcript, call)
        assert result.temperature == 0.0


# ------------------------------------------------------- what comes out


class TestJudgeOutputIsValidated:
    def _evidence(self, meddicc, extraction, transcript, call):
        return gather_evidence(
            meddicc["economic_buyer"], extraction.claims, transcript, call
        )

    @pytest.mark.parametrize("bad", [{"level": 9}, {"level": -1}])
    def test_a_level_off_the_scale_raises(
        self, meddicc, extraction, transcript, call, bad
    ):
        with pytest.raises(InvalidAnchorLevel, match="not on"):
            score_criterion(
                meddicc["economic_buyer"],
                self._evidence(meddicc, extraction, transcript, call),
                ScriptedJudge(default=bad),
                framework_id=meddicc.framework_id,
            )

    @pytest.mark.parametrize("bad", [{}, {"level": "high"}, {"level": None}])
    def test_a_non_level_answer_raises(
        self, meddicc, extraction, transcript, call, bad
    ):
        with pytest.raises(InvalidAnchorLevel, match="anchor level"):
            score_criterion(
                meddicc["economic_buyer"],
                self._evidence(meddicc, extraction, transcript, call),
                ScriptedJudge(default=bad),
                framework_id=meddicc.framework_id,
            )

    def test_a_missing_rationale_falls_back_to_the_anchor_text(
        self, meddicc, extraction, transcript, call
    ):
        score = score_criterion(
            meddicc["economic_buyer"],
            self._evidence(meddicc, extraction, transcript, call),
            ScriptedJudge(default={"level": 3}),
            framework_id=meddicc.framework_id,
        )
        assert meddicc["economic_buyer"].anchor(3) in score.rationale


class TestGroundedForFree:
    def test_the_score_inherits_the_extraction_spans(
        self, meddicc, extraction, transcript, call
    ):
        score = score_criterion(
            meddicc["economic_buyer"],
            gather_evidence(
                meddicc["economic_buyer"], extraction.claims, transcript, call
            ),
            ScriptedJudge(),
            framework_id=meddicc.framework_id,
        )
        assert score.spans
        assert "Priya" in score.quote
        for span in score.spans:
            transcript.validate_span(span)

    def test_the_framework_version_travels_with_the_score(
        self, meddicc, extraction, transcript, call
    ):
        # A score of 2 means nothing without the words that defined 2.
        result = score_call(extraction, meddicc, ScriptedJudge(), transcript, call)
        assert result.framework_id == "MEDDICC@3"
        assert all(s.framework_id == "MEDDICC@3" for s in result.scores)


class TestCallScore:
    def test_scoring_a_stale_extraction_is_refused(
        self, meddicc, extraction, transcript, call
    ):
        revised = transcript.revise(
            [segment("s0", "p_rep", "Different words entirely.", 0)],
            note="correction",
        )
        with pytest.raises(ValueError, match="Re-extract"):
            score_call(extraction, meddicc, ScriptedJudge(), revised, call)

    def test_judgment_criteria_always_need_review(
        self, meddicc, extraction, transcript, call
    ):
        result = score_call(extraction, meddicc, ScriptedJudge(), transcript, call)
        assert {s.criterion_key for s in result.needs_review} == {
            c.key for c in meddicc.judgment
        }
        assert {s.criterion_key for s in result.auto_writable} == {
            c.key for c in meddicc.factual
        }

    def test_coverage_says_what_was_not_discussed(
        self, meddicc, extraction, transcript, call
    ):
        result = score_call(extraction, meddicc, ScriptedJudge(), transcript, call)
        # Two of seven criteria have evidence in this call.
        assert result.levels["competition"] == 0
        assert result.levels["champion"] == 0
        assert result.coverage == pytest.approx(2 / 7)

    def test_there_is_deliberately_no_overall_number(self):
        # Summing ordinals asserts that a step on "champion" is the same size
        # as a step on "metrics". It is not.
        names = set(dir(CallScore))
        assert "total" not in names
        assert "overall" not in names

    def test_a_four_criterion_framework_scores_the_same_way(
        self, bant, extraction, transcript, call
    ):
        result = score_call(extraction, bant, ScriptedJudge(), transcript, call)
        assert result.framework_id == "BANT@1"
        assert len(result.scores) == 4
        # BANT's Authority reads the same extraction field MEDDICC's Economic
        # Buyer does, so it finds the same evidence.
        assert result.levels["authority"] > 0
        assert result.levels["timing"] == 0


class TestWhoSaidItComesFromTheTranscript:
    """The exclusion in `gather_evidence` must not be defeatable by the model.

    `subject_participant_id` is who a claim is *about*, and it is model
    output. If it decided who *said* something, a model could tag the rep's
    own ROI number as being about the customer -- a natural thing to emit --
    and MEDDICC Metrics would score 3 on vendor marketing.
    """

    def test_a_model_cannot_relabel_the_speaker(self, meddicc, transcript, call):
        vendor_roi = claim(
            Field.METRIC,
            "300% return",
            transcript,
            "s2",  # spoken by p_rep
            "three hundred percent return",
            subject_participant_id="p_dana",  # the model says it is about Dana
        )
        evidence = gather_evidence(
            meddicc["metrics"], (vendor_roi,), transcript, call
        )
        assert evidence.items == ()
        assert "stated by the rep" in evidence.excluded[0][1]

    def test_the_speaker_is_reported_from_the_transcript(
        self, meddicc, transcript, call
    ):
        prospect_metric = claim(
            Field.METRIC,
            "40 hours a week",
            transcript,
            "s1",  # spoken by p_dana
            "forty hours a week",
            subject_participant_id="p_rep",  # the model is wrong about this
        )
        evidence = gather_evidence(
            meddicc["metrics"], (prospect_metric,), transcript, call
        )
        assert len(evidence.items) == 1
        assert evidence.items[0].stated_by == "p_dana"


class TestTheJudgeIsAModelToo:
    """Section 10.2 applies to the judge as much as to the extractor."""

    class Redactor:
        def redact(self, text):
            class R:
                pass

            r = R()
            r.text = text.replace("Priya", "[PERSON_1]")
            return r

        def restore(self, text):
            return text.replace("[PERSON_1]", "Priya")

    def test_the_evidence_block_is_redacted(self, meddicc, extraction, transcript, call):
        criterion = meddicc["economic_buyer"]
        evidence = gather_evidence(criterion, extraction.claims, transcript, call)
        prompt = build_prompt(criterion, evidence, self.Redactor())
        assert "Priya" not in prompt
        assert "[PERSON_1]" in prompt

    def test_the_rationale_comes_back_restored(
        self, meddicc, extraction, transcript, call
    ):
        # The judge quotes what it was shown, so its rationale arrives
        # carrying placeholders. The rep is entitled to read the real thing.
        criterion = meddicc["economic_buyer"]
        evidence = gather_evidence(criterion, extraction.claims, transcript, call)
        score = score_criterion(
            criterion,
            evidence,
            ScriptedJudge(default={"level": 3, "rationale": "[PERSON_1] signs off"}),
            framework_id=meddicc.framework_id,
            redactor=self.Redactor(),
        )
        assert score.rationale == "Priya signs off"
