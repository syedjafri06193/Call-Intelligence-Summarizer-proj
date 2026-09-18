"""Span grounding (design.md sections 6.2, 6.3, 6.4, 16.1).

Grounding is the anti-hallucination mechanism, the trust mechanism, and the
evaluation mechanism at once. These tests exercise it against a model that
hallucinates deliberately, because a grounding layer only tested against a
well-behaved model is a grounding layer that has never done its job.
"""

from __future__ import annotations

import pytest

from cis.extract.chunk import chunk_by_turns
from cis.extract.grounded import (
    ATTRIBUTION_DEPENDENT,
    Field,
    GroundedClaim,
    UngroundedClaim,
    extract_chunk,
    validate,
    validate_all,
)
from cis.extract.reconcile import normalize_value, reconcile
from cis.transcript.model import SpanMismatch, Transcript, TranscriptSpan

from .conftest import segment


# --------------------------------------------------------------- fixtures


@pytest.fixture
def discovery_call() -> Transcript:
    """A short call with everything the tests need to quote."""
    return Transcript(
        "call_001",
        [
            segment("s0", "p_rep", "So walk me through how you handle this today.", 0),
            segment(
                "s1",
                "p_dana",
                "Right now it's three people doing it manually, and we're "
                "losing about forty hours a week to it.",
                4_000,
            ),
            segment(
                "s2",
                "p_rep",
                "And who would need to sign off on something like this?",
                12_000,
            ),
            segment(
                "s3",
                "p_dana",
                "That would be me, and then Priya on the finance side has "
                "the final say on anything over fifty thousand.",
                16_000,
            ),
            segment(
                "s4",
                "p_marco",
                "We looked at Salesforce for this last year but the timeline "
                "killed it.",
                24_000,
            ),
            segment(
                "s5",
                "p_rep",
                "I'll send over the security questionnaire by Friday.",
                30_000,
            ),
        ],
    )


class ScriptedModel:
    """A model that returns exactly what it is told to.

    Including quotes that are not in the transcript, which is the point.
    """

    def __init__(self, response: dict, *, per_chunk: list[dict] | None = None) -> None:
        self.response = response
        self.per_chunk = per_chunk
        self.calls = 0

    def extract(self, chunk_text: str, fields):
        index = self.calls
        self.calls += 1
        if self.per_chunk is not None:
            return self.per_chunk[index] if index < len(self.per_chunk) else {}
        return self.response


# --------------------------------------------------- the structural rule


class TestClaimsCannotBeUngrounded:
    def test_a_claim_with_no_spans_cannot_be_constructed(self):
        # Enforced in __post_init__ rather than by convention: the whole
        # argument rests on it being structurally impossible to hold an
        # ungrounded claim.
        with pytest.raises(UngroundedClaim, match="no supporting span"):
            GroundedClaim(
                field=Field.ECONOMIC_BUYER,
                value="Dana Chen",
                spans=(),
                confidence=0.9,
            )

    def test_confidence_outside_the_unit_interval_is_rejected(self, discovery_call):
        span = discovery_call.make_span("s3", 0, 10)
        with pytest.raises(ValueError, match="confidence"):
            GroundedClaim(Field.PAIN, "x", (span,), confidence=1.4)


# --------------------------------------------------- hallucination catching


class TestHallucination:
    def test_a_fabricated_quote_is_discarded(self, discovery_call, caplog):
        chunk = chunk_by_turns(discovery_call)[0]
        model = ScriptedModel(
            {
                "economic_buyer": {
                    "value": "Priya Raman, CFO",
                    # Never said. This is the failure mode grounding exists for.
                    "quotes": ["Priya is our CFO and she signs off on everything"],
                    "confidence": 0.9,
                }
            }
        )

        claims, stats = extract_chunk(chunk, discovery_call, model, list(Field))

        assert claims == (), "an ungrounded claim must never reach the caller"
        assert stats.quotes_offered == 1
        assert stats.quotes_located == 0
        assert stats.hallucination_rate == 1.0
        assert stats.dropped_quotes[0][0] == "economic_buyer"

    def test_the_discarded_quote_is_logged_as_the_monitor(self, discovery_call, caplog):
        # Section 16.1: "The logger.warning on a discarded quote is your
        # hallucination monitor. Track its rate over time -- a rising rate
        # after a model change is a regression signal you'd otherwise miss
        # entirely."
        import logging

        chunk = chunk_by_turns(discovery_call)[0]
        model = ScriptedModel(
            {"pain": {"value": "x", "quotes": ["never said this"], "confidence": 0.5}}
        )
        with caplog.at_level(logging.WARNING, logger="cis.extract.grounded"):
            extract_chunk(chunk, discovery_call, model, list(Field))
        assert any("quote not found" in r.message for r in caplog.records)

    def test_a_partially_hallucinated_claim_keeps_its_real_evidence(
        self, discovery_call
    ):
        # One good quote and one invented one. The claim survives on the good
        # one; the invented one is dropped and counted.
        chunk = chunk_by_turns(discovery_call)[0]
        model = ScriptedModel(
            {
                "pain": {
                    "value": "forty hours a week lost to manual work",
                    "quotes": [
                        "losing about forty hours a week to it",
                        "and it's costing us a fortune",  # invented
                    ],
                    "confidence": 0.8,
                }
            }
        )
        claims, stats = extract_chunk(chunk, discovery_call, model, list(Field))

        assert len(claims) == 1
        assert len(claims[0].spans) == 1
        assert stats.quotes_offered == 2
        assert stats.quotes_located == 1
        assert stats.hallucination_rate == 0.5

    def test_a_claim_with_a_value_but_no_quotes_is_dropped(self, discovery_call):
        chunk = chunk_by_turns(discovery_call)[0]
        model = ScriptedModel(
            {"budget": {"value": "$50,000", "quotes": [], "confidence": 0.95}}
        )
        claims, stats = extract_chunk(chunk, discovery_call, model, list(Field))
        assert claims == ()
        assert stats.claims_dropped_ungrounded == 1

    def test_a_null_field_is_not_an_error(self, discovery_call):
        # Section 6.4 tells the model to output null when nothing supports a
        # field. Doing as it is told must be the cheap path.
        chunk = chunk_by_turns(discovery_call)[0]
        model = ScriptedModel({"champion": None, "budget": None})
        claims, stats = extract_chunk(chunk, discovery_call, model, list(Field))
        assert claims == ()
        assert stats.fields_returned == 0
        assert stats.hallucination_rate == 0.0


# ------------------------------------------------------------ span quality


class TestSpans:
    def test_a_located_span_quotes_the_transcript_not_the_model(
        self, discovery_call
    ):
        # The model reformats whitespace; the span must carry the
        # transcript's own text. A span whose text came from the model would
        # validate against itself and prove nothing.
        chunk = chunk_by_turns(discovery_call)[0]
        model = ScriptedModel(
            {
                "pain": {
                    "value": "manual process",
                    "quotes": ["three   people\n  doing it manually"],
                    "confidence": 0.8,
                }
            }
        )
        claims, _ = extract_chunk(chunk, discovery_call, model, list(Field))

        assert len(claims) == 1
        span = claims[0].spans[0]
        assert span.text == "three people doing it manually"
        # And it resolves against the transcript.
        discovery_call.validate_span(span)

    def test_whitespace_is_fuzzy_but_words_are_not(self, discovery_call):
        chunk = chunk_by_turns(discovery_call)[0]

        # Whitespace differences: accepted.
        assert chunk.locate("three people   doing it manually") is not None
        # A changed word: rejected. That is the model writing, not quoting.
        assert chunk.locate("three people doing it by hand") is None
        # An expanded contraction: also rejected, and deliberately so.
        assert chunk.locate("Right now it is three people") is None

    def test_a_span_carries_playable_timings(self, discovery_call):
        chunk = chunk_by_turns(discovery_call)[0]
        span = chunk.locate("forty hours a week")
        assert span is not None
        # Interpolated from the word timings, not the whole turn: a citation
        # that plays the entire four-minute segment is technically grounded
        # and practically useless.
        seg = discovery_call.segment("s1")
        assert seg is not None
        assert span.start_ms > seg.start_ms
        assert span.end_ms <= seg.end_ms
        assert span.end_ms > span.start_ms

    def test_validation_catches_a_span_that_no_longer_matches(self, discovery_call):
        span = discovery_call.make_span("s1", 0, 9)
        claim = GroundedClaim(Field.PAIN, "x", (span,), 0.8)
        validate(claim, discovery_call)

        # A corrected transcript at v2. The stored span cites v1 and must not
        # silently resolve against v2 -- that is how a claim comes to quote
        # text it was never extracted from.
        revised = discovery_call.revise(
            [
                s if s.segment_id != "s1"
                else segment("s1", "p_dana", "Completely different text now.", 4_000)
                for s in discovery_call.segments
            ],
            note="asr correction",
        )
        with pytest.raises(SpanMismatch):
            validate(claim, revised)

    def test_validate_all_partitions_instead_of_raising(self, discovery_call):
        # At display time one bad claim must not hide the other fifteen.
        good = GroundedClaim(
            Field.PAIN, "ok", (discovery_call.make_span("s1", 0, 9),), 0.8
        )
        stale = GroundedClaim(
            Field.BUDGET,
            "bad",
            (
                TranscriptSpan(
                    transcript_version=1,
                    segment_id="s1",
                    start_char=0,
                    end_char=9,
                    start_ms=0,
                    end_ms=1,
                    text="not what the transcript says",
                ),
            ),
            0.8,
        )
        ok, rejected = validate_all([good, stale], discovery_call)
        assert ok == (good,)
        assert len(rejected) == 1
        assert "span" in rejected[0][1].lower()


# ------------------------------------------------------------- attribution


class TestAttributionDependence:
    def test_commitments_are_refused_on_an_unattributed_transcript(
        self, discovery_call
    ):
        # Section 4.3: "A transcript that silently mis-attributes a commitment
        # is worse than one that says 'speaker unknown.'"
        chunk = chunk_by_turns(discovery_call)[0]
        model = ScriptedModel(
            {
                "commitment": {
                    "value": "send the security questionnaire by Friday",
                    "quotes": ["I'll send over the security questionnaire by Friday"],
                    "confidence": 0.95,
                },
                "pain": {
                    "value": "manual process",
                    "quotes": ["three people doing it manually"],
                    "confidence": 0.8,
                },
            }
        )

        claims, stats = extract_chunk(
            chunk, discovery_call, model, list(Field), attributed=False
        )

        fields = {c.field for c in claims}
        assert Field.COMMITMENT not in fields
        assert Field.PAIN in fields, "topic extraction still works unattributed"
        assert stats.claims_dropped_unattributed == 1

    def test_the_attribution_dependent_set_is_the_ones_naming_a_person(self):
        for field in ATTRIBUTION_DEPENDENT:
            assert field in {
                Field.ECONOMIC_BUYER,
                Field.CHAMPION,
                Field.COMMITMENT,
                Field.NEXT_STEP,
            }


# --------------------------------------------------------------- chunking


class TestChunking:
    def test_turns_are_never_split(self, discovery_call):
        chunks = chunk_by_turns(discovery_call, target_tokens=20, overlap_turns=1)
        assert len(chunks) > 1
        original = [s.text for s in discovery_call.segments]
        for chunk in chunks:
            for seg in chunk.segments:
                assert seg.text in original

    def test_an_oversized_turn_becomes_its_own_chunk_rather_than_being_cut(self):
        long_turn = " ".join(["word"] * 4_000)
        transcript = Transcript(
            "call_001",
            [
                segment("s0", "p_rep", "Short.", 0),
                segment("s1", "p_dana", long_turn, 1_000),
                segment("s2", "p_rep", "Also short.", 2_000),
            ],
        )
        chunks = chunk_by_turns(transcript, target_tokens=100, overlap_turns=0)
        # Half a sentence extracted out of context is worse than a chunk that
        # is over budget.
        assert any(
            len(c.segments) == 1 and c.segments[0].segment_id == "s1" for c in chunks
        )

    def test_chunks_overlap_by_whole_turns(self, discovery_call):
        # 60 tokens is ~240 characters, which fits two or three of these turns.
        # The overlap only exists when a chunk holds more turns than the
        # overlap width -- see the next test for the other case.
        chunks = chunk_by_turns(discovery_call, target_tokens=60, overlap_turns=2)
        assert len(chunks) >= 2
        for previous, current in zip(chunks, chunks[1:]):
            shared = {s.segment_id for s in previous.segments} & {
                s.segment_id for s in current.segments
            }
            assert shared, "consecutive chunks must share context"
            assert current.overlap_segment_ids == shared

    def test_forward_progress_beats_overlap_when_one_turn_fills_a_chunk(
        self, discovery_call
    ):
        """Overlap is the thing that yields, and it has to be.

        When the budget only fits a single turn, honouring the overlap would
        mean either re-emitting that turn forever or emitting chunks at twice
        the budget. The guarantee that survives is forward progress: every
        turn appears in exactly one chunk, in order, and none is dropped.
        """
        chunks = chunk_by_turns(discovery_call, target_tokens=25, overlap_turns=2)
        assert all(len(c.segments) == 1 for c in chunks)
        assert [c.segments[0].segment_id for c in chunks] == [
            s.segment_id for s in discovery_call.segments
        ]
        assert all(c.overlap_segment_ids == frozenset() for c in chunks)

    def test_chunking_terminates_on_pathological_input(self):
        # A chunk shorter than the overlap would loop forever without the
        # forward-progress guard.
        transcript = Transcript(
            "call_001",
            [segment(f"s{i}", "p_rep", "A very long turn " * 40, i * 1000) for i in range(6)],
        )
        chunks = chunk_by_turns(transcript, target_tokens=10, overlap_turns=50)
        assert len(chunks) == 6

    def test_the_chunk_text_names_the_speaker(self, discovery_call):
        # Who said it is load-bearing: MEDDICC's Metrics definition requires
        # the number to be stated by the prospect, and vendor-asserted ROI
        # does not count.
        chunk = chunk_by_turns(discovery_call)[0]
        assert "[p_dana]" in chunk.text
        assert "[p_rep]" in chunk.text


# ------------------------------------------------------------ reconciliation


class TestReconciliation:
    def _claim(self, transcript, field, value, segment_id, chunk_index, conf=0.8):
        seg = transcript.segment(segment_id)
        assert seg is not None
        span = transcript.make_span(segment_id, 0, min(20, len(seg.text)))
        return GroundedClaim(
            field=field,
            value=value,
            spans=(span,),
            confidence=conf,
            source_chunks=(chunk_index,),
        )

    def test_the_same_claim_from_two_chunks_becomes_one(self, discovery_call):
        a = self._claim(discovery_call, Field.PAIN, "manual process", "s1", 0)
        b = self._claim(discovery_call, Field.PAIN, "Manual Process ", "s1", 1)
        result = reconcile("call_001", 1, [[a], [b]])
        assert len(result.by_field(Field.PAIN)) == 1
        assert result.claims[0].source_chunks == (0, 1)

    def test_a_changed_budget_is_surfaced_not_collapsed(self, discovery_call):
        # THE POINT OF THIS MODULE. Section 6.2: "If one chunk says the budget
        # is $50k and another says $80k, that's usually a real thing that
        # happened in the call, and it's more valuable surfaced than silently
        # collapsed."
        early = self._claim(discovery_call, Field.BUDGET, "$50k", "s1", 0)
        later = self._claim(discovery_call, Field.BUDGET, "$80,000", "s4", 1)
        result = reconcile("call_001", 1, [[early], [later]])

        assert len(result.contradictions) == 1
        contradiction = result.contradictions[0]
        assert contradiction.field is Field.BUDGET
        assert set(contradiction.values) == {"$50k", "$80,000"}
        # Both survive, in time order, with their own evidence.
        assert len(result.by_field(Field.BUDGET)) == 2
        assert "then" in contradiction.describe()

    def test_equivalent_money_formats_are_not_a_contradiction(self, discovery_call):
        a = self._claim(discovery_call, Field.BUDGET, "$50k", "s1", 0)
        b = self._claim(discovery_call, Field.BUDGET, "50,000", "s3", 1)
        result = reconcile("call_001", 1, [[a], [b]])
        assert result.contradictions == ()
        assert len(result.by_field(Field.BUDGET)) == 1

    def test_multi_valued_fields_are_not_contradictions(self, discovery_call):
        # Two competitors is two facts, not a disagreement.
        a = self._claim(discovery_call, Field.COMPETITOR, "Salesforce", "s4", 0)
        b = self._claim(discovery_call, Field.COMPETITOR, "HubSpot", "s4", 1)
        result = reconcile("call_001", 1, [[a], [b]])
        assert result.contradictions == ()
        assert len(result.by_field(Field.COMPETITOR)) == 2

    def test_a_name_with_and_without_a_title_is_one_person(self, discovery_call):
        a = self._claim(discovery_call, Field.ECONOMIC_BUYER, "Priya Raman", "s3", 0)
        b = self._claim(
            discovery_call, Field.ECONOMIC_BUYER, "Priya Raman, VP Finance", "s3", 1
        )
        result = reconcile("call_001", 1, [[a], [b]])
        assert result.contradictions == ()
        assert len(result.by_field(Field.ECONOMIC_BUYER)) == 1

    def test_spans_are_unioned_and_deduplicated(self, discovery_call):
        a = self._claim(discovery_call, Field.PAIN, "manual", "s1", 0)
        b = self._claim(discovery_call, Field.PAIN, "manual", "s1", 1)  # same span
        c = self._claim(discovery_call, Field.PAIN, "manual", "s3", 2)
        result = reconcile("call_001", 1, [[a], [b], [c]])
        claim = result.by_field(Field.PAIN)[0]
        assert len(claim.spans) == 2, "the overlap duplicate is not new evidence"

    def test_confidence_takes_the_maximum(self, discovery_call):
        # A claim seen once with high confidence and once with low is not less
        # certain than the high one alone.
        a = self._claim(discovery_call, Field.PAIN, "manual", "s1", 0, conf=0.9)
        b = self._claim(discovery_call, Field.PAIN, "manual", "s3", 1, conf=0.3)
        result = reconcile("call_001", 1, [[a], [b]])
        assert result.by_field(Field.PAIN)[0].confidence == 0.9

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("$50k", "$50000"),
            ("50,000", "$50000"),
            ("$50,000.00", "$50000"),
            ("1.5 million", "$1500000"),
            ("£80k", "£80000"),
        ],
    )
    def test_money_normalization(self, raw, expected):
        assert normalize_value(Field.BUDGET, raw) == expected

    def test_unparseable_money_is_left_alone_rather_than_half_parsed(self):
        # A wrong parse invents a contradiction, which is worse than missing a
        # duplicate.
        assert normalize_value(Field.BUDGET, "fifty thousand dollars") == (
            "fifty thousand dollars"
        )


class TestMoneyNormalisationDoesNotInventEquality:
    """`normalize_value` merges by design, so what it merges is load bearing.

    "Over-normalizing merges two genuinely different values and hides a
    contradiction, which is the exact failure this module exists to avoid."
    A pattern that treats any digit run as money turns "Q4 budget cycle" into
    "$4" -- and two unrelated budget claims then collapse into one.
    """

    @pytest.mark.parametrize(
        "text", ["Q4 budget cycle", "Q3 planning", "2026 budget", "around 250 users"]
    )
    def test_a_bare_number_is_not_money(self, text):
        from cis.extract.grounded import Field as F

        assert normalize_value(F.BUDGET, text) == text.lower()

    def test_two_unrelated_budget_phrases_do_not_merge(self):
        from cis.extract.grounded import Field as F

        assert normalize_value(F.BUDGET, "Q4 budget cycle") != normalize_value(
            F.BUDGET, "Q4 spend review"
        )

    def test_a_range_is_left_alone(self):
        # Reducing "50-80k" to one end either hides a real spread or invents a
        # contradiction with a claim that quoted the other end.
        from cis.extract.grounded import Field as F

        assert normalize_value(F.BUDGET, "50-80k range") == "50-80k range"

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("$50k", "$50000"),
            ("50,000", "$50000"),
            ("$50,000", "$50000"),
            ("1.5 million", "$1500000"),
            ("80k", "$80000"),
        ],
    )
    def test_real_amounts_still_normalise(self, text, expected):
        from cis.extract.grounded import Field as F

        assert normalize_value(F.BUDGET, text) == expected

    def test_a_spelled_out_number_is_left_alone(self):
        from cis.extract.grounded import Field as F

        assert normalize_value(F.BUDGET, "fifty thousand dollars") == (
            "fifty thousand dollars"
        )
