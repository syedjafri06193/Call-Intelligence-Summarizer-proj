"""Audio ingestion and speaker attribution (design.md section 4.2).

THE HIGHEST-LEVERAGE DECISION IN THE PROJECT, and the legal and technical
answers coincide.

Diarization -- deciding who spoke when from a mixed recording -- is the least
reliable stage in any call pipeline. It degrades badly with overlapping
speech, and sales calls have a lot of overlap. It is also the stage that
creates BIPA exposure, because voice-characteristic speaker identification is
what the Otter plaintiffs allege constitutes voiceprint extraction.

Both problems disappear if you take per-speaker audio tracks. Each track has
exactly one speaker, already tied to a platform account identity:

    * perfect speaker attribution
    * better ASR accuracy, because each track has no cross-talk
    * speaker role from calendar metadata, trivially
    * no voice modelling of any kind

Where per-speaker tracks are unavailable, this module RAISES. It does not fall
back. The explicit exception with a policy reference in the message is the
point -- it makes the constraint visible to whoever hits it six months from
now and is tempted to just add pyannote.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol, Sequence

from ..consent.gate import ProcessingDecision, verify_decision_still_valid
from ..consent.model import Call


class Attribution(str, Enum):
    """How speaker identity was established. All four are non-voice."""

    #: A separate audio file per participant from the meeting platform, each
    #: already tied to an account. Best case.
    PLATFORM_TRACK = "platform"
    #: Two-leg stereo from a dialer: channel is speaker. Effectively
    #: per-speaker, and equally free of voice modelling.
    TWO_LEG_STEREO = "two_leg_stereo"
    #: A human labelled the turns in the review UI (section 4.3, option 2).
    MANUAL = "manual"
    #: Mixed audio, transcribed unattributed (section 4.3, option 1). Honest,
    #: and unusable for "who committed to what".
    NONE = "none"


@dataclass(frozen=True)
class AudioTrack:
    """One audio stream with its speaker already known."""

    participant_id: str | None
    uri: str
    attribution: Attribution
    sample_rate_hz: int = 16_000
    channels: int = 1
    duration_ms: int | None = None

    @property
    def is_narrowband(self) -> bool:
        """8 kHz telephony. Worth surfacing: section 5.1 notes published WER
        figures are measured on clean read speech and do not transfer, and the
        gap is widest on narrowband dialer audio."""
        return self.sample_rate_hz <= 8_000


class NoSpeakerAttribution(RuntimeError):
    """Mixed single-channel audio with no way to attribute speakers.

    Raised rather than worked around. The three honest options are in the
    message, in the design document's order of preference.
    """

    def __init__(self, call_id: str, detail: str) -> None:
        self.call_id = call_id
        super().__init__(
            f"call {call_id}: {detail}\n"
            "\n"
            "Options, in order of preference (docs/legal.md section 4.3):\n"
            "  1. transcribe unattributed -- pass require_attribution=False. "
            "Useful for keyword search and topics; NOT usable for 'who "
            "committed to what'.\n"
            "  2. ask the rep to label the turns in the review UI.\n"
            "  3. skip the call. Sometimes correct.\n"
            "\n"
            "Voice-based diarization is disabled by policy. Deriving speaker "
            "identity from vocal characteristics plausibly creates a biometric "
            "identifier under Illinois BIPA, which carries $1,000-$5,000 per "
            "voiceprint and is class-actionable. See docs/legal.md section 2.3."
        )


class PlatformClient(Protocol):
    """What a meeting platform has to provide.

    Note what is absent: there is no `download_mixed_recording` that the rest
    of the pipeline could reach for. A platform that cannot supply per-speaker
    tracks returns an empty list and the caller has to decide what to do,
    rather than silently receiving something it can diarize.
    """

    def per_speaker_tracks(self, external_id: str) -> Sequence[AudioTrack]:
        ...

    def mixed_track(self, external_id: str) -> AudioTrack | None:
        ...


@dataclass(frozen=True)
class IngestResult:
    tracks: tuple[AudioTrack, ...]
    attribution: Attribution
    #: Set when the result is usable for transcription but not for
    #: attribution-dependent extraction. The UI shows this, and
    #: commitment extraction refuses to run against it.
    unattributed: bool = False
    warnings: tuple[str, ...] = ()


def ingest(
    call: Call,
    client: PlatformClient,
    decision: ProcessingDecision,
    *,
    require_attribution: bool = True,
) -> IngestResult:
    """Fetch audio for a call.

    THE CONSENT GATE IS CHECKED HERE, BEFORE ANY BYTES MOVE.

    Section 3.2: "The gate runs before audio is fetched. Not before
    transcription, not before storage. If you have downloaded the recording,
    you have already arguably intercepted it. Fetching is the action to gate."

    `verify_decision_still_valid` also re-checks the roster, because minutes
    pass between the gate running and the fetch starting in a durable
    workflow, and a participant joining in that window is exactly what the
    gate exists to catch.
    """
    verify_decision_still_valid(decision, call)

    tracks = tuple(client.per_speaker_tracks(call.external_id))
    if tracks:
        # Sanity-check what the platform handed back. A "per-speaker" track
        # with no participant id is not per-speaker, and accepting it would
        # produce a transcript that looks attributed and is not.
        unnamed = [t for t in tracks if not t.participant_id]
        if unnamed:
            raise NoSpeakerAttribution(
                call.call_id,
                f"{len(unnamed)} of {len(tracks)} platform tracks arrived "
                "without a participant id, so they are not per-speaker",
            )
        return IngestResult(
            tracks=tuple(
                AudioTrack(
                    participant_id=t.participant_id,
                    uri=t.uri,
                    attribution=Attribution.PLATFORM_TRACK,
                    sample_rate_hz=t.sample_rate_hz,
                    channels=t.channels,
                    duration_ms=t.duration_ms,
                )
                for t in tracks
            ),
            attribution=Attribution.PLATFORM_TRACK,
            warnings=_quality_warnings(tracks),
        )

    # No per-speaker tracks. Two-leg stereo is the one remaining case where
    # attribution is available without touching voice: the channel IS the
    # speaker, which is a property of how the dialer recorded it.
    mixed = client.mixed_track(call.external_id)
    if mixed is not None and call.channels == 2 and call.source_type == "dialer_two_leg":
        return _two_leg_stereo(call, mixed)

    if require_attribution:
        raise NoSpeakerAttribution(
            call.call_id,
            "the platform supplied no per-speaker tracks and this is not "
            "two-leg stereo",
        )

    if mixed is None:
        raise NoSpeakerAttribution(
            call.call_id, "no audio of any kind is available"
        )

    # Section 4.3 option 1, taken deliberately by the caller. The result is
    # marked so the UI can say "speaker unknown" and so commitment extraction
    # refuses: "A transcript that silently mis-attributes a commitment is
    # worse than one that says 'speaker unknown.'"
    return IngestResult(
        tracks=(
            AudioTrack(
                participant_id=None,
                uri=mixed.uri,
                attribution=Attribution.NONE,
                sample_rate_hz=mixed.sample_rate_hz,
                channels=mixed.channels,
                duration_ms=mixed.duration_ms,
            ),
        ),
        attribution=Attribution.NONE,
        unattributed=True,
        warnings=(
            "no speaker attribution: usable for search and topics, not for "
            "commitments, next steps, or anything naming who said what",
        )
        + _quality_warnings([mixed]),
    )


def _two_leg_stereo(call: Call, mixed: AudioTrack) -> IngestResult:
    """Split a two-leg stereo recording into one track per channel.

    Channel is speaker, which is a fact about how the dialer recorded the call
    rather than anything inferred from the audio. The mapping from channel to
    participant comes from the call roster.
    """
    reps = [p for p in call.participants if p.role.value == "rep"]
    others = [p for p in call.participants if p.role.value != "rep"]

    if len(reps) != 1 or len(others) != 1:
        raise NoSpeakerAttribution(
            call.call_id,
            f"two-leg stereo needs exactly one rep and one other party; "
            f"this call has {len(reps)} and {len(others)}",
        )

    return IngestResult(
        tracks=(
            AudioTrack(
                participant_id=reps[0].participant_id,
                uri=f"{mixed.uri}#channel=0",
                attribution=Attribution.TWO_LEG_STEREO,
                sample_rate_hz=mixed.sample_rate_hz,
                channels=1,
                duration_ms=mixed.duration_ms,
            ),
            AudioTrack(
                participant_id=others[0].participant_id,
                uri=f"{mixed.uri}#channel=1",
                attribution=Attribution.TWO_LEG_STEREO,
                sample_rate_hz=mixed.sample_rate_hz,
                channels=1,
                duration_ms=mixed.duration_ms,
            ),
        ),
        attribution=Attribution.TWO_LEG_STEREO,
        warnings=_quality_warnings([mixed]),
    )


def _quality_warnings(tracks: Sequence[AudioTrack]) -> tuple[str, ...]:
    out: list[str] = []
    if any(t.is_narrowband for t in tracks):
        out.append(
            "8 kHz narrowband audio: expect materially worse WER than the "
            "published figures, and a much worse proper-noun error rate "
            "(section 5.1). Track WER by source separately."
        )
    return tuple(out)
