"""Reconciling per-chunk extractions (design.md section 6.2).

    "Reconcile: dedupe by (type, normalized_value), union the spans, and flag
    genuine contradictions rather than silently picking one."

The second half is the interesting part:

    "**Surface contradictions rather than resolving them.** If one chunk says
    the budget is $50k and another says $80k, that's usually a real thing that
    happened in the call (the number changed during the conversation), and
    it's more valuable surfaced than silently collapsed."

A pipeline that picks one number is discarding the most interesting fact in
the call. A pipeline that surfaces both, in time order, with both quotes, is
telling the rep something they would otherwise have to re-listen to find.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Sequence

from ..transcript.model import TranscriptSpan
from .grounded import Field, GroundedClaim

#: Fields where two different values are a contradiction worth surfacing
#: rather than two independent facts. "Budget was 50k then 80k" is one
#: changing fact; "competitors were Salesforce and HubSpot" is two facts.
SINGLE_VALUED = frozenset(
    {
        Field.ECONOMIC_BUYER,
        Field.BUDGET,
        Field.TIMELINE,
        Field.CHAMPION,
    }
)


@dataclass(frozen=True)
class Contradiction:
    """Two or more incompatible values for a single-valued field."""

    field: Field
    claims: tuple[GroundedClaim, ...]

    @property
    def values(self) -> tuple[str, ...]:
        return tuple(c.value for c in self.claims)

    def describe(self) -> str:
        parts = " then ".join(
            f"{c.value!r} (at {c.earliest_ms // 1000}s)"
            for c in sorted(self.claims, key=lambda c: c.earliest_ms)
        )
        return f"{self.field.value}: {parts}"


@dataclass(frozen=True)
class Extraction:
    """The reconciled result for a call."""

    call_id: str
    transcript_version: int
    claims: tuple[GroundedClaim, ...]
    contradictions: tuple[Contradiction, ...]
    extractor_version: str

    def by_field(self, field: Field) -> tuple[GroundedClaim, ...]:
        return tuple(c for c in self.claims if c.field is field)

    def has(self, field: Field) -> bool:
        return any(c.field is field for c in self.claims)

    @property
    def contradicted_fields(self) -> frozenset[Field]:
        return frozenset(c.field for c in self.contradictions)


def normalize_value(field: Field, value: str) -> str:
    """Canonical form for deduplication.

    Deliberately conservative. Over-normalizing merges two genuinely different
    values and hides a contradiction, which is the exact failure this module
    exists to avoid -- so it only does things that cannot change meaning:
    case, whitespace, surrounding punctuation, and for money, the format of
    the number.
    """
    text = re.sub(r"\s+", " ", value).strip().strip(".,;:").lower()

    if field is Field.BUDGET:
        return _normalize_money(text)
    if field in {Field.ECONOMIC_BUYER, Field.CHAMPION}:
        # "Dana Chen, VP Finance" and "Dana Chen" are the same person. Take
        # the name before the first comma, which is where a title goes.
        return text.split(",")[0].strip()
    return text


#: A bare digit run is NOT money. With `\d+` optional on both sides, this
#: pattern turns "Q4 budget cycle" into "$4" and "2026 budget" into "$2026",
#: and two unrelated budget claims then normalise to the same string and
#: silently merge -- which is the exact failure this module exists to avoid.
#: So something has to mark it as an amount: a currency symbol, a scale word,
#: or thousands/decimal formatting.
_MONEY_RE = re.compile(
    r"(?P<currency>[$£€])\s*(?P<amount>\d[\d,]*\.?\d*)\s*"
    r"(?P<scale>k|m|thousand|million)?"
    r"|(?P<amount2>\d[\d,]*\.?\d*)\s*(?P<scale2>k|m|thousand|million)\b"
    r"|(?P<amount3>\d{1,3}(?:,\d{3})+(?:\.\d+)?)",
    re.IGNORECASE,
)

#: "50-80k", "fifty to eighty thousand". A range is not a point value, and
#: reducing it to one end of itself either hides a real spread or invents a
#: contradiction with a claim that quoted the other end.
_RANGE_RE = re.compile(r"\d[\d,.]*\s*(?:-|–|to)\s*\d", re.IGNORECASE)

_SCALES = {"k": 1_000, "thousand": 1_000, "m": 1_000_000, "million": 1_000_000}


def _normalize_money(text: str) -> str:
    """Reduce a money phrase to a comparable number.

    "$50k", "50,000", and "fifty thousand dollars" should not all be different
    budgets. The first two normalize; spelled-out numbers do not, and are left
    alone rather than half-parsed -- a wrong parse here invents a
    contradiction, which is worse than missing a duplicate.
    """
    if _RANGE_RE.search(text):
        return text

    matches = list(_MONEY_RE.finditer(text))
    if len(matches) != 1:
        # Zero: nothing here is recognisably an amount, so leave it alone.
        # More than one: this phrase is not a single value, and picking the
        # first would be a guess. Either way the safe answer is the text.
        return text
    match = matches[0]

    raw = match.group("amount") or match.group("amount2") or match.group("amount3")
    try:
        amount = float(raw.replace(",", ""))
    except (AttributeError, ValueError):
        return text

    scale = match.group("scale") or match.group("scale2")
    if scale:
        amount *= _SCALES[scale.lower()]

    currency = match.group("currency") or "$"
    return f"{currency}{amount:.0f}"


def _merge_spans(claims: Sequence[GroundedClaim]) -> tuple[TranscriptSpan, ...]:
    """Union the spans, dropping duplicates from chunk overlap."""
    seen: dict[str, TranscriptSpan] = {}
    for claim in claims:
        for span in claim.spans:
            seen.setdefault(span.span_id, span)
    return tuple(sorted(seen.values(), key=lambda s: (s.start_ms, s.start_char)))


def reconcile(
    call_id: str,
    transcript_version: int,
    partials: Sequence[Sequence[GroundedClaim]],
    *,
    extractor_version: str = "1.0.0",
) -> Extraction:
    """Merge per-chunk claims into one extraction.

    Same value from several chunks becomes one claim with the union of spans.
    Different values for a single-valued field become a contradiction, and
    every value survives -- the caller decides what to show.
    """
    groups: dict[tuple[Field, str], list[GroundedClaim]] = defaultdict(list)
    for chunk_claims in partials:
        for claim in chunk_claims:
            key = (claim.field, normalize_value(claim.field, claim.value))
            groups[key].append(claim)

    merged: list[GroundedClaim] = []
    for (field, _normalized), claims in groups.items():
        spans = _merge_spans(claims)
        # Highest confidence wins for the displayed value; all the evidence is
        # kept. Confidence itself is the max rather than the mean: a claim
        # seen once with high confidence and once with low is not less certain
        # than the high one alone.
        best = max(claims, key=lambda c: c.confidence)
        merged.append(
            GroundedClaim(
                field=field,
                value=best.value,
                spans=spans,
                confidence=max(c.confidence for c in claims),
                extractor_version=best.extractor_version,
                subject_participant_id=best.subject_participant_id,
                source_chunks=tuple(
                    sorted({i for c in claims for i in c.source_chunks})
                ),
            )
        )

    contradictions = _find_contradictions(merged)

    merged.sort(key=lambda c: (c.field.value, c.earliest_ms))
    return Extraction(
        call_id=call_id,
        transcript_version=transcript_version,
        claims=tuple(merged),
        contradictions=contradictions,
        extractor_version=extractor_version,
    )


def _find_contradictions(claims: Sequence[GroundedClaim]) -> tuple[Contradiction, ...]:
    by_field: dict[Field, list[GroundedClaim]] = defaultdict(list)
    for claim in claims:
        if claim.field in SINGLE_VALUED:
            by_field[claim.field].append(claim)

    out: list[Contradiction] = []
    for field, group in sorted(by_field.items(), key=lambda kv: kv[0].value):
        if len(group) > 1:
            out.append(
                Contradiction(
                    field=field,
                    claims=tuple(sorted(group, key=lambda c: c.earliest_ms)),
                )
            )
    return tuple(out)
