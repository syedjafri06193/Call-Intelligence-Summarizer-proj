"""Next steps become tasks -- carefully (design.md section 9.3).

    "The highest-value output and the highest-risk one, because a hallucinated
    task is an action taken in the world."

Four gates, all of them cheap, because the thing they prevent is not:

* ungrounded -> never a task
* below the confidence threshold -> never a task
* unattributed transcript -> never a task, because an owner cannot be resolved
* `requires_confirmation=True` unconditionally for v1

And one date rule that is easy to get wrong and produces a visibly silly
result when you do:

    "Resolve relative dates ('by end of next week') against the call date, not
    the processing date -- a call processed the following Monday would
    otherwise produce a task due in the past."

`resolve_due` therefore takes the call date and has no access to the clock.
Nothing in this module can read the current time.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from typing import Sequence

from ..consent.model import Call, ParticipantRole
from ..extract.grounded import Field, GroundedClaim
from ..transcript.model import TranscriptSpan

#: Below this, a commitment is not proposed at all. Higher than the threshold
#: for writing a field, because the failure is worse: a wrong field is read
#: and ignored, a wrong task is a message sent to a colleague.
TASK_THRESHOLD = 0.7

#: The fields that can become a task. Both are attribution-dependent, which is
#: why an unattributed transcript produces no tasks at all.
TASK_FIELDS = frozenset({Field.COMMITMENT, Field.NEXT_STEP})


class TaskMustBeConfirmed(ValueError):
    """Someone set `requires_confirmation=False`."""


@dataclass(frozen=True)
class ProposedTask:
    """A task, pending a human's nod.

    `requires_confirmation` is not a default that can be overridden -- the
    constructor rejects False. Section 9.3: "unconditionally for v1. A task
    auto-assigned to a colleague based on a misheard sentence is a
    meaningfully bad outcome, and the review step costs the rep five seconds."
    """

    title: str
    evidence: tuple[TranscriptSpan, ...]
    confidence: float
    due: dt.date | None = None
    owner_participant_id: str | None = None
    owner_email: str | None = None
    requires_confirmation: bool = True
    #: Why no date was set, when there is a date-like phrase that did not
    #: parse. Shown in the review UI so the rep can fill it in rather than
    #: wondering whether the pipeline missed it.
    due_note: str | None = None

    def __post_init__(self) -> None:
        if not self.requires_confirmation:
            raise TaskMustBeConfirmed(
                "requires_confirmation is unconditional for v1 (section 9.3). "
                "A task auto-assigned from a misheard sentence is an action "
                "taken in the world."
            )
        if not self.evidence:
            raise ValueError("a task with no evidence is a hallucinated task")
        if not self.title.strip():
            raise ValueError("a task needs a title")

    @property
    def quote(self) -> str:
        return " ... ".join(s.text for s in self.evidence)


# ------------------------------------------------------------ date parsing

_WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

#: Order matters inside the alternation. "end of next month" has to be tried
#: before "next month", or the leftmost-longest-alternative rule takes the
#: shorter one and returns the *first* of next month for a phrase that means
#: the last -- thirty days early, silently.
_DATE_PHRASE_RE = re.compile(
    r"\b("
    r"today|tomorrow|tonight"
    r"|end of (?:the )?next (?:week|month|quarter)"
    r"|end of (?:the )?(?:day|week|month|quarter)"
    r"|next week|this week|next month|next quarter"
    r"|(?<!from )(?:by |on |before )?(?:next |this )?(?:" + "|".join(_WEEKDAYS) + r")"
    r"|in (?:a|an|one|two|three|four|five|six|seven|eight|nine|ten|eleven"
    r"|twelve|\d+) (?:day|days|week|weeks|month|months)"
    r")\b",
    re.IGNORECASE,
)

_NUMBER_WORDS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12,
}

#: Phrases that clearly name a date and that this parser deliberately does not
#: resolve. Matching them is the point: the alternative is returning
#: `(None, None)`, which reads as "no date was mentioned" and leaves the rep
#: wondering whether the pipeline missed it. These produce a note instead, and
#: the review UI shows it next to an empty due date.
_AMBIGUOUS_DATE_RE = re.compile(
    r"\b("
    r"(?:a|an|one|two|three|\w+) weeks? from (?:" + "|".join(_WEEKDAYS) + r")"
    r"|the \d{1,2}(?:st|nd|rd|th)"
    r"|(?:early|mid|late)[ -](?:next )?(?:week|month|quarter|year)"
    r"|(?:before|by|after) (?:the )?(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?"
    r"|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t|tember)?"
    r"|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
    r"|in (?:a few|several|a couple of) (?:days|weeks|months)"
    r")\b",
    re.IGNORECASE,
)


def resolve_due(text: str, call_date: dt.date) -> tuple[dt.date | None, str | None]:
    """Resolve a relative date against the call date.

    Returns `(date, note)`. A date-like phrase that cannot be resolved returns
    `(None, note)` rather than a guess: the note goes to the review UI and the
    rep sets the date in two seconds. Guessing produces a task due on a day
    nobody agreed to, which is worse than an empty field and much harder to
    notice.
    """
    # Ambiguous phrases are checked FIRST, because several of them contain a
    # resolvable phrase: "late next month" contains "next month", and
    # resolving it would silently answer a question the speaker did not ask.
    ambiguous = _AMBIGUOUS_DATE_RE.search(text)
    if ambiguous:
        return None, f"could not resolve {ambiguous.group(0)!r}"

    match = _DATE_PHRASE_RE.search(text)
    if not match:
        return None, None

    phrase = " ".join(match.group(0).lower().split())
    phrase = re.sub(r"^(by|on|before)\s+", "", phrase)

    if phrase == "today" or phrase == "tonight" or phrase == "end of day":
        return call_date, None
    if phrase == "end of the day":
        return call_date, None
    if phrase == "tomorrow":
        return call_date + dt.timedelta(days=1), None

    # Weeks run Monday to Sunday, and "this week" means the week containing
    # the call. Computing it as "the next Friday" instead would make "end of
    # next week" on a Saturday land two calendar weeks out, because the Friday
    # it steps forward from is already in the following week.
    friday_this_week = call_date + dt.timedelta(days=4 - call_date.weekday())
    if phrase in {"end of week", "end of the week", "this week"}:
        # On a weekend this week's Friday has passed. Roll forward rather than
        # return a date in the past -- which is the failure section 9.3 names.
        if friday_this_week < call_date:
            return friday_this_week + dt.timedelta(days=7), None
        return friday_this_week, None
    if phrase in {"end of next week", "next week", "end of the next week"}:
        return friday_this_week + dt.timedelta(days=7), None

    if phrase in {
        "end of month", "end of the month", "next month",
        "end of next month", "end of the next month",
    }:
        first_next = (call_date.replace(day=1) + dt.timedelta(days=32)).replace(day=1)
        if phrase == "next month":
            return first_next, None
        if phrase in {"end of next month", "end of the next month"}:
            first_after = (first_next + dt.timedelta(days=32)).replace(day=1)
            return first_after - dt.timedelta(days=1), None
        return first_next - dt.timedelta(days=1), None

    if phrase in {
        "end of quarter", "end of the quarter",
        "end of next quarter", "end of the next quarter", "next quarter",
    }:
        quarters_ahead = 1 if "next" in phrase else 0
        # Month index after the end of the target quarter, which can run past
        # December and has to wrap rather than produce month 15.
        month_after = ((call_date.month - 1) // 3 + 1 + quarters_ahead) * 3 + 1
        year = call_date.year + (month_after - 1) // 12
        month = (month_after - 1) % 12 + 1
        return dt.date(year, month, 1) - dt.timedelta(days=1), None

    weekday_match = re.match(r"(?:(next|this) )?(" + "|".join(_WEEKDAYS) + r")$", phrase)
    if weekday_match:
        qualifier, day = weekday_match.groups()
        if qualifier == "next":
            # "next Tuesday" is the Tuesday of the following Monday-to-Sunday
            # week, whichever day the call was on. The tempting alternative --
            # the next occurrence, plus a week if that is soon -- reads "next
            # Tuesday" said on a Monday as tomorrow.
            #
            # One consequence worth naming: on a Sunday, the following week
            # starts tomorrow, so "next Monday" is tomorrow. That is what the
            # words mean on a Sunday, and the test says so.
            start_of_next_week = call_date + dt.timedelta(days=7 - call_date.weekday())
            return start_of_next_week + dt.timedelta(days=_WEEKDAYS[day]), None
        # `allow_today=True`: "I'll send it by Friday", said on a Friday,
        # means today. The alternative reads it as a week away, which is the
        # kind of quietly wrong date a rep does not notice until it matters.
        return _next_weekday(call_date, _WEEKDAYS[day], allow_today=True), None

    in_match = re.match(r"in (\S+) (day|days|week|weeks|month|months)$", phrase)
    if in_match:
        count_raw, unit = in_match.groups()
        count = _NUMBER_WORDS.get(count_raw)
        if count is None:
            try:
                count = int(count_raw)
            except ValueError:
                return None, f"could not resolve {match.group(0)!r}"
        if unit.startswith("day"):
            return call_date + dt.timedelta(days=count), None
        if unit.startswith("week"):
            return call_date + dt.timedelta(weeks=count), None
        month = call_date.month - 1 + count
        year = call_date.year + month // 12
        month = month % 12 + 1
        day = min(call_date.day, _days_in_month(year, month))
        return dt.date(year, month, day), None

    return None, f"could not resolve {match.group(0)!r}"


def _next_weekday(start: dt.date, weekday: int, *, allow_today: bool) -> dt.date:
    delta = (weekday - start.weekday()) % 7
    if delta == 0 and not allow_today:
        delta = 7
    return start + dt.timedelta(days=delta)


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        return 31
    return (dt.date(year, month + 1, 1) - dt.timedelta(days=1)).day


# ----------------------------------------------------------- task proposal


def resolve_owner(claim: GroundedClaim, call: Call) -> tuple[str | None, str | None]:
    """Who owns this task.

    Only the roster is consulted. A commitment whose speaker cannot be tied to
    a participant gets no owner and is assigned by the rep during
    confirmation -- guessing "probably the rep" is how a task lands on the
    wrong person's list.
    """
    participant_id = claim.subject_participant_id
    if not participant_id:
        return None, None
    for participant in call.participants:
        if participant.participant_id == participant_id:
            return participant.participant_id, participant.email
    return None, None


def propose_tasks(
    claims: Sequence[GroundedClaim],
    call: Call,
    *,
    attributed: bool = True,
    threshold: float = TASK_THRESHOLD,
) -> tuple[ProposedTask, ...]:
    """Turn commitments into proposed tasks.

    Nothing here creates a task in the CRM. These are proposals for the review
    UI, and they stay proposals until a human confirms.
    """
    if not attributed:
        # Section 4.3: a transcript with no speakers cannot say who committed
        # to anything, and a task is precisely a claim about who committed to
        # what. Refused wholesale rather than assigned to the rep by default.
        return ()

    call_date = call.scheduled_at.date()
    tasks: list[ProposedTask] = []

    for claim in claims:
        if claim.field not in TASK_FIELDS:
            continue
        if claim.confidence < threshold:
            continue

        due, note = resolve_due(f"{claim.value} {claim.quote}", call_date)
        owner_id, owner_email = resolve_owner(claim, call)

        tasks.append(
            ProposedTask(
                title=claim.value.strip(),
                evidence=claim.spans,
                confidence=claim.confidence,
                due=due,
                due_note=note,
                owner_participant_id=owner_id,
                owner_email=owner_email,
            )
        )

    return tuple(tasks)


def rep_of(call: Call) -> str | None:
    for participant in call.participants:
        if participant.role is ParticipantRole.REP:
            return participant.participant_id
    return None
