"""CRM writeback (design.md sections 9.1, 9.2, 9.5).

    "This is where trust is won or lost, permanently."

    "A model that clobbers a rep's carefully written note has ended the
    product's credibility for that rep and everyone they talk to."

Three defences, in order of how much they are worth:

**A dedicated AI field cannot collide.** `AI_Summary__c` has never held a
human's writing, so the question of overwriting one does not arise. Section
9.1 calls it the best default and it is; everything below is for the fields
where that option was not taken.

**Human content is never replaced.** Checked before every write, and the
fallback is a suggestion rather than a silent skip -- the rep should see what
the pipeline would have written. The single exception is an append policy,
which adds below the human's text with an attribution marker and replaces
none of it.

**Auto-write is earned, per field, with a number.** `FieldPolicy` will not
construct in `auto` mode without a measured precision from section 8.3 that
clears the floor. "It seems good" cannot be typed into this dataclass.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field as dc_field
from enum import Enum
from typing import Any, Callable, Mapping, Sequence

from ..extract.grounded import GroundedClaim
from .idempotency import CRM, Provenance, identity_key, upsert

logger = logging.getLogger(__name__)

#: Precision below which a field may not be written automatically, however
#: confident an individual claim looks. From section 8.3's measurement, not
#: from the model's own confidence, which measures nothing across calls.
AUTO_PRECISION_FLOOR = 0.95

#: Naming convention for fields that exist only for this pipeline. Section
#: 9.1: "Zero collision risk. Best default."
AI_FIELD_PATTERN = ("ai_", "AI_")


class WriteMode(str, Enum):
    AUTO = "auto"
    REVIEW = "review"
    SUGGEST_ONLY = "suggest_only"


class WriteAction(str, Enum):
    WRITTEN = "written"
    APPENDED = "appended"
    SUGGESTED = "suggested"
    SKIPPED = "skipped"
    FAILED = "failed"


class PolicyError(ValueError):
    """A field policy that cannot be honoured."""


@dataclass(frozen=True)
class FieldPolicy:
    """Section 9.2's policy, with the promotion rule enforced.

    "Nothing goes to `auto` without a precision number behind it. 'It seems
    good' is not a threshold." So the threshold is checked in
    `__post_init__`: a policy that claims auto without the evidence does not
    exist, rather than existing and being caught by a review that may not
    happen.
    """

    field: str
    mode: WriteMode
    min_confidence: float = 0.0
    requires_span: bool = True
    #: From section 8.3, per field. None means never measured.
    measured_precision: float | None = None
    #: Append rather than replace, with attribution (section 9.1, pattern 2).
    append: bool = False

    def __post_init__(self) -> None:
        if not 0.0 <= self.min_confidence <= 1.0:
            raise PolicyError(f"{self.field}: min_confidence outside [0,1]")
        if self.mode is not WriteMode.AUTO:
            return
        if self.measured_precision is None:
            raise PolicyError(
                f"{self.field}: mode=auto requires a measured_precision from "
                "the evaluation set (section 8.3). Nothing goes to auto "
                "without a precision number behind it."
            )
        if self.measured_precision < AUTO_PRECISION_FLOOR:
            raise PolicyError(
                f"{self.field}: measured precision {self.measured_precision:.3f} "
                f"is below the auto-write floor of {AUTO_PRECISION_FLOOR}. "
                "Use mode=review until it clears."
            )

    @property
    def is_dedicated_ai_field(self) -> bool:
        return self.field.startswith(AI_FIELD_PATTERN)


@dataclass(frozen=True)
class WriteResult:
    action: WriteAction
    field: str
    reason: str
    #: What would have been written, when it was not.
    suggestion: str | None = None
    record_id: str | None = None
    #: Spans backing the value, shown wherever the value is shown.
    spans: tuple = ()

    @property
    def landed(self) -> bool:
        return self.action in {WriteAction.WRITTEN, WriteAction.APPENDED}


def attribution_marker(call_id: str) -> str:
    """The marker on appended content (section 9.1, pattern 2).

    Includes the call id so the note links back to its source. A note that
    says "AI generated" and nothing else tells a rep that they cannot trust it
    without telling them how to check it.
    """
    return f"[AI note from call {call_id}]"


def write_field(
    crm: CRM,
    record_id: str,
    field: str,
    value: str,
    claim: GroundedClaim | None,
    policy: FieldPolicy,
    *,
    call_id: str = "",
) -> WriteResult:
    """Write one field, or explain why it was not written.

    Every path that does not write returns a suggestion. A silent skip leaves
    the rep believing the pipeline found nothing, when in fact it found
    something and declined to act -- a different thing, and one they would
    want to see.
    """
    if policy.requires_span and (claim is None or not claim.spans):
        return WriteResult(
            WriteAction.SKIPPED,
            field,
            "no supporting span, and this field requires one",
            suggestion=value,
        )

    confidence = claim.confidence if claim else 0.0
    if confidence < policy.min_confidence:
        return WriteResult(
            WriteAction.SUGGESTED,
            field,
            f"confidence {confidence:.2f} below the {policy.min_confidence:.2f} "
            "threshold for this field",
            suggestion=value,
            spans=claim.spans if claim else (),
        )

    current = crm.get_field(record_id, field)
    # A dedicated AI field has never held a human's writing, so the question
    # of overwriting one does not arise (section 9.1, pattern 1: "zero
    # collision risk, best default"). Checking the convention here is also
    # what lets a CRM with no provenance API auto-write safely.
    human_written = (
        bool(current)
        and not policy.is_dedicated_ai_field
        and not crm.is_ai_authored(record_id, field)
    )

    if human_written and not policy.append:
        # A human wrote this. Never replace it -- not on a higher confidence,
        # not on a newer model, not ever.
        return WriteResult(
            WriteAction.SUGGESTED,
            field,
            "field contains human-entered content",
            suggestion=value,
            record_id=record_id,
            spans=claim.spans if claim else (),
        )

    if policy.mode is WriteMode.SUGGEST_ONLY:
        return WriteResult(
            WriteAction.SUGGESTED,
            field,
            "field is suggest-only",
            suggestion=value,
            record_id=record_id,
            spans=claim.spans if claim else (),
        )

    if policy.mode is WriteMode.REVIEW:
        return WriteResult(
            WriteAction.SUGGESTED,
            field,
            "field is in review mode; a human accepts or edits before it lands",
            suggestion=value,
            record_id=record_id,
            spans=claim.spans if claim else (),
        )

    if policy.append:
        # Append is the one mode allowed to touch human-written content,
        # because it does not replace any of it -- that is the whole of
        # section 9.1's second pattern. The human's text stays exactly where
        # it was and the addition is marked as ours.
        marker = attribution_marker(call_id)
        body = f"{current}\n\n{marker}\n{value}" if current else f"{marker}\n{value}"
        crm.update(record_id, {field: body})
        return WriteResult(
            WriteAction.APPENDED,
            field,
            "appended with attribution",
            record_id=record_id,
            spans=claim.spans if claim else (),
        )

    crm.update(record_id, {field: value})
    return WriteResult(
        WriteAction.WRITTEN,
        field,
        "auto-write policy, measured precision "
        f"{policy.measured_precision:.3f}",
        record_id=record_id,
        spans=claim.spans if claim else (),
    )


# ------------------------------------------------- failure must be visible


class RateLimited(RuntimeError):
    """The CRM asked us to slow down. Carries the hint if it gave one."""

    def __init__(self, retry_after: float = 1.0) -> None:
        self.retry_after = retry_after
        super().__init__(f"rate limited; retry after {retry_after}s")


@dataclass
class JournalEntry:
    key: str
    payload: Mapping[str, Any]
    attempts: int = 0
    last_error: str | None = None
    done: bool = False

    @property
    def failed(self) -> bool:
        return not self.done and self.attempts > 0


@dataclass
class WritebackJournal:
    """Durable record of every write attempt (section 9.5).

    "A failed writeback must be visible. A call that processed successfully
    but silently failed to write is the worst failure mode: the rep believes
    their notes are in the CRM and they aren't."

    So there is no code path where a write is attempted and no entry exists.
    The journal is written before the call, not after it, and `pending()` is
    what the UI reads to show the failure.
    """

    entries: list[JournalEntry] = dc_field(default_factory=list)

    def record(self, key: str, payload: Mapping[str, Any]) -> JournalEntry:
        for entry in self.entries:
            if entry.key == key:
                entry.payload = payload
                return entry
        entry = JournalEntry(key=key, payload=payload)
        self.entries.append(entry)
        return entry

    def pending(self) -> tuple[JournalEntry, ...]:
        """Everything that has not landed. Surfaced in the UI, not logged."""
        return tuple(e for e in self.entries if not e.done)

    def failures(self) -> tuple[JournalEntry, ...]:
        return tuple(e for e in self.entries if e.failed)

    @property
    def all_landed(self) -> bool:
        return all(e.done for e in self.entries)


def with_backoff(
    fn: Callable[[], Any],
    *,
    attempts: int = 5,
    base_delay: float = 0.5,
    sleep: Callable[[float], None] = time.sleep,
) -> Any:
    """Retry on rate limiting, backing off exponentially.

    `sleep` is injected so tests do not actually wait, and so a durable
    workflow can substitute its own timer rather than blocking a worker.
    """
    delay = base_delay
    last: RateLimited | None = None
    for attempt in range(attempts):
        try:
            return fn()
        except RateLimited as exc:
            last = exc
            if attempt == attempts - 1:
                break
            sleep(max(exc.retry_after, delay))
            delay *= 2
    raise last if last else RuntimeError("retry loop exited without an attempt")


def write_payload(
    crm: CRM,
    journal: WritebackJournal,
    call_id: str,
    kind: str,
    payload: Mapping[str, Any],
    *,
    provenance: Provenance | None = None,
    discriminator: str | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> JournalEntry:
    """Upsert one object, journalled and retried.

    The entry is created before the attempt, so a crash between the attempt
    and its result still leaves something for `pending()` to surface.
    """
    key = identity_key(call_id, kind, discriminator)
    entry = journal.record(key, payload)

    def attempt() -> Any:
        entry.attempts += 1
        return upsert(crm, key, payload, provenance=provenance)

    try:
        with_backoff(attempt, sleep=sleep)
    except Exception as exc:  # noqa: BLE001 -- the journal records everything
        entry.last_error = f"{type(exc).__name__}: {exc}"
        logger.error("writeback failed for %s: %s", key, entry.last_error)
        return entry

    entry.done = True
    entry.last_error = None
    return entry


def summarise(results: Sequence[WriteResult]) -> Mapping[WriteAction, int]:
    counts: dict[WriteAction, int] = {}
    for result in results:
        counts[result.action] = counts.get(result.action, 0) + 1
    return counts
