"""The evaluation harness (design.md section 8).

The first class here is the most important one in the file. An agreement
statistic that is subtly wrong does not fail loudly -- it produces a confident
ceiling that is not the ceiling, and every model result afterwards is measured
against a number nobody checked. So the implementation is verified against
Krippendorff's own published worked example, on all three difference
functions, before anything else in this module is trusted.
"""

from __future__ import annotations

import math

import pytest

from cis.extract.grounded import Field, GroundedClaim
from cis.transcript.model import Transcript
from eval.agreement import (
    Metric,
    NoConsensus,
    consensus,
    krippendorff_alpha,
    measure_against_ceiling,
    rater_agreement,
    stratification_gaps,
)
from eval.consistency import improvement_is_meaningful, rerun_consistency
from eval.extraction_metrics import (
    GoldFact,
    SpanVerdict,
    evaluate_extraction,
    per_field,
)

from .conftest import segment

# Krippendorff, "Computing Krippendorff's Alpha-Reliability" (2011), the
# worked example used throughout the literature: 15 units, 3 observers,
# missing ratings everywhere. Published results: nominal .691, ordinal .807,
# interval .811.
CANONICAL = {
    "A": "*  *  *  *  *  3  4  1  2  1  1  3  3  *  3",
    "B": "1  *  2  1  3  3  4  3  *  *  *  *  *  *  *",
    "C": "*  *  2  1  3  4  4  *  2  1  1  3  3  *  4",
}


def canonical_ratings() -> dict[str, dict[str, int]]:
    columns = {rater: row.split() for rater, row in CANONICAL.items()}
    out: dict[str, dict[str, int]] = {}
    for unit in range(15):
        per_rater = {
            rater: int(values[unit])
            for rater, values in columns.items()
            if values[unit] != "*"
        }
        out[f"u{unit}"] = per_rater
    return out


class TestAlphaAgainstPublishedValues:
    """If this class fails, nothing else in the evaluation is trustworthy."""

    @pytest.mark.parametrize(
        "metric,published",
        [
            (Metric.NOMINAL, 0.691),
            (Metric.ORDINAL, 0.807),
            (Metric.INTERVAL, 0.811),
        ],
    )
    def test_reproduces_krippendorffs_worked_example(self, metric, published):
        alpha = krippendorff_alpha(canonical_ratings(), metric=metric)
        assert alpha == pytest.approx(published, abs=0.001)

    def test_perfect_agreement_is_one(self):
        ratings = {
            "u1": {"a": 0, "b": 0},
            "u2": {"a": 3, "b": 3},
            "u3": {"a": 1, "b": 1, "c": 1},
        }
        assert krippendorff_alpha(ratings) == pytest.approx(1.0)

    def test_systematic_disagreement_goes_negative(self):
        # Alpha below zero is a real state and worth preserving: it means the
        # raters disagree more than chance would, which usually means they
        # read the anchors differently rather than that one is careless.
        ratings = {f"u{i}": {"a": 0, "b": 3} for i in range(4)}
        ratings.update({f"v{i}": {"a": 3, "b": 0} for i in range(4)})
        assert krippendorff_alpha(ratings) < 0

    def test_ordinal_sees_the_size_of_a_disagreement_and_nominal_does_not(self):
        # Both sets have identical marginals -- every level used exactly
        # twice -- and in both sets every rater pair disagrees. They differ
        # only in how far apart the disagreements are. Equal marginals is
        # what makes this a fair comparison: alpha's expected disagreement is
        # computed from them, so two sets with different marginals would
        # differ for a reason that has nothing to do with distance.
        near = {
            "u0": {"a": 0, "b": 1},
            "u1": {"a": 1, "b": 0},
            "u2": {"a": 2, "b": 3},
            "u3": {"a": 3, "b": 2},
        }
        far = {
            "u0": {"a": 0, "b": 2},
            "u1": {"a": 2, "b": 0},
            "u2": {"a": 1, "b": 3},
            "u3": {"a": 3, "b": 1},
        }
        assert krippendorff_alpha(near, metric=Metric.ORDINAL) > krippendorff_alpha(
            far, metric=Metric.ORDINAL
        )
        assert krippendorff_alpha(near, metric=Metric.NOMINAL) == pytest.approx(
            krippendorff_alpha(far, metric=Metric.NOMINAL)
        )

    def test_missing_ratings_are_handled_not_dropped(self):
        # Section 16.2: "Handles missing ratings, which matters because raters
        # skip calls." The canonical example is mostly holes and still
        # produces the published number, which is the real proof; this checks
        # that a unit rated once does not poison the result.
        ratings = canonical_ratings()
        ratings["lonely"] = {"A": 2}
        assert krippendorff_alpha(ratings, metric=Metric.ORDINAL) == pytest.approx(
            0.807, abs=0.001
        )


class TestAlphaIsUndefinedRatherThanWrong:
    def test_no_unit_rated_twice_is_undefined_not_zero(self):
        assert math.isnan(krippendorff_alpha({"u1": {"a": 1}, "u2": {"b": 2}}))

    def test_everyone_agreeing_on_one_value_is_undefined_not_one(self):
        # Three raters who give every call a 0 agree perfectly and have
        # demonstrated nothing. Reporting 1.0 here would claim excellent
        # reliability for labels that carry no information.
        ratings = {f"u{i}": {"a": 0, "b": 0, "c": 0} for i in range(20)}
        assert math.isnan(krippendorff_alpha(ratings))

    def test_an_undefined_alpha_is_reported_as_such(self):
        report = rater_agreement({"c1": {"r1": {"champion": 2}}})
        assert not report["champion"].defined
        assert "undefined" in report["champion"].describe()


# ------------------------------------------------- the report, per criterion


def labelled_set() -> dict[str, dict[str, dict[str, int]]]:
    """Three raters, ten calls. Factual criterion agreed, judgment not."""
    economic = [3, 0, 2, 3, 1, 0, 3, 2, 1, 3]
    # Chosen so that alpha lands at 0.38 -- the figure in section 8.2's
    # table, which is there precisely because it looks alarming and is not.
    champion_r1 = [3, 0, 3, 3, 0, 1, 3, 3, 2, 2]
    champion_r2 = [3, 0, 0, 2, 2, 1, 2, 2, 2, 3]
    champion_r3 = [2, 0, 1, 2, 0, 0, 2, 0, 2, 3]

    out: dict[str, dict[str, dict[str, int]]] = {}
    for i in range(10):
        out[f"c{i}"] = {
            "r1": {"economic_buyer": economic[i], "champion": champion_r1[i]},
            "r2": {"economic_buyer": economic[i], "champion": champion_r2[i]},
            "r3": {"economic_buyer": economic[i], "champion": champion_r3[i]},
        }
    return out


class TestPerCriterionReporting:
    def test_agreement_differs_sharply_between_factual_and_judgment(self):
        # Section 8.1's "typical outcome", reproduced: the distribution is the
        # finding, and it is what should shape the product.
        report = rater_agreement(labelled_set())
        assert report["economic_buyer"].alpha == pytest.approx(1.0)
        assert report["champion"].alpha == pytest.approx(0.38, abs=0.01)

    def test_the_weakest_criterion_is_findable(self):
        report = rater_agreement(labelled_set())
        assert report.weakest.criterion_key == "champion"
        assert [a.criterion_key for a in report.unreliable()] == ["champion"]

    def test_there_is_no_aggregate_alpha(self):
        # Section 16.2: "The aggregate hides exactly the variation that should
        # drive product decisions."
        report = rater_agreement(labelled_set())
        assert not hasattr(report, "alpha")
        assert not hasattr(report, "overall")

    def test_the_counts_travel_with_the_number(self):
        report = rater_agreement(labelled_set())
        assert report["champion"].units_used == 10
        assert report["champion"].raters == 3
        assert report["champion"].ratings == 30

    def test_a_rater_who_skipped_calls_still_counts(self):
        labels = labelled_set()
        for call_id in ("c0", "c1", "c2"):
            del labels[call_id]["r3"]
        report = rater_agreement(labels)
        assert report["champion"].units_used == 10
        assert report["champion"].ratings == 27


class TestConsensusIsAdjudicated:
    def test_unanimity_is_consensus(self):
        assert consensus({"r1": 2, "r2": 2, "r3": 2}) == 2

    def test_a_majority_does_not_settle_it(self):
        # Section 8.1: "adjudicated discussion, not majority vote." A majority
        # vote manufactures a label no rater would defend.
        with pytest.raises(NoConsensus, match="majority"):
            consensus({"r1": 2, "r2": 2, "r3": 0})

    def test_an_adjudicated_value_settles_it(self):
        assert consensus({"r1": 2, "r2": 0}, adjudicated=1) == 1


class TestMeasuringAgainstTheCeiling:
    #: The adjudicated labels. Both criteria happen to share them here; only
    #: the model's answers differ between the two.
    CONSENSUS = [3, 0, 2, 3, 1, 0, 3, 2, 1, 3]
    #: A model that agrees with the consensus on champion at alpha 0.365 --
    #: section 8.2's 0.36, against that table's human ceiling of 0.38.
    MODEL_CHAMPION = [1, 0, 3, 1, 0, 1, 1, 1, 1, 2]

    def build(self):
        human = rater_agreement(labelled_set())
        consensus_labels = {
            f"c{i}": {"economic_buyer": v, "champion": v}
            for i, v in enumerate(self.CONSENSUS)
        }
        model_labels = {
            f"c{i}": {
                "economic_buyer": self.CONSENSUS[i],
                "champion": self.MODEL_CHAMPION[i],
            }
            for i in range(len(self.CONSENSUS))
        }
        return measure_against_ceiling(human, consensus_labels, model_labels)

    def test_both_columns_are_published(self):
        table = self.build().table()
        assert "human a" in table and "model a" in table

    def test_a_low_model_number_against_a_low_ceiling_is_not_a_model_problem(self):
        """Section 8.2's Champion row, which is the whole point of the table.

        A raw 0.36 looks bad in isolation. Against a human ceiling of 0.38
        the model is performing as well as a person, and the verdict has to
        say that the criterion is the thing to fix, not the model.
        """
        row = self.build()["champion"]
        assert row.human_alpha == pytest.approx(0.38, abs=0.01)
        assert row.model_alpha == pytest.approx(0.36, abs=0.01)
        assert row.fraction_of_ceiling == pytest.approx(0.95, abs=0.02)
        assert row.verdict == "at the human ceiling; the criterion is what needs work"

    def test_a_criterion_humans_agree_on_reads_plainly(self):
        assert self.build()["economic_buyer"].verdict == "at the human ceiling"

    def test_a_ceiling_at_or_below_chance_is_not_a_ceiling(self):
        # Dividing by a negative alpha produces a ratio that looks like a
        # percentage and means nothing.
        human = rater_agreement(
            {f"c{i}": {"r1": {"k": 0}, "r2": {"k": 3}} for i in range(4)}
            | {f"d{i}": {"r1": {"k": 3}, "r2": {"k": 0}} for i in range(4)}
        )
        report = measure_against_ceiling(
            human,
            {f"c{i}": {"k": 1} for i in range(4)} | {f"d{i}": {"k": 2} for i in range(4)},
            {f"c{i}": {"k": 1} for i in range(4)} | {f"d{i}": {"k": 2} for i in range(4)},
        )
        row = report["k"]
        assert row.human_alpha < 0
        assert math.isnan(row.fraction_of_ceiling)
        assert "chance" in row.verdict


class TestStratification:
    def test_gaps_are_counted(self):
        # Forty calls from one rep produces a ceiling for that rep, and the
        # failure is invisible unless something counts.
        strata = {
            "enterprise": ["c1", "c2", "c3"],
            "mid_market": ["c4", "c5"],
            "smb": ["c6"],
        }
        gaps = stratification_gaps(strata, labelled=["c1", "c2", "c3", "c4"])
        assert gaps == {"enterprise": 3, "mid_market": 1, "smb": 0}


# ------------------------------------------------------ extraction metrics


@pytest.fixture
def transcript() -> Transcript:
    return Transcript(
        "call_001",
        [
            segment("s0", "p_dana", "We lose about forty hours a week on this.", 0),
            segment("s1", "p_dana", "Priya signs off on anything over fifty thousand.", 5_000),
        ],
    )


def made(transcript: Transcript, field: Field, value: str, seg: str, text: str):
    start = transcript.segment(seg).text.index(text)
    span = transcript.make_span(seg, start, start + len(text))
    return GroundedClaim(field=field, value=value, spans=(span,), confidence=0.9)


class TestExtractionIsMeasuredObjectively:
    def test_precision_and_recall_are_separate_numbers(self, transcript):
        predicted = {
            "call_001": [
                made(transcript, Field.BUDGET, "$50k", "s1", "fifty thousand"),
                made(transcript, Field.METRIC, "wrong", "s0", "forty hours a week"),
            ]
        }
        gold = [
            GoldFact("call_001", Field.BUDGET, "50,000"),
            GoldFact("call_001", Field.METRIC, "40 hours a week"),
            GoldFact("call_001", Field.ECONOMIC_BUYER, "Priya"),
        ]
        metrics = evaluate_extraction(predicted, gold)
        # "$50k" and "50,000" are the same budget, because matching uses the
        # same normalizer reconciliation does.
        assert metrics.true_positives == 1
        assert metrics.false_positives == 1
        assert metrics.false_negatives == 2
        assert metrics.precision == pytest.approx(0.5)
        assert metrics.recall == pytest.approx(1 / 3)

    def test_span_accuracy_is_not_precision(self, transcript):
        """A claim can be right and cite the wrong line.

        That is a real defect even with the value correct: the rep clicks the
        quote, hears something unrelated, and stops trusting every quote in
        the product.
        """
        predicted = {
            "call_001": [made(transcript, Field.BUDGET, "$50k", "s1", "fifty thousand")]
        }
        gold = [GoldFact("call_001", Field.BUDGET, "$50k")]
        metrics = evaluate_extraction(
            predicted,
            gold,
            span_verdicts=[
                SpanVerdict("call_001", Field.BUDGET, "$50k", "fifty thousand", False)
            ],
        )
        assert metrics.precision == pytest.approx(1.0)
        assert metrics.span_accuracy == pytest.approx(0.0)

    def test_a_span_that_stopped_matching_counts_as_a_hallucination(self, transcript):
        claim = made(transcript, Field.BUDGET, "$50k", "s1", "fifty thousand")
        revised = transcript.revise(
            [segment("s1", "p_dana", "Priya signs off on the small stuff.", 5_000)],
            note="correction",
        )
        metrics = evaluate_extraction(
            {"call_001": [claim]},
            [GoldFact("call_001", Field.BUDGET, "$50k")],
            transcripts={"call_001": revised},
            quotes_offered=1,
            quotes_located=1,
        )
        # The quote was located at extraction time and is not there now. To
        # the rep looking at the screen those are the same thing.
        assert metrics.spans_not_in_transcript == 1
        assert metrics.hallucination_rate == pytest.approx(1.0)

    def test_per_field_splits_a_respectable_average(self, transcript):
        predicted = {
            "call_001": [
                made(transcript, Field.BUDGET, "$50k", "s1", "fifty thousand"),
                made(transcript, Field.METRIC, "40 hours a week", "s0", "forty hours"),
            ]
        }
        gold = [
            GoldFact("call_001", Field.BUDGET, "$50k"),
            GoldFact("call_001", Field.METRIC, "40 hours a week"),
            GoldFact("call_001", Field.ECONOMIC_BUYER, "Priya"),
        ]
        overall = evaluate_extraction(predicted, gold)
        split = per_field(predicted, gold)
        assert overall.f1 > 0.7
        assert split[Field.ECONOMIC_BUYER].recall == 0.0
        assert split[Field.BUDGET].recall == 1.0

    def test_gold_facts_are_not_claims(self):
        # Keeping the types apart stops a gold set from being produced by the
        # thing it is supposed to be measuring.
        assert not issubclass(GoldFact, GroundedClaim)


# --------------------------------------------------------- rerun variance


class TestSelfConsistency:
    def runs(self):
        stable = {f"c{i}": {"economic_buyer": 3, "champion": 2} for i in range(10)}
        wobbly = dict(stable)
        wobbly["c3"] = {"economic_buyer": 3, "champion": 1}
        wobbly["c7"] = {"economic_buyer": 3, "champion": 3}
        return [stable, wobbly, stable]

    def test_a_flip_rate_is_produced_per_criterion(self):
        report = rerun_consistency(self.runs())
        assert report["economic_buyer"].flip_rate == 0.0
        assert report["champion"].flip_rate == pytest.approx(0.2)

    def test_the_max_spread_survives_the_average(self):
        # A criterion can look stable on average and still swing on one call,
        # and that call is the one a rep notices.
        report = rerun_consistency(self.runs())
        assert report["champion"].max_spread == 1

    def test_the_noise_floor_caps_what_an_accuracy_change_can_mean(self):
        report = rerun_consistency(self.runs())
        assert report.noise_floor() == pytest.approx(0.2)
        assert not improvement_is_meaningful(0.03, report)
        assert improvement_is_meaningful(0.3, report)

    def test_a_per_criterion_floor_is_used_when_asked(self):
        report = rerun_consistency(self.runs())
        # Stable criterion: a small gain there is real.
        assert improvement_is_meaningful(
            0.03, report, criterion_key="economic_buyer"
        )

    def test_one_run_is_not_a_consistency_measurement(self):
        with pytest.raises(ValueError, match="at least two"):
            rerun_consistency([{"c0": {"champion": 1}}])

    def test_a_call_missing_from_a_run_is_skipped_not_counted_as_a_change(self):
        # A pipeline failure is a different defect, and counting it here
        # would hide it inside a consistency number.
        runs = self.runs()
        del runs[1]["c3"]
        report = rerun_consistency(runs)
        assert report["champion"].flip_rate == pytest.approx(0.1)
        assert report["champion"].calls == 10

    def test_the_table_states_the_floor_in_words(self):
        table = rerun_consistency(self.runs()).table()
        assert "noise floor" in table
        assert "not a result" in table


class TestMetricsAreUndefinedRatherThanZero:
    def test_a_stale_span_is_counted_without_a_separate_quote_count(self, transcript):
        """The natural call has to report the thing the metric exists for.

        With `quotes_offered` defaulting to 0 and used as the only
        denominator, passing predictions, gold and transcripts -- which is
        how a caller re-checks stored claims -- reported 0.0 for a claim
        whose span no longer exists.
        """
        stale = made(transcript, Field.BUDGET, "$50k", "s1", "fifty thousand")
        revised = transcript.revise(
            [segment("s1", "p_dana", "Priya signs off on the small stuff.", 5_000)],
            note="correction",
        )
        metrics = evaluate_extraction(
            {"call_001": [stale]},
            [GoldFact("call_001", Field.BUDGET, "$50k")],
            transcripts={"call_001": revised},
        )
        assert metrics.spans_checked == 1
        assert metrics.hallucination_rate == pytest.approx(1.0)

    def test_the_rate_cannot_exceed_one(self):
        from eval.extraction_metrics import ExtractionMetrics

        metrics = ExtractionMetrics(
            true_positives=1,
            false_positives=0,
            false_negatives=0,
            spans_judged=0,
            spans_supporting=0,
            quotes_offered=2,
            quotes_located=0,
            spans_not_in_transcript=3,
            spans_checked=2,
        )
        assert metrics.hallucination_rate == 1.0

    def test_unjudged_spans_report_undefined_not_zero(self, transcript):
        # 0.000 reads as "every span was wrong"; this means "nobody looked".
        predicted = {
            "call_001": [made(transcript, Field.BUDGET, "$50k", "s1", "fifty thousand")]
        }
        metrics = evaluate_extraction(predicted, [GoldFact("call_001", Field.BUDGET, "$50k")])
        assert math.isnan(metrics.span_accuracy)
        assert "n/a" in metrics.report()

    def test_per_field_rows_do_not_claim_zero_span_accuracy(self, transcript):
        predicted = {
            "call_001": [made(transcript, Field.BUDGET, "$50k", "s1", "fifty thousand")]
        }
        split = per_field(predicted, [GoldFact("call_001", Field.BUDGET, "$50k")])
        assert math.isnan(split[Field.BUDGET].span_accuracy)


class TestAnUndefinedModelAlphaIsNotATrailingModel:
    def test_a_model_that_agreed_on_everything_is_not_reported_as_behind(self):
        # No variation in the paired ratings means alpha is undefined. Saying
        # "the model trails the ceiling" there is the same mistake
        # krippendorff_alpha returns NaN to avoid.
        human = rater_agreement(
            {
                "c1": {"r1": {"k": 0}, "r2": {"k": 1}},
                "c2": {"r1": {"k": 2}, "r2": {"k": 2}},
                "c3": {"r1": {"k": 3}, "r2": {"k": 3}},
                "c4": {"r1": {"k": 1}, "r2": {"k": 0}},
            }
        )
        labels = {f"c{i}": {"k": 2} for i in range(1, 5)}
        row = measure_against_ceiling(human, labels, labels)["k"]
        assert math.isnan(row.model_alpha)
        assert "undefined" in row.verdict
        assert "trails" not in row.verdict
