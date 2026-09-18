"""Consent as first-class state (design.md section 3.1).

Consent is the first thing the system does and the gate on everything else, so
it is modelled as state rather than as a flag on a call.

Four properties, each of which is a legal position rather than a style choice:

* **Per participant, not per call.** The host consenting is not the others
  consenting. Section 2.2: single-host consent is not a defence, and it is the
  specific allegation in the Otter litigation -- consent obtained "at most,
  from the host who added the assistant".

* **Evidence-backed.** Every record points at something verifiable: a platform
  event, a signed document, or a transcript span containing the
  acknowledgment. A consent record you cannot substantiate is not evidence of
  consent, it is evidence that you recorded a claim about consent.

* **Immutable and append-only.** Withdrawal is a new record, not an edit. The
  history of who consented when is the artifact that matters if anyone ever
  asks, and an editable record cannot answer the question.

* **`unknown` is not `consented`.** Default deny, everywhere.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Mapping


class ConsentMethod(str, Enum):
    """How consent was obtained, or why it is absent.

    The enum is deliberately small. Every value here is either something you
    can point at in a record, or an explicit absence -- there is no
    "implied", "assumed", or "covered by terms", because section 3.4 is
    unambiguous that boilerplate in a calendar invite is `UNKNOWN`.
    """

    #: Announced on the call and the participant responded affirmatively.
    VERBAL_ACKNOWLEDGED = "verbal_acknowledged"
    #: The meeting platform's own consent prompt fired and was accepted.
    #: Best case -- it produces a platform event you can rely on without
    #: processing any audio at all (section 3.3).
    PLATFORM_CONSENT = "platform_consent"
    #: A signed agreement on file, predating the call.
    WRITTEN_PRIOR = "written_prior"
    #: Explicitly said no.
    DECLINED = "declined"
    #: Never established. Silence, a late joiner who missed the announcement,
    #: a calendar-invite disclaimer. NOT consent.
    UNKNOWN = "unknown"
    #: Previously consented and has since withdrawn. Distinct from DECLINED
    #: because processing may already have happened and may need undoing.
    WITHDRAWN = "withdrawn"


#: The methods that actually permit processing. Everything else is a refusal
#: or an absence. Written as a frozenset rather than an `in (...)` test at each
#: call site so there is exactly one place this can be widened, and widening it
#: shows up in a diff as what it is.
CONSENTING_METHODS = frozenset(
    {
        ConsentMethod.VERBAL_ACKNOWLEDGED,
        ConsentMethod.PLATFORM_CONSENT,
        ConsentMethod.WRITTEN_PRIOR,
    }
)


class ParticipantRole(str, Enum):
    REP = "rep"
    PROSPECT = "prospect"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Participant:
    """Someone on the call.

    `participant_id` comes from the meeting platform, never from voice. That
    is the whole of section 2.3 in one field: identity is an account
    identifier the platform already has, not something derived from vocal
    characteristics.
    """

    participant_id: str
    display_name: str | None = None
    email: str | None = None
    role: ParticipantRole = ParticipantRole.UNKNOWN
    #: Self-declared or inferred from calendar/CRM data. NEVER authoritative:
    #: section 2.1's interstate rule means a participant's stated location does
    #: not settle which law applies, and BIPA attaches on residency rather than
    #: location. It is a hint for the UI, not an input to the gate.
    jurisdiction_hint: str | None = None
    #: True when the participant joined after the recording announcement.
    #: Section 3.4: a late joiner who missed the announcement is UNKNOWN.
    joined_late: bool = False


@dataclass(frozen=True)
class ConsentRecord:
    """One immutable statement about one participant's consent."""

    call_id: str
    participant_id: str
    method: ConsentMethod
    #: A transcript span id, a platform event id, or a document id. Required
    #: for every method that permits processing -- enforced in __post_init__,
    #: because a consenting record without evidence is the exact thing that
    #: fails in discovery.
    evidence_ref: str | None
    recorded_at: dt.datetime
    email: str | None = None
    jurisdiction_hint: str | None = None
    #: Free text for the audit trail -- who ran the tool, which policy version.
    note: str | None = None

    def __post_init__(self) -> None:
        if self.method in CONSENTING_METHODS and not self.evidence_ref:
            raise ValueError(
                f"consent method {self.method.value!r} for participant "
                f"{self.participant_id!r} has no evidence_ref. Every record "
                "that permits processing must point at something verifiable "
                "(design.md section 3.1)."
            )
        if self.recorded_at.tzinfo is None:
            raise ValueError(
                "recorded_at must be timezone-aware; a naive timestamp in an "
                "audit trail is not a timestamp"
            )

    @property
    def permits_processing(self) -> bool:
        return self.method in CONSENTING_METHODS


class ConsentLedger:
    """Append-only store of consent records.

    Append-only is load-bearing. `withdraw()` adds a record, it does not
    remove or amend one, so the ledger can always answer "what did we believe,
    and when, and on what evidence" -- which is the only question that matters
    if this is ever examined.
    """

    def __init__(self, records: Iterable[ConsentRecord] = ()) -> None:
        self._records: list[ConsentRecord] = list(records)

    def append(self, record: ConsentRecord) -> None:
        self._records.append(record)

    def extend(self, records: Iterable[ConsentRecord]) -> None:
        for record in records:
            self.append(record)

    def all(self) -> tuple[ConsentRecord, ...]:
        return tuple(self._records)

    def for_call(self, call_id: str) -> tuple[ConsentRecord, ...]:
        return tuple(r for r in self._records if r.call_id == call_id)

    def history(self, call_id: str, participant_id: str) -> tuple[ConsentRecord, ...]:
        """Every record for one participant, oldest first."""
        return tuple(
            sorted(
                (
                    r
                    for r in self._records
                    if r.call_id == call_id and r.participant_id == participant_id
                ),
                key=lambda r: r.recorded_at,
            )
        )

    def current(self, call_id: str, participant_id: str) -> ConsentRecord | None:
        """The record in force now, or None if we have never heard anything.

        Latest-wins by `recorded_at`, which is what makes withdrawal work:
        appending a WITHDRAWN record dated after the consent supersedes it
        without touching it.
        """
        history = self.history(call_id, participant_id)
        return history[-1] if history else None

    def effective(self, call_id: str, participant_id: str) -> ConsentMethod:
        """What the gate should act on. Absent means UNKNOWN, never consented."""
        record = self.current(call_id, participant_id)
        return record.method if record else ConsentMethod.UNKNOWN

    def withdraw(
        self,
        call_id: str,
        participant_id: str,
        *,
        at: dt.datetime,
        note: str | None = None,
    ) -> ConsentRecord:
        """Record a withdrawal. Appends; never edits.

        A withdrawal needs no evidence_ref -- the asymmetry is deliberate.
        Permitting processing requires evidence; stopping it does not, because
        the failure modes are not symmetric. Refusing to honour a withdrawal
        because it was not sufficiently documented is not a defensible
        position.
        """
        record = ConsentRecord(
            call_id=call_id,
            participant_id=participant_id,
            method=ConsentMethod.WITHDRAWN,
            evidence_ref=None,
            recorded_at=at,
            note=note,
        )
        self.append(record)
        return record


@dataclass(frozen=True)
class Call:
    """A call, before anything has been fetched or processed."""

    call_id: str
    external_id: str
    participants: tuple[Participant, ...]
    scheduled_at: dt.datetime
    source_type: str = "zoom_cloud"
    channels: int = 1
    #: Set once the recording announcement has been located in the opening
    #: window. Purely informational for the gate.
    announcement_span_id: str | None = None

    def participant(self, participant_id: str) -> Participant | None:
        for p in self.participants:
            if p.participant_id == participant_id:
                return p
        return None


def snapshot(ledger: ConsentLedger, call: Call) -> Mapping[str, ConsentMethod]:
    """Current effective consent for every participant on a call."""
    return {
        p.participant_id: ledger.effective(call.call_id, p.participant_id)
        for p in call.participants
    }
