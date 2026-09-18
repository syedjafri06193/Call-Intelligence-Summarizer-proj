"""Scoring against anchors (design.md sections 7.2, 7.3, 7.4).

Three rules, each of which exists to defeat a specific known failure:

**Score from extracted claims, not from the transcript** (7.2). The judge
never receives a transcript. `gather_evidence` reads one, and only to map a
span's segment to the participant who spoke it; `build_prompt` and
`score_criterion` have no parameter for one. That is not an oversight to be
fixed later -- it is the mitigation for verbosity bias, and it is enforced by
the signatures. A longer call cannot score higher here, because length is not
an input.

**The judge is a model, so PII is redacted on the way to it** (10.2). Same
rule as extraction, and for the same reason: the evidence block quotes the
transcript, and a quote can contain an email address. `score_call` takes an
optional redactor and the rationale is restored on the way back.

**Ordinal anchors, not numbers** (7.3). The judge picks a level from a small
written scale. It is never asked for "a score out of 10", because a number
emitted by a language model is a token sequence and not a measurement.

**Nothing that could be agreed with** (7.4, sycophancy). No prior score, no
suggested level, no other rater's opinion reaches the prompt. There is no
parameter through which one could, which is the only version of this rule that
survives contact with a hurried change six months from now.

And one rule that belongs to code rather than to the model:

**Level 0 is assigned here, not chosen there.** "Not discussed" is a fact about
what extraction found, and it is settled before any model is called. When
evidence does exist, 0 is not on the menu -- a judge returning it is returning
a level off the scale, and that raises.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Mapping, Protocol, Sequence

from ..consent.model import Call, ParticipantRole
from ..extract.grounded import GroundedClaim
from ..extract.reconcile import Extraction
from ..transcript.model import Transcript, TranscriptSpan
from .framework import NO_EVIDENCE_LEVEL, Criterion, Framework

logger = logging.getLogger(__name__)

JUDGE_VERSION = "1.0.0"

#: Section 7.4, self-inconsistency: "Fix temperature at 0; measure rerun
#: variance and report it." The first half is here; the second half is
#: eval/consistency.py.
JUDGE_TEMPERATURE = 0.0

NO_EVIDENCE_RATIONALE = "no supporting evidence found"


class InvalidAnchorLevel(ValueError):
    """The judge returned a level that is not on this criterion's scale."""


@dataclass(frozen=True)
class EvidenceItem:
    """One claim, with who said it resolved from the roster.

    `stated_by_role` comes from the call roster by way of the segment the span
    sits in -- platform identity, never voice (section 2.3).
    """

    claim: GroundedClaim
    stated_by: str | None
    stated_by_role: ParticipantRole

    @property
    def quote(self) -> str:
        return self.claim.quote


@dataclass(frozen=True)
class Evidence:
    """Everything the judge is allowed to see about one criterion."""

    criterion_key: str
    items: tuple[EvidenceItem, ...]
    #: Claims that matched the criterion's fields but were ruled out, with the
    #: reason. Surfaced rather than dropped: "we heard a number but the rep
    #: said it" is a different state from "nobody mentioned a number", and a
    #: rep reading the score needs to be able to tell them apart.
    excluded: tuple[tuple[EvidenceItem, str], ...] = ()

    def __bool__(self) -> bool:
        return bool(self.items)

    @property
    def spans(self) -> tuple[TranscriptSpan, ...]:
        seen: dict[str, TranscriptSpan] = {}
        for item in self.items:
            for span in item.claim.spans:
                seen.setdefault(span.span_id, span)
        return tuple(sorted(seen.values(), key=lambda s: (s.start_ms, s.start_char)))

    def render(self, redactor: "TextRedactor | None" = None) -> str:
        """The evidence block, as the judge sees it.

        Quotes and speaker roles only. No timestamps beyond ordering, no
        confidence scores, no transcript surrounding the quote -- everything
        here is something the judge needs to apply the anchor definitions, and
        nothing here is something it could anchor on instead.

        The judge is a model like any other, so when a redactor is supplied
        the quotes are redacted before they reach it. A quote saying who signs
        off on spend is exactly the kind of sentence that carries an email
        address along with it.
        """
        lines: list[str] = []
        for index, item in enumerate(self.items, start=1):
            role = item.stated_by_role.value
            lines.append(f"{index}. [{role}] {_redacted(item.claim.value, redactor)}")
            for span in item.claim.spans:
                lines.append(f'     quote: "{_redacted(span.text, redactor)}"')
        return "\n".join(lines)


def gather_evidence(
    criterion: Criterion,
    claims: Sequence[GroundedClaim],
    transcript: Transcript,
    call: Call,
) -> Evidence:
    """Collect the claims that can support a criterion, and say who said them.

    This is the one place a transcript is touched, and only to map a span's
    segment to the participant who spoke it. No transcript text crosses into
    scoring: what the judge sees is the claim's own quoted span.
    """
    speaker_of = {s.segment_id: s.participant_id for s in transcript.segments}
    role_of: Mapping[str, ParticipantRole] = {
        p.participant_id: p.role for p in call.participants
    }

    wanted = set(criterion.fields)
    items: list[EvidenceItem] = []
    excluded: list[tuple[EvidenceItem, str]] = []

    for claim in claims:
        if claim.field not in wanted:
            continue

        # WHO SAID IT COMES FROM THE TRANSCRIPT, NEVER FROM THE MODEL.
        #
        # `claim.subject_participant_id` is who the claim is *about*, and it
        # is model output. Using it here would let a model defeat the
        # exclusion below by tagging the rep's own ROI number as being about
        # the customer -- which is a natural thing for a model to emit and
        # would score MEDDICC Metrics 3 on vendor marketing. The span sits in
        # a segment, the segment has a platform-supplied speaker, and that is
        # the only thing this is allowed to consult.
        earliest = min(claim.spans, key=lambda s: s.start_ms)
        speaker_id = speaker_of.get(earliest.segment_id)

        role = role_of.get(speaker_id or "", ParticipantRole.UNKNOWN)
        item = EvidenceItem(
            claim=claim, stated_by=speaker_id, stated_by_role=role
        )

        if criterion.prospect_stated_only and role is not ParticipantRole.PROSPECT:
            # Section 7.1: "Vendor-asserted ROI does not count." An unknown
            # speaker is excluded for the same reason a rep is: the criterion
            # requires the prospect to have said it, and "we cannot tell who
            # said this" does not establish that. Default deny here too.
            reason = (
                "stated by the rep, and this criterion requires the prospect"
                if role is ParticipantRole.REP
                else "speaker could not be established from the roster"
            )
            excluded.append((item, reason))
            continue

        items.append(item)

    return Evidence(
        criterion_key=criterion.key,
        items=tuple(items),
        excluded=tuple(excluded),
    )


@dataclass(frozen=True)
class CriterionScore:
    """One criterion's level, with the evidence that produced it."""

    criterion_key: str
    level: int
    rationale: str
    spans: tuple[TranscriptSpan, ...]
    framework_id: str
    #: Section 8.1: judgment criteria always go to a human, factual ones can
    #: be written automatically. Decided by the framework file, which sets it
    #: from measured inter-rater agreement.
    requires_review: bool
    judge_version: str = JUDGE_VERSION
    #: Claims that matched the fields but were ruled out, with reasons. Shown
    #: in the UI next to the score.
    excluded: tuple[str, ...] = ()

    @property
    def has_evidence(self) -> bool:
        return self.level > NO_EVIDENCE_LEVEL

    @property
    def quote(self) -> str:
        return " ... ".join(s.text for s in self.spans)


@dataclass(frozen=True)
class CallScore:
    """A whole call scored against one version of one framework.

    There is deliberately no overall number. Summing ordinals produces
    something that looks like a measurement and is not one -- the distance
    from 1 to 2 on "champion" is not the distance from 1 to 2 on "metrics",
    and adding them asserts that it is. The per-criterion levels are the
    result; any roll-up is a product decision made downstream, in the open.
    """

    call_id: str
    framework_id: str
    transcript_version: int
    extractor_version: str
    scores: tuple[CriterionScore, ...]
    judge_version: str = JUDGE_VERSION
    temperature: float = JUDGE_TEMPERATURE

    def __getitem__(self, key: str) -> CriterionScore:
        for score in self.scores:
            if score.criterion_key == key:
                return score
        raise KeyError(key)

    @property
    def levels(self) -> Mapping[str, int]:
        return {s.criterion_key: s.level for s in self.scores}

    @property
    def needs_review(self) -> tuple[CriterionScore, ...]:
        return tuple(s for s in self.scores if s.requires_review)

    @property
    def auto_writable(self) -> tuple[CriterionScore, ...]:
        return tuple(s for s in self.scores if not s.requires_review)

    @property
    def coverage(self) -> float:
        """Fraction of criteria with any evidence at all.

        The most useful single number about a discovery call, and the one the
        rep can act on: it says what was not covered, which is a thing to go
        and ask about.
        """
        if not self.scores:
            return 0.0
        return sum(1 for s in self.scores if s.has_evidence) / len(self.scores)


class TextRedactor(Protocol):
    """Just enough of `redact.pii.Redactor`. See `extract.grounded`."""

    def redact(self, text: str): ...

    def restore(self, text: str) -> str: ...


def _redacted(text: str, redactor: "TextRedactor | None") -> str:
    return redactor.redact(text).text if redactor else text


class AnchorJudge(Protocol):
    """The model boundary for scoring.

    It receives a string and returns a level and a rationale. Everything that
    could bias it -- the transcript, the call's length, a previous score, a
    human's score -- is on this side of the boundary and does not cross it.
    """

    def choose_level(self, prompt: str) -> dict:
        """Return {"level": int, "rationale": str}."""
        ...


JUDGE_PREAMBLE = """\
You are assigning a level on a written ordinal scale for one criterion of a \
sales qualification framework.

Choose the highest level whose definition is fully satisfied by the evidence \
below. If the evidence only partly satisfies a level, that level is not \
satisfied; choose the one below it.

Judge only the evidence given. Do not infer what was probably meant, do not \
draw on what usually happens on calls like this, and do not reward the \
evidence for being lengthy or detailed beyond what the level requires.

Reply with the level number and one sentence saying which piece of evidence \
decided it.
"""


def build_prompt(
    criterion: Criterion,
    evidence: Evidence,
    redactor: "TextRedactor | None" = None,
) -> str:
    """Render the judge prompt.

    The signature is the specification. There is no parameter for a prior
    score, a suggested level, another rater's answer, or the transcript,
    because section 7.4 lists exactly those as the things a judge will agree
    with or be biased by. A prompt that cannot contain them cannot leak them.
    """
    if not evidence.items:
        raise ValueError(
            "build_prompt called with no evidence. Level 0 is assigned by "
            "code, without a model (section 7.2)."
        )

    levels = ", ".join(
        str(level) for level in sorted(criterion.anchors) if level != NO_EVIDENCE_LEVEL
    )
    return (
        f"{JUDGE_PREAMBLE}\n"
        f"CRITERION: {criterion.label}\n"
        f"DEFINITION: {criterion.definition}\n"
        f"EVIDENCE REQUIRED: {criterion.evidence_requires}\n"
        f"\nSCALE:\n{criterion.describe_anchors()}\n"
        f"\nLevel {NO_EVIDENCE_LEVEL} has already been ruled out: evidence was "
        f"found. Choose from {levels}.\n"
        f"\nEVIDENCE:\n{evidence.render(redactor)}\n"
    )


def score_criterion(
    criterion: Criterion,
    evidence: Evidence,
    judge: AnchorJudge,
    *,
    framework_id: str,
    redactor: "TextRedactor | None" = None,
) -> CriterionScore:
    """Assign one criterion's level."""
    excluded_notes = tuple(
        f"{item.claim.value!r}: {reason}" for item, reason in evidence.excluded
    )

    if not evidence.items:
        # No model call. Section 7.2's sketch returns level 0 with "no
        # supporting evidence found" before doing anything else, and asking a
        # model to confirm an absence is both a cost and an invitation to
        # invent one.
        rationale = NO_EVIDENCE_RATIONALE
        if excluded_notes:
            rationale = (
                f"{NO_EVIDENCE_RATIONALE} ({len(excluded_notes)} mention(s) "
                "found but ruled out; see excluded)"
            )
        return CriterionScore(
            criterion_key=criterion.key,
            level=NO_EVIDENCE_LEVEL,
            rationale=rationale,
            spans=(),
            framework_id=framework_id,
            requires_review=criterion.requires_human_review,
            excluded=excluded_notes,
        )

    raw = judge.choose_level(build_prompt(criterion, evidence, redactor))

    try:
        level = int(raw["level"])
    except (KeyError, TypeError, ValueError):
        raise InvalidAnchorLevel(
            f"{criterion.key}: judge returned {raw!r}, which has no integer "
            "`level`. Section 7.3: the judge picks an anchor level, not a "
            "free-form score."
        ) from None

    if level not in criterion.anchors:
        raise InvalidAnchorLevel(
            f"{criterion.key}: judge returned level {level}, which is not on "
            f"the scale (0..{criterion.max_level})"
        )
    if level == NO_EVIDENCE_LEVEL:
        # Evidence exists, so "not discussed" is false as a matter of record.
        # A judge that wants to say the evidence is weak has level 1 for that.
        raise InvalidAnchorLevel(
            f"{criterion.key}: judge returned level {NO_EVIDENCE_LEVEL} "
            "('not discussed') while evidence exists. Level 0 is a statement "
            "about what extraction found and is assigned by code."
        )

    rationale = str(raw.get("rationale") or "").strip()
    if rationale and redactor is not None:
        # The judge quotes the evidence it was shown, so its rationale comes
        # back carrying placeholders. Restored here, for the rep who is
        # entitled to read it.
        rationale = redactor.restore(rationale)
    if not rationale:
        rationale = f"level {level}: {criterion.anchor(level)}"

    return CriterionScore(
        criterion_key=criterion.key,
        level=level,
        rationale=rationale,
        # The score inherits the extraction's spans, so it is grounded for
        # free (section 7.2). Clicking the score plays the audio.
        spans=evidence.spans,
        framework_id=framework_id,
        requires_review=criterion.requires_human_review,
        excluded=excluded_notes,
    )


def score_call(
    extraction: Extraction,
    framework: Framework,
    judge: AnchorJudge,
    transcript: Transcript,
    call: Call,
    *,
    redactor: "TextRedactor | None" = None,
) -> CallScore:
    """Score a call against every criterion in a framework.

    `transcript` is here to resolve which participant spoke a span, and for
    nothing else: see `gather_evidence`. Its text never reaches the judge.
    """
    if extraction.transcript_version != transcript.version:
        raise ValueError(
            f"extraction is against transcript v{extraction.transcript_version} "
            f"but transcript is v{transcript.version}. Re-extract before "
            "scoring: a score grounded in spans from an older version is "
            "grounded in text that may no longer be there."
        )

    scores = tuple(
        score_criterion(
            criterion,
            gather_evidence(criterion, extraction.claims, transcript, call),
            judge,
            framework_id=framework.framework_id,
            redactor=redactor,
        )
        for criterion in framework.criteria
    )

    return CallScore(
        call_id=extraction.call_id,
        framework_id=framework.framework_id,
        transcript_version=extraction.transcript_version,
        extractor_version=extraction.extractor_version,
        scores=scores,
    )
