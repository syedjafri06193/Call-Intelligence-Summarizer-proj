"""The pipeline, end to end (design.md section 13.2).

    "Note the ordering: the consent gate returns *before* `fetch_audio`. That
    ordering is the legal control, and it's worth a comment in the code saying
    so."

So here is the comment. `run()` evaluates consent and returns before anything
touches audio. Not before transcription, not before storage -- before the
fetch. Section 3.2: "If you have downloaded the recording, you have already
arguably intercepted it."

This is written as a plain function rather than as a Temporal workflow, and
that is a deliberate scope decision rather than a disagreement with 13.2.
Every step here is already a pure function of its inputs with its failure
modes as exceptions, which is what makes it mechanically translatable into
activities; wiring a durable engine into v1 would add an operational
dependency to a repository whose point is the decisions above it.
`PipelineResult` carries the per-stage outcome a workflow engine would
otherwise give you for free, so a failed call is visible either way.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field as dc_field
from enum import Enum
from typing import Protocol, Sequence

from ..asr.vocabulary import VocabularyHints, vocabulary_hints
from ..consent.gate import ConsentPolicy, ProcessingDecision, may_process
from ..consent.model import Call, ConsentLedger
from ..crm.tasks import TASK_FIELDS, ProposedTask, propose_tasks
from ..extract.chunk import chunk_by_turns
from ..extract.grounded import (
    ExtractionModel,
    ExtractionStats,
    Field,
    extract_chunk,
)
from ..extract.reconcile import Extraction, reconcile
from ..ingest.tracks import AudioTrack, IngestResult, NoSpeakerAttribution, PlatformClient, ingest
from ..redact.pii import Redactor, roster_names
from ..score.anchors import AnchorJudge, CallScore, score_call
from ..score.framework import Framework
from ..transcript.model import Transcript

logger = logging.getLogger(__name__)


class Stage(str, Enum):
    CONSENT = "consent"
    INGEST = "ingest"
    TRANSCRIBE = "transcribe"
    REDACT = "redact"
    EXTRACT = "extract"
    SCORE = "score"
    STAGE_FOR_REVIEW = "stage_for_review"


class Outcome(str, Enum):
    COMPLETED = "completed"
    #: Consent said no. Not a failure: the system did its job.
    SKIPPED = "skipped"
    FAILED = "failed"


class Transcriber(Protocol):
    """The ASR boundary.

    Takes tracks and hints, returns a transcript with word-level timings.
    Section 5.3: word-level timestamps are not optional, because grounding
    depends on them, so a transcriber that cannot supply them cannot be used
    here -- which `_require_word_timings` turns from a sentence into a check.
    """

    def transcribe(
        self, tracks: Sequence[AudioTrack], hints: VocabularyHints
    ) -> Transcript:
        ...


@dataclass
class PipelineResult:
    call_id: str
    outcome: Outcome
    reason: str = ""
    failed_stage: Stage | None = None
    decision: ProcessingDecision | None = None
    ingest_result: IngestResult | None = None
    transcript: Transcript | None = None
    extraction: Extraction | None = None
    score: CallScore | None = None
    tasks: tuple[ProposedTask, ...] = ()
    stats: ExtractionStats = dc_field(default_factory=ExtractionStats)
    redactions: int = 0
    warnings: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.outcome is Outcome.COMPLETED

    @property
    def needs_review(self) -> tuple:
        """Everything a human has to look at before it leaves the product."""
        judgment = self.score.needs_review if self.score else ()
        return tuple(judgment) + self.tasks


def default_fields(framework: Framework) -> tuple[Field, ...]:
    """What to extract when the caller does not say.

    The framework's own fields are not enough. MEDDICC scores seven criteria
    and none of them is a commitment, so a pipeline that extracted only what
    the framework scores would never produce a task -- and next steps are
    section 9.3's "highest-value output". Budget and timeline are here for the
    same reason: MEDDICC does not score them, the CRM still wants them.
    """
    return tuple(
        sorted(
            frozenset(framework.fields_used)
            | frozenset(TASK_FIELDS)
            | {Field.BUDGET, Field.TIMELINE, Field.OBJECTION},
            key=lambda f: f.value,
        )
    )


class MissingWordTimings(RuntimeError):
    """A transcript arrived without word-level timings (section 5.3)."""


def _require_word_timings(transcript: Transcript) -> None:
    missing = [s.segment_id for s in transcript.segments if not s.words]
    if missing:
        raise MissingWordTimings(
            f"{len(missing)} segment(s) have no word timings, starting at "
            f"{missing[0]!r}. Grounding needs a character-and-time range per "
            "claim, and that can only be built from word-level alignment "
            "(section 5.3). Whisper's native timestamps are segment-level; "
            "use forced alignment or a recogniser that returns word timings."
        )


def run(
    call: Call,
    ledger: ConsentLedger,
    client: PlatformClient,
    transcriber: Transcriber,
    model: ExtractionModel,
    judge: AnchorJudge,
    framework: Framework,
    *,
    policy: ConsentPolicy | None = None,
    fields: Sequence[Field] | None = None,
    #: Chunking is tuned per model: a larger context window does not make
    #: a single pass safe (section 6.2's 'lost in the middle'), but it does
    #: move where the useful boundary sits.
    target_tokens: int = 2_500,
    overlap_turns: int = 3,
    now: dt.datetime | None = None,
    redact_before_model: bool = True,
    require_attribution: bool = True,
) -> PipelineResult:
    """Process one call.

    Returns rather than raising for the ordinary refusals -- no consent, no
    speaker attribution -- because they are outcomes the product has to
    display, not errors to page someone about.
    """
    result = PipelineResult(call_id=call.call_id, outcome=Outcome.FAILED)

    # ---------------------------------------------------------------------
    # THE CONSENT GATE. This returns before `client` is touched. That
    # ordering is the legal control (sections 3.2, 13.2), and nothing below
    # may be reordered above it.
    # ---------------------------------------------------------------------
    decision = may_process(call, ledger, policy, now=now)
    result.decision = decision
    if not decision.allowed:
        result.outcome = Outcome.SKIPPED
        result.failed_stage = Stage.CONSENT
        result.reason = decision.reason
        logger.info("call %s skipped at the consent gate: %s", call.call_id, decision.reason)
        return result

    try:
        ingested = ingest(
            call, client, decision, require_attribution=require_attribution
        )
    except NoSpeakerAttribution as exc:
        result.outcome = Outcome.SKIPPED
        result.failed_stage = Stage.INGEST
        result.reason = str(exc)
        return result
    result.ingest_result = ingested
    result.warnings += ingested.warnings

    hints = vocabulary_hints(call)

    try:
        transcript = transcriber.transcribe(ingested.tracks, hints)
        _require_word_timings(transcript)
    except Exception as exc:  # noqa: BLE001 -- recorded, not swallowed
        result.failed_stage = Stage.TRANSCRIBE
        result.reason = f"{type(exc).__name__}: {exc}"
        return result
    result.transcript = transcript

    # Redaction happens before the transcript reaches a hosted model
    # (section 10.2). The restoration map stays here; what crosses the
    # boundary is the redacted text.
    redactor = Redactor(
        names=roster_names(p.display_name for p in call.participants)
    )
    outbound = redactor if redact_before_model else None

    attributed = not ingested.unattributed
    wanted = tuple(fields) if fields else default_fields(framework)

    try:
        partials = []
        for chunk in chunk_by_turns(
            transcript, target_tokens=target_tokens, overlap_turns=overlap_turns
        ):
            claims, stats = extract_chunk(
                chunk,
                transcript,
                model,
                wanted,
                attributed=attributed,
                redactor=outbound,
            )
            partials.append(claims)
            result.stats.merge(stats)
        extraction = reconcile(call.call_id, transcript.version, partials)
        result.redactions = len(redactor.restoration)
    except Exception as exc:  # noqa: BLE001
        result.failed_stage = Stage.EXTRACT
        result.reason = f"{type(exc).__name__}: {exc}"
        return result
    result.extraction = extraction

    try:
        result.score = score_call(
            extraction, framework, judge, transcript, call, redactor=outbound
        )
    except Exception as exc:  # noqa: BLE001
        result.failed_stage = Stage.SCORE
        result.reason = f"{type(exc).__name__}: {exc}"
        return result

    result.tasks = propose_tasks(extraction.claims, call, attributed=attributed)

    if extraction.contradictions:
        result.warnings += tuple(
            f"contradiction: {c.describe()}" for c in extraction.contradictions
        )
    if result.stats.hallucination_rate > 0:
        result.warnings += (
            f"hallucination rate {result.stats.hallucination_rate:.1%} "
            f"({result.stats.quotes_offered} quotes offered)",
        )

    result.outcome = Outcome.COMPLETED
    return result
