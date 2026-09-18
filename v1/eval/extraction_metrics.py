"""Extraction measured objectively (design.md section 8.3).

    "Extraction errors are objective, so evaluate them objectively."

This is the half of the evaluation that can be trusted. Scoring agreement is
capped by how much two humans agree about "champion strength"; extraction has
no such ceiling, because "did the transcript say Priya approves spend over
fifty thousand" has an answer.

    "Span verification is a much cheaper labeling task than full extraction --
    a rater reads a quote and a claim and answers yes or no. You can label
    thousands of these quickly, which means extraction can be measured far
    more precisely than scoring can."

Which is why the four metrics here are separated rather than averaged into an
"extraction quality" number. They fail for different reasons and are fixed by
different work: low precision is a prompt problem, low recall is usually a
chunking problem, low span accuracy is a matching problem, and hallucination
is a model problem.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

from cis.extract.grounded import Field, GroundedClaim
from cis.extract.reconcile import normalize_value
from cis.transcript.model import SpanMismatch, Transcript


@dataclass(frozen=True)
class GoldFact:
    """A fact a human confirmed is in the transcript.

    Deliberately not a GroundedClaim. A gold fact is a statement about the
    call; a claim is a statement the pipeline made. Keeping the types apart
    stops a gold set from being quietly produced by the thing it is supposed
    to be measuring, which is the most common way an evaluation set stops
    measuring anything.
    """

    call_id: str
    field: Field
    value: str
    #: True when the fact is present but a human judged it genuinely
    #: ambiguous. Counted separately: a pipeline that misses these is not
    #: making the same mistake as one that misses the clear ones.
    ambiguous: bool = False

    @property
    def key(self) -> tuple[str, Field, str]:
        return (self.call_id, self.field, normalize_value(self.field, self.value))


@dataclass(frozen=True)
class SpanVerdict:
    """A rater's yes/no on whether a cited span supports its claim.

    The cheap label from section 8.3. One rater, one quote, one claim, one
    answer.
    """

    call_id: str
    field: Field
    value: str
    quote: str
    supports: bool

    @property
    def key(self) -> tuple[str, Field, str]:
        return (self.call_id, self.field, normalize_value(self.field, self.value))


@dataclass(frozen=True)
class ExtractionMetrics:
    true_positives: int
    false_positives: int
    false_negatives: int
    spans_judged: int
    spans_supporting: int
    quotes_offered: int
    quotes_located: int
    spans_not_in_transcript: int
    #: Spans re-validated against a transcript. The denominator for
    #: `hallucination_rate` when no extraction-time quote count was supplied.
    spans_checked: int = 0

    @property
    def precision(self) -> float:
        """Of claims made, how many are supported by their cited span?"""
        made = self.true_positives + self.false_positives
        return self.true_positives / made if made else 0.0

    @property
    def recall(self) -> float:
        """Of facts present in the transcript, how many were found?"""
        present = self.true_positives + self.false_negatives
        return self.true_positives / present if present else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0

    @property
    def span_accuracy(self) -> float:
        """Does the cited span actually support the claim?

        Separate from precision on purpose. A claim can be correct while
        citing the wrong line, and that is a real defect even though the value
        is right: the rep clicks the quote, hears something unrelated, and
        stops trusting every quote in the product.

        NaN when nothing was judged. Returning 0.0 there would read as
        "every span was wrong" when it means "nobody looked", and the two
        belong in different columns of a report.
        """
        if not self.spans_judged:
            return float("nan")
        return self.spans_supporting / self.spans_judged

    @property
    def hallucination_rate(self) -> float:
        """Quotes the model produced that are not in the transcript.

        The headline monitoring number (section 16.1). Counts both quotes
        rejected at extraction time and spans that no longer validate, since
        both mean the same thing to a rep looking at the screen.

        The denominator falls back to the number of spans re-validated when no
        extraction-time quote count was supplied. Without that, the natural
        call -- predictions, gold, and transcripts to re-check against --
        reports 0.0 for the exact case the second half of this metric was
        added to catch.
        """
        denominator = max(self.quotes_offered, self.spans_checked)
        if not denominator:
            return float("nan")
        missed = (
            self.quotes_offered - self.quotes_located
        ) + self.spans_not_in_transcript
        return min(1.0, missed / denominator)

    def report(self) -> str:
        def pct(value: float) -> str:
            return "  n/a" if value != value else f"{value:.3f}"

        return (
            f"precision       {self.precision:.3f}  "
            f"({self.true_positives} of "
            f"{self.true_positives + self.false_positives})\n"
            f"recall          {self.recall:.3f}  "
            f"({self.true_positives} of "
            f"{self.true_positives + self.false_negatives})\n"
            f"f1              {self.f1:.3f}\n"
            f"span accuracy   {pct(self.span_accuracy)}  "
            f"({self.spans_supporting} of {self.spans_judged} judged)\n"
            f"hallucination   {pct(self.hallucination_rate)}  "
            f"({max(self.quotes_offered, self.spans_checked)} quote(s) checked)"
        )


def evaluate_extraction(
    predicted: Mapping[str, Sequence[GroundedClaim]],
    gold: Iterable[GoldFact],
    *,
    transcripts: Mapping[str, Transcript] | None = None,
    span_verdicts: Iterable[SpanVerdict] = (),
    quotes_offered: int = 0,
    quotes_located: int = 0,
) -> ExtractionMetrics:
    """Score a run against a gold set.

    `predicted[call_id]` are the reconciled claims; `gold` are the facts a
    human confirmed. Matching is on (call, field, normalized value), using the
    same normalizer reconciliation uses -- if the two disagreed about whether
    "$50k" and "50,000" are the same budget, the metrics would measure the
    disagreement rather than the pipeline.

    `transcripts`, when supplied, re-validates every span. That catches the
    case a hallucination counter cannot: a span that matched at extraction
    time and no longer matches, because the transcript was revised underneath
    the stored claim.
    """
    gold_keys = {fact.key for fact in gold}
    predicted_keys: set[tuple[str, Field, str]] = set()

    not_in_transcript = 0
    spans_checked = 0
    for call_id, claims in predicted.items():
        transcript = (transcripts or {}).get(call_id)
        for claim in claims:
            predicted_keys.add(
                (call_id, claim.field, normalize_value(claim.field, claim.value))
            )
            if transcript is None:
                continue
            for span in claim.spans:
                spans_checked += 1
                try:
                    transcript.validate_span(span)
                except SpanMismatch:
                    not_in_transcript += 1

    verdicts = tuple(span_verdicts)

    return ExtractionMetrics(
        true_positives=len(predicted_keys & gold_keys),
        false_positives=len(predicted_keys - gold_keys),
        false_negatives=len(gold_keys - predicted_keys),
        spans_judged=len(verdicts),
        spans_supporting=sum(1 for v in verdicts if v.supports),
        quotes_offered=quotes_offered,
        quotes_located=quotes_located,
        spans_not_in_transcript=not_in_transcript,
        spans_checked=spans_checked,
    )


def per_field(
    predicted: Mapping[str, Sequence[GroundedClaim]],
    gold: Iterable[GoldFact],
) -> Mapping[Field, ExtractionMetrics]:
    """The same metrics, split by field.

    The aggregate hides the useful signal here as surely as it does for
    agreement: a pipeline that finds every competitor and no economic buyer
    has a respectable overall F1 and one badly broken field.
    """
    gold_by_field: dict[Field, set[tuple[str, Field, str]]] = defaultdict(set)
    for fact in gold:
        gold_by_field[fact.field].add(fact.key)

    predicted_by_field: dict[Field, set[tuple[str, Field, str]]] = defaultdict(set)
    for call_id, claims in predicted.items():
        for claim in claims:
            predicted_by_field[claim.field].add(
                (call_id, claim.field, normalize_value(claim.field, claim.value))
            )

    out: dict[Field, ExtractionMetrics] = {}
    for field in set(gold_by_field) | set(predicted_by_field):
        got = predicted_by_field[field]
        want = gold_by_field[field]
        out[field] = ExtractionMetrics(
            true_positives=len(got & want),
            false_positives=len(got - want),
            false_negatives=len(want - got),
            spans_judged=0,
            spans_supporting=0,
            quotes_offered=0,
            quotes_located=0,
            spans_not_in_transcript=0,
        )
    return out
