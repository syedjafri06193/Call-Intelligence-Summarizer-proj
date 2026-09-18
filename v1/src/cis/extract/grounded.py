"""Span-grounded extraction (design.md sections 6.3, 6.4, 16.1).

    "This is the most important design decision in the extraction layer."

Every claim cites a transcript span. A claim with no span is discarded, not
shown. That single rule does three jobs at once:

**Anti-hallucination.** A fabricated economic buyer has no span to point at.
Requiring the model to quote makes fabrication structurally harder and
mechanically detectable.

**Trust.** A rep verifies in two seconds by clicking the quote and hearing the
audio. That is what makes the product credible in a way a confident paragraph
never is.

**Evaluation.** You can check whether the cited span supports the claim -- a
much cheaper labelling task than producing the claim from scratch, so
extraction can be measured far more precisely than scoring can.

The verification step is what makes the prompt instruction binding rather than
aspirational. A model told to quote will sometimes not quote; the check is
what turns that from a silent failure into a caught one.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol, Sequence

from ..transcript.model import SpanMismatch, Transcript, TranscriptSpan
from .chunk import Chunk

logger = logging.getLogger(__name__)

EXTRACTOR_VERSION = "1.0.0"


class Field(str, Enum):
    """What extraction pulls out (section 6.1).

    All of these are checkable against the transcript. Judgment lives in
    scoring, separately, because "extraction errors are checkable and fixable
    [while] scoring disagreements are often legitimate. Conflating them means
    you can't tell which is broken."
    """

    ECONOMIC_BUYER = "economic_buyer"
    METRIC = "metric"
    BUDGET = "budget"
    TIMELINE = "timeline"
    DECISION_PROCESS = "decision_process"
    DECISION_CRITERIA = "decision_criteria"
    PAIN = "pain"
    CHAMPION = "champion"
    COMPETITOR = "competitor"
    COMMITMENT = "commitment"
    NEXT_STEP = "next_step"
    OBJECTION = "objection"


#: Fields that assert who said or promised something. They are refused on an
#: unattributed transcript: section 4.3, "A transcript that silently
#: mis-attributes a commitment is worse than one that says 'speaker unknown.'"
ATTRIBUTION_DEPENDENT = frozenset(
    {
        Field.ECONOMIC_BUYER,
        Field.CHAMPION,
        Field.COMMITMENT,
        Field.NEXT_STEP,
    }
)


class UngroundedClaim(ValueError):
    """A claim arrived with no supporting span."""


@dataclass(frozen=True)
class GroundedClaim:
    """A fact, and the transcript text that supports it.

    `spans` is never empty. That is enforced in `__post_init__` rather than by
    convention, because the whole argument rests on it being structurally
    impossible to hold an ungrounded claim.
    """

    field: Field
    value: str
    spans: tuple[TranscriptSpan, ...]
    confidence: float
    extractor_version: str = EXTRACTOR_VERSION
    #: The participant this claim is about, where the field implies one.
    subject_participant_id: str | None = None
    #: Chunk indices this claim came from. Used by reconciliation to tell a
    #: duplicate from the overlap apart from two genuine mentions.
    source_chunks: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if not self.spans:
            raise UngroundedClaim(
                f"{self.field.value} has no supporting span. A claim with no "
                "span is discarded, not shown (design.md section 6.3)."
            )
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence {self.confidence} outside [0,1]")

    @property
    def quote(self) -> str:
        """The evidence, as the rep will see it."""
        return " ... ".join(s.text for s in self.spans)

    @property
    def earliest_ms(self) -> int:
        return min(s.start_ms for s in self.spans)


@dataclass
class ExtractionStats:
    """Hallucination monitoring (section 16.1).

    "The `logger.warning` on a discarded quote is your hallucination monitor.
    Track its rate over time -- a rising rate after a model change is a
    regression signal you'd otherwise miss entirely."
    """

    fields_returned: int = 0
    claims_accepted: int = 0
    quotes_offered: int = 0
    quotes_located: int = 0
    claims_dropped_ungrounded: int = 0
    claims_dropped_unattributed: int = 0
    dropped_quotes: list[tuple[str, str]] = field(default_factory=list)

    @property
    def hallucination_rate(self) -> float:
        """Fraction of offered quotes that were not in the transcript.

        The headline number. Zero is the target; a rise after a model or
        prompt change is the signal.
        """
        if not self.quotes_offered:
            return 0.0
        return 1.0 - (self.quotes_located / self.quotes_offered)

    def merge(self, other: "ExtractionStats") -> None:
        self.fields_returned += other.fields_returned
        self.claims_accepted += other.claims_accepted
        self.quotes_offered += other.quotes_offered
        self.quotes_located += other.quotes_located
        self.claims_dropped_ungrounded += other.claims_dropped_ungrounded
        self.claims_dropped_unattributed += other.claims_dropped_unattributed
        self.dropped_quotes.extend(other.dropped_quotes)


class TextRedactor(Protocol):
    """Just enough of `redact.pii.Redactor` to be swapped or omitted.

    Declared as a Protocol rather than imported so that the extraction layer
    does not depend on the redaction layer. The direction of the dependency
    matters: redaction is a deployment choice (are you sending this to a
    hosted model?), and extraction should not know the answer.
    """

    def redact(self, text: str): ...

    def restore(self, text: str) -> str: ...


class ExtractionModel(Protocol):
    """The LLM boundary.

    Deliberately narrow. Everything interesting -- the span validation, the
    reconciliation, the attribution rules -- happens on this side of it, which
    is what makes those parts testable without a model and what keeps the
    behaviour the same whichever model is behind it.
    """

    def extract(self, chunk_text: str, fields: Sequence[Field]) -> dict:
        """Return {field_name: {"value": str, "quotes": [str], "confidence": float}}.

        Returns None or omits a field it cannot support. The prompt
        (GROUNDED_EXTRACTION_PROMPT) instructs it to; the validation below is
        what makes that binding.
        """
        ...


#: Section 6.4. The instruction and the verification are a pair -- the
#: instruction alone is aspirational.
GROUNDED_EXTRACTION_PROMPT = """\
You are extracting facts from a sales discovery call transcript.

For each field, output:
  - the exact verbatim quote(s) from the transcript that support it
  - the value you derive from those quotes

If no quote supports a field, output null. Do not infer, do not generalize
from context, and do not use outside knowledge. A field with no supporting
quote must be null.

Quotes must be copied character for character from the transcript. Do not
paraphrase, do not correct grammar, do not expand contractions, and do not
merge two separate statements into one quote.
"""


def extract_chunk(
    chunk: Chunk,
    transcript: Transcript,
    model: ExtractionModel,
    fields: Sequence[Field],
    *,
    attributed: bool = True,
    redactor: TextRedactor | None = None,
) -> tuple[tuple[GroundedClaim, ...], ExtractionStats]:
    """Extract from one chunk, discarding anything that is not grounded.

    When a `redactor` is supplied, the model sees redacted text and its quotes
    are restored before they are located. That ordering is what lets both
    requirements hold at once: section 10.2 says the hosted model must not see
    the PII, and section 6.3 says every span must quote the real transcript.
    Redact outbound, restore inbound, and the span still points at the words
    that were actually said.
    """
    stats = ExtractionStats()
    text = redactor.redact(chunk.text).text if redactor else chunk.text
    raw = model.extract(text, fields)
    claims: list[GroundedClaim] = []

    for field_name, payload in raw.items():
        if payload is None:
            continue
        stats.fields_returned += 1

        try:
            field_enum = Field(field_name)
        except ValueError:
            logger.warning("model returned unknown field %r -- ignoring", field_name)
            continue

        # An attribution-dependent field on an unattributed transcript is
        # refused before it can be evaluated. There is no way to say who
        # committed to something from a transcript with no speakers, and
        # producing one anyway is the failure section 4.3 names.
        if not attributed and field_enum in ATTRIBUTION_DEPENDENT:
            stats.claims_dropped_unattributed += 1
            logger.info(
                "dropping %s: transcript is unattributed and this field "
                "asserts who said or promised something",
                field_enum.value,
            )
            continue

        spans: list[TranscriptSpan] = []
        for quote in payload.get("quotes", ()):
            stats.quotes_offered += 1
            if redactor is not None:
                quote = redactor.restore(quote)
            located = chunk.locate(quote)
            if located is None:
                # THE HALLUCINATION MONITOR. This log line is the signal.
                stats.dropped_quotes.append((field_enum.value, quote[:120]))
                logger.warning(
                    "quote not found in transcript -- discarding. field=%s quote=%r",
                    field_enum.value,
                    quote[:80],
                )
                continue
            stats.quotes_located += 1
            spans.append(located)

        if not spans:
            stats.claims_dropped_ungrounded += 1
            continue

        value = payload.get("value")
        if not value:
            stats.claims_dropped_ungrounded += 1
            continue

        claims.append(
            GroundedClaim(
                field=field_enum,
                value=str(value),
                spans=tuple(spans),
                confidence=float(payload.get("confidence", 0.5)),
                subject_participant_id=payload.get("subject_participant_id"),
                source_chunks=(chunk.index,),
            )
        )
        stats.claims_accepted += 1

    return tuple(claims), stats


def validate(claim: GroundedClaim, transcript: Transcript) -> None:
    """Raise unless every span still quotes the transcript exactly.

    Run before a claim is stored and again before it is displayed. The second
    check is not redundant: a transcript revision between extraction and
    display is exactly when a stored claim quietly stops matching, and showing
    a stale quote is worse than showing nothing because it looks verified.
    """
    if not claim.spans:
        raise UngroundedClaim(f"{claim.field.value} has no supporting span")
    for span in claim.spans:
        transcript.validate_span(span)


def validate_all(
    claims: Sequence[GroundedClaim], transcript: Transcript
) -> tuple[tuple[GroundedClaim, ...], tuple[tuple[GroundedClaim, str], ...]]:
    """Partition claims into valid and rejected, with reasons.

    Returns rather than raises, because at display time one bad claim should
    not hide the other fifteen.
    """
    good: list[GroundedClaim] = []
    bad: list[tuple[GroundedClaim, str]] = []
    for claim in claims:
        try:
            validate(claim, transcript)
        except (SpanMismatch, UngroundedClaim) as exc:
            bad.append((claim, str(exc)))
        else:
            good.append(claim)
    return tuple(good), tuple(bad)
