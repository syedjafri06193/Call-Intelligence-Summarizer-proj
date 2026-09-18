"""The consent gate (design.md section 3.2).

Two rules make this real rather than decorative, and both are stated in the
design document as requirements rather than suggestions:

**The gate runs before audio is fetched.** Not before transcription, not
before storage. "If you have downloaded the recording, you have already
arguably intercepted it. Fetching is the action to gate." Everything in
`cis.ingest` refuses to move bytes without a `ProcessingDecision` that
permits it, and the decision carries the participant list it was computed
from so it cannot be reused against a call whose roster has changed.

**Default to all-party mode.** Given the interstate rule (section 2.1) and
BIPA's residency trigger, "the operationally correct default is to treat every
call as all-party." Relaxing it requires a named authoriser and is logged.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from enum import Enum

from .model import (
    Call,
    ConsentLedger,
    ConsentMethod,
    Participant,
)


class ConsentMode(str, Enum):
    #: Every participant must have a consenting record. The default, and the
    #: only mode that is safe without knowing where everyone is.
    ALL_PARTY = "all_party"
    #: One participant suffices. Federal baseline. Requires an explicit,
    #: logged authorisation to select -- see ConsentPolicy.
    ONE_PARTY = "one_party"


@dataclass(frozen=True)
class ConsentPolicy:
    """Per-customer configuration, with the relaxation deliberately awkward."""

    mode: ConsentMode = ConsentMode.ALL_PARTY
    #: Who authorised a non-default mode, and when. Required for ONE_PARTY:
    #: the design document says relaxing the default "should require an
    #: explicit acknowledgment from someone with authority, and should be
    #: logged". Making it a constructor requirement is how that stops being a
    #: process everyone forgets.
    authorised_by: str | None = None
    authorised_at: dt.datetime | None = None
    #: Participants who joined after the announcement are UNKNOWN by section
    #: 3.4. Turning this off is a relaxation of the same kind as ONE_PARTY and
    #: carries the same requirement: `authorised_by` must name someone. The
    #: field exists so that when it is on -- which is always, by default --
    #: the reason appears in the decision rather than as unexplained
    #: behaviour.
    late_joiners_require_consent: bool = True
    policy_version: str = "1"

    def __post_init__(self) -> None:
        if self.mode is ConsentMode.ONE_PARTY and not self.authorised_by:
            raise ValueError(
                "one_party mode requires authorised_by. Section 3.2: relaxing "
                "the all-party default needs an explicit acknowledgment from "
                "someone with authority, and it needs to be logged."
            )
        if not self.late_joiners_require_consent and not self.authorised_by:
            raise ValueError(
                "late_joiners_require_consent=False requires authorised_by. "
                "It relaxes section 3.4 -- a participant who missed the "
                "announcement is UNKNOWN -- and an unattributed relaxation of "
                "a consent rule is the thing that cannot be explained "
                "afterwards."
            )


class Blocker(str, Enum):
    NO_CONSENT_RECORD = "no_consent_record"
    DECLINED = "declined"
    WITHDRAWN = "withdrawn"
    LATE_JOINER = "late_joiner"
    NO_PARTICIPANTS = "no_participants"


#: Someone said no, or took it back. Distinct from never having answered:
#: a one-party policy can proceed past silence and cannot proceed past a
#: refusal.
_EXPLICIT_REFUSALS = frozenset({Blocker.DECLINED, Blocker.WITHDRAWN})


@dataclass(frozen=True)
class ParticipantBlock:
    participant_id: str
    display_name: str | None
    blocker: Blocker
    detail: str


@dataclass(frozen=True)
class ProcessingDecision:
    """The gate's answer. Carried alongside the call through the pipeline.

    `roster_fingerprint` is what stops a decision from outliving the facts it
    was computed against. A participant joining after the gate ran invalidates
    the decision, and ingestion checks it rather than trusting that the caller
    re-ran the gate.
    """

    allowed: bool
    call_id: str
    mode: ConsentMode
    policy_version: str
    decided_at: dt.datetime
    roster_fingerprint: str
    reason: str = ""
    remediation: str = ""
    blocks: tuple[ParticipantBlock, ...] = ()
    consenting_participants: tuple[str, ...] = ()

    def require(self) -> None:
        """Raise unless processing is permitted.

        Call sites use this rather than testing `.allowed` so that forgetting
        to check is a crash rather than a silent bypass.
        """
        if not self.allowed:
            raise ConsentDenied(self)


class ConsentDenied(RuntimeError):
    def __init__(self, decision: ProcessingDecision) -> None:
        self.decision = decision
        detail = "; ".join(
            f"{b.display_name or b.participant_id}: {b.detail}"
            for b in decision.blocks
        )
        super().__init__(
            f"processing denied for call {decision.call_id}: {decision.reason}"
            + (f" [{detail}]" if detail else "")
            + (f" -- {decision.remediation}" if decision.remediation else "")
        )


def roster_fingerprint(participants: tuple[Participant, ...]) -> str:
    """Order-independent digest of who is on the call.

    Order-independent because platforms report participants in arbitrary
    order and a re-ordering is not a roster change. Includes `joined_late`,
    because a participant transitioning to late-joiner status is a change the
    gate must see.
    """
    import hashlib

    parts = sorted(
        f"{p.participant_id}|{int(p.joined_late)}" for p in participants
    )
    digest = hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()
    return digest[:16]


def _block_for(
    participant: Participant,
    method: ConsentMethod,
    policy: ConsentPolicy,
) -> ParticipantBlock | None:
    if method is ConsentMethod.DECLINED:
        return ParticipantBlock(
            participant.participant_id,
            participant.display_name,
            Blocker.DECLINED,
            "declined to be recorded",
        )
    if method is ConsentMethod.WITHDRAWN:
        return ParticipantBlock(
            participant.participant_id,
            participant.display_name,
            Blocker.WITHDRAWN,
            "withdrew consent",
        )
    if method is ConsentMethod.UNKNOWN:
        return ParticipantBlock(
            participant.participant_id,
            participant.display_name,
            Blocker.NO_CONSENT_RECORD,
            "no consent record -- silence and calendar boilerplate are not "
            "consent (section 3.4)",
        )

    # Consenting method, but the participant joined after the announcement.
    # Section 3.4 again: they did not hear it, so whatever we recorded from the
    # announcement does not cover them.
    if participant.joined_late and policy.late_joiners_require_consent:
        if method is ConsentMethod.VERBAL_ACKNOWLEDGED:
            return ParticipantBlock(
                participant.participant_id,
                participant.display_name,
                Blocker.LATE_JOINER,
                "joined after the announcement, so the verbal acknowledgment "
                "on file cannot be theirs",
            )
    return None


def may_process(
    call: Call,
    ledger: ConsentLedger,
    policy: ConsentPolicy | None = None,
    *,
    now: dt.datetime | None = None,
) -> ProcessingDecision:
    """Decide whether this call may be fetched and processed.

    RUNS BEFORE ANY AUDIO IS FETCHED, STORED, OR TRANSCRIBED.
    """
    policy = policy or ConsentPolicy()
    now = now or dt.datetime.now(dt.timezone.utc)
    fingerprint = roster_fingerprint(call.participants)

    base = dict(
        call_id=call.call_id,
        mode=policy.mode,
        policy_version=policy.policy_version,
        decided_at=now,
        roster_fingerprint=fingerprint,
    )

    if not call.participants:
        # A call with no known participants cannot be consent-checked, so it
        # cannot be processed. This is not an edge case to smooth over: an
        # empty roster usually means the platform metadata failed to load, and
        # processing anyway would be processing a call we know nothing about.
        return ProcessingDecision(
            allowed=False,
            reason="no participants on the roster",
            remediation="reload participant metadata from the platform",
            blocks=(
                ParticipantBlock("", None, Blocker.NO_PARTICIPANTS, "empty roster"),
            ),
            **base,
        )

    blocks: list[ParticipantBlock] = []
    consenting: list[str] = []

    for participant in call.participants:
        method = ledger.effective(call.call_id, participant.participant_id)
        block = _block_for(participant, method, policy)
        if block is not None:
            blocks.append(block)
        else:
            consenting.append(participant.participant_id)

    if policy.mode is ConsentMode.ALL_PARTY:
        if blocks:
            return ProcessingDecision(
                allowed=False,
                reason=(
                    f"{len(blocks)} of {len(call.participants)} participant(s) "
                    "without recorded consent"
                ),
                remediation=(
                    "announce and capture acknowledgment, ask the platform to "
                    "prompt, or exclude the call"
                ),
                blocks=tuple(blocks),
                consenting_participants=tuple(consenting),
                **base,
            )
        return ProcessingDecision(
            allowed=True,
            reason="all participants have a consenting record",
            consenting_participants=tuple(consenting),
            **base,
        )

    # An explicit refusal stops processing in ANY mode. "One party consented"
    # is a defence against nobody having said yes; it is not a defence against
    # someone having said no, and docs/legal.md commits to withdrawal being as
    # easy as consent. UNKNOWN is different -- that is the case one-party mode
    # exists for -- so only DECLINED and WITHDRAWN block here.
    refusals = tuple(b for b in blocks if b.blocker in _EXPLICIT_REFUSALS)
    if refusals:
        named = ", ".join(b.participant_id for b in refusals)
        return ProcessingDecision(
            allowed=False,
            reason=(
                f"{len(refusals)} participant(s) declined or withdrew ({named}); "
                "a one-party policy does not override an explicit refusal"
            ),
            remediation="exclude the call, or delete their contribution",
            blocks=tuple(blocks),
            **base,
        )

    # ONE_PARTY. Still requires at least one affirmative record -- "one party"
    # means one party consented, not that nobody objected.
    if consenting:
        return ProcessingDecision(
            allowed=True,
            reason=(
                f"{len(consenting)} participant(s) consented under a one-party "
                f"policy authorised by {policy.authorised_by}"
            ),
            blocks=tuple(blocks),
            consenting_participants=tuple(consenting),
            **base,
        )
    return ProcessingDecision(
        allowed=False,
        reason="one-party policy, but no participant has a consenting record",
        remediation="capture consent from at least the host",
        blocks=tuple(blocks),
        **base,
    )


def verify_decision_still_valid(
    decision: ProcessingDecision, call: Call
) -> None:
    """Re-check a decision against the current roster.

    Called by ingestion immediately before moving any bytes. A participant who
    joined between the gate running and the fetch starting is exactly the
    situation the gate exists to catch, and the window is real -- these are
    minutes apart in a durable workflow.
    """
    if decision.call_id != call.call_id:
        raise ConsentDenied(
            ProcessingDecision(
                allowed=False,
                call_id=call.call_id,
                mode=decision.mode,
                policy_version=decision.policy_version,
                decided_at=decision.decided_at,
                roster_fingerprint=roster_fingerprint(call.participants),
                reason=(
                    f"decision was made for call {decision.call_id!r}, not "
                    f"{call.call_id!r}"
                ),
            )
        )
    current = roster_fingerprint(call.participants)
    if current != decision.roster_fingerprint:
        raise ConsentDenied(
            ProcessingDecision(
                allowed=False,
                call_id=call.call_id,
                mode=decision.mode,
                policy_version=decision.policy_version,
                decided_at=decision.decided_at,
                roster_fingerprint=current,
                reason="the participant roster changed after the gate ran",
                remediation="re-run may_process() against the current roster",
            )
        )
    decision.require()
