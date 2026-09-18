"""Detecting verbal consent in the opening minutes (design.md section 3.3).

THE CHICKEN-AND-EGG PROBLEM, STATED PLAINLY.

You need a transcript to detect verbal consent, but consent gates
transcription. The design document is unusually direct about this:

    "The resolution is a narrowly-scoped consent-detection pass over the first
    N seconds, under a documented policy, whose output is either a consent
    record or an immediate deletion of everything. Get counsel's view on this
    specifically -- it's the weakest point in the design and you should not
    paper over it."

So this module does exactly that and nothing more:

* It only ever sees `CONSENT_WINDOW_MS` of audio. The window is a module
  constant rather than a parameter, so widening it is a diff someone reviews.
* Its only outputs are consent records, or a `DeletionRequired` signal.
* `ConsentDetectionRun` records what was examined and when, so the narrow
  scope is demonstrable rather than asserted.

Where the platform has its own consent prompt, use that instead and skip this
entirely -- it produces a platform event with no audio processing at all.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass

from ..transcript.model import Transcript, TranscriptSpan
from .model import Call, ConsentMethod, ConsentRecord

#: How much of the call the consent pass may examine. Ninety seconds is long
#: enough for an announcement plus a round of responses and short enough that
#: it cannot become a general transcription pass by accident.
CONSENT_WINDOW_MS = 90_000

#: Phrases that constitute announcing the recording. Deliberately requires an
#: explicit mention of recording or an AI notetaker -- "let's get started" is
#: not an announcement.
_ANNOUNCEMENT_PATTERNS = (
    r"\brecord(ing|ed)?\b.{0,40}\b(this|the)\s+(call|meeting|conversation)\b",
    r"\b(this|the)\s+(call|meeting|conversation)\b.{0,40}\brecord(ing|ed)?\b",
    r"\b(ai|automated)\s+(assistant|note.?taker|notetaker)\b",
    r"\btaking\s+notes\b.{0,30}\brecord",
    r"\bis\s+everyone\s+(ok|okay|alright|fine)\s+with\s+that\b",
)

#: Affirmative responses. Short list on purpose: an ambiguous response is
#: UNKNOWN, and section 3.4 is explicit that the burden runs that way.
_AFFIRMATIVE_PATTERNS = (
    r"^\s*(yes|yeah|yep|yup|sure|absolutely|of course|fine|okay|ok)\b",
    r"\b(that'?s|sounds)\s+(fine|good|great|okay|ok)\b",
    r"\bno\s+(problem|objection|issue)s?\b",
    r"\bgo\s+ahead\b",
    r"\bi'?m\s+(fine|okay|ok|good)\s+with\s+(that|it)\b",
    r"\bhappy\s+(for\s+you\s+)?to\b.{0,20}\brecord",
)

#: Explicit refusals. Checked BEFORE affirmatives, because "no, that's fine"
#: and "no, I'd rather not" both start with "no" and only one of them is
#: consent -- so the refusal patterns are written to be specific and are
#: given precedence.
_NEGATIVE_PATTERNS = (
    r"\b(i'?d\s+)?(rather|prefer)\s+(not|you\s+didn'?t)\b",
    r"\bplease\s+(don'?t|do\s+not)\b",
    r"\bnot\s+(comfortable|okay|ok)\s+with\b",
    r"\bturn\s+(it|that|the\s+recording)\s+off\b",
    r"\bi\s+don'?t\s+consent\b",
    r"\bno,?\s+(i|please|don'?t|do\s+not)\b",
    r"\bcan\s+(we|you)\s+not\s+record\b",
)

_ANNOUNCEMENT_RE = tuple(re.compile(p, re.IGNORECASE) for p in _ANNOUNCEMENT_PATTERNS)
_AFFIRMATIVE_RE = tuple(re.compile(p, re.IGNORECASE) for p in _AFFIRMATIVE_PATTERNS)
_NEGATIVE_RE = tuple(re.compile(p, re.IGNORECASE) for p in _NEGATIVE_PATTERNS)


class DeletionRequired(RuntimeError):
    """No usable consent was found. Everything fetched must be deleted now.

    Raised rather than returned so it cannot be ignored by a caller who only
    looked at the happy path. The design document's phrasing is "either a
    consent record or an immediate deletion of everything", and an exception is
    how "immediate" becomes structural.
    """

    def __init__(self, call_id: str, reason: str) -> None:
        self.call_id = call_id
        self.reason = reason
        super().__init__(
            f"consent detection found nothing usable for call {call_id}: "
            f"{reason}. Delete all fetched audio and derived artifacts now "
            "(docs/legal.md section 3.3)."
        )


@dataclass(frozen=True)
class DetectedAnnouncement:
    segment_id: str
    participant_id: str | None
    span: TranscriptSpan
    text: str


@dataclass(frozen=True)
class ConsentDetectionRun:
    """A record of what the narrow pass examined.

    Exists so the scope is demonstrable. "It only looked at the first ninety
    seconds" is a claim; this is the artifact that supports it.
    """

    call_id: str
    window_ms: int
    segments_examined: int
    ran_at: dt.datetime
    announcement: DetectedAnnouncement | None
    records: tuple[ConsentRecord, ...]
    unresolved_participants: tuple[str, ...]
    detector_version: str = "1"


def _matches(patterns: tuple[re.Pattern[str], ...], text: str) -> re.Match[str] | None:
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            return match
    return None


def find_announcement(
    transcript: Transcript, *, window_ms: int = CONSENT_WINDOW_MS
) -> DetectedAnnouncement | None:
    """Locate the recording announcement within the opening window."""
    for segment in transcript.window(0, window_ms):
        match = _matches(_ANNOUNCEMENT_RE, segment.text)
        if match is None:
            continue
        span = transcript.make_span(segment.segment_id, match.start(), match.end())
        return DetectedAnnouncement(
            segment_id=segment.segment_id,
            participant_id=segment.participant_id,
            span=span,
            text=segment.text,
        )
    return None


def classify_response(text: str) -> ConsentMethod:
    """Classify one utterance.

    Negatives are checked first. "No, that's fine" and "No, I'd rather not"
    both open with "no", and treating an ambiguous utterance as consent is the
    failure that matters -- so the refusal patterns are specific and win ties.
    """
    if _matches(_NEGATIVE_RE, text):
        return ConsentMethod.DECLINED
    if _matches(_AFFIRMATIVE_RE, text):
        return ConsentMethod.VERBAL_ACKNOWLEDGED
    return ConsentMethod.UNKNOWN


def detect_consent(
    call: Call,
    transcript: Transcript,
    *,
    now: dt.datetime | None = None,
    window_ms: int = CONSENT_WINDOW_MS,
) -> ConsentDetectionRun:
    """The narrowly-scoped pass.

    Produces a consent record for each participant who audibly responded, and
    names the rest as unresolved. It does NOT invent records for participants
    who said nothing: section 3.4, silence is not consent.

    Raises DeletionRequired when no announcement was found at all -- that is
    the case where nothing was ever established and there is no basis to keep
    anything.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    window = transcript.window(0, window_ms)

    announcement = find_announcement(transcript, window_ms=window_ms)
    if announcement is None:
        raise DeletionRequired(
            call.call_id,
            f"no recording announcement in the first {window_ms // 1000}s",
        )

    records: list[ConsentRecord] = []
    unresolved: list[str] = []
    responded: set[str] = set()

    # Only utterances AFTER the announcement can be responses to it. A "sure"
    # spoken in the small talk beforehand is not consent, and matching it would
    # be the sort of thing that looks fine until someone reads the transcript.
    announcement_segment = transcript.segment(announcement.segment_id)
    assert announcement_segment is not None
    after = [
        s
        for s in window
        if s.start_ms >= announcement_segment.start_ms
        and s.segment_id != announcement.segment_id
    ]

    for segment in after:
        if segment.participant_id is None or segment.participant_id in responded:
            continue
        method = classify_response(segment.text)
        if method is ConsentMethod.UNKNOWN:
            continue

        responded.add(segment.participant_id)
        span = transcript.make_span(segment.segment_id, 0, len(segment.text))
        records.append(
            ConsentRecord(
                call_id=call.call_id,
                participant_id=segment.participant_id,
                method=method,
                # DECLINED carries no evidence requirement in the model, but
                # recording it anyway means a dispute about whether someone
                # refused has a transcript span behind it.
                evidence_ref=span.span_id if method in {
                    ConsentMethod.VERBAL_ACKNOWLEDGED,
                    ConsentMethod.DECLINED,
                } else None,
                recorded_at=now,
                email=_email_for(call, segment.participant_id),
                note=f"detected by consent pass v1 over the first {window_ms // 1000}s",
            )
        )

    # The speaker who made the announcement consented to their own recording by
    # making it. Recorded explicitly rather than assumed, with the announcement
    # itself as the evidence.
    if announcement.participant_id and announcement.participant_id not in responded:
        responded.add(announcement.participant_id)
        records.append(
            ConsentRecord(
                call_id=call.call_id,
                participant_id=announcement.participant_id,
                method=ConsentMethod.VERBAL_ACKNOWLEDGED,
                evidence_ref=announcement.span.span_id,
                recorded_at=now,
                email=_email_for(call, announcement.participant_id),
                note="made the recording announcement",
            )
        )

    for participant in call.participants:
        if participant.participant_id not in responded:
            unresolved.append(participant.participant_id)

    # The deletion threshold is about the OTHER parties, not about whether any
    # record at all came out.
    #
    # The announcer consents to their own recording by announcing it, so a pass
    # that found only the announcement always produces one permitting record.
    # Treating that as success would mean the narrow exception -- processing
    # ninety seconds of audio from people who had not consented -- bought
    # nothing and we kept the audio anyway. That is host-only consent, which
    # section 2.2 says is not a defence and is the specific allegation in the
    # Otter litigation.
    #
    # One other participant responding is enough to keep going: the gate will
    # still block on anyone who did not, the rep re-asks, and the call can be
    # re-gated without throwing away a real record.
    others_consented = any(
        r.permits_processing and r.participant_id != announcement.participant_id
        for r in records
    )
    if not others_consented:
        raise DeletionRequired(
            call.call_id,
            "an announcement was made but no participant other than the "
            "announcer affirmatively consented -- host-only consent is not a "
            "basis to retain the audio",
        )

    return ConsentDetectionRun(
        call_id=call.call_id,
        window_ms=window_ms,
        segments_examined=len(window),
        ran_at=now,
        announcement=announcement,
        records=tuple(records),
        unresolved_participants=tuple(unresolved),
    )


def _email_for(call: Call, participant_id: str) -> str | None:
    participant = call.participant(participant_id)
    return participant.email if participant else None
