"""Transcripts, spans, and immutability (design.md sections 5.3, 5.4, 6.3).

Three properties the rest of the system depends on:

**Word-level timestamps are required** (5.3). Click-to-play is what makes a
cited quote verifiable in two seconds, and that is the mechanism the whole
grounding argument rests on. A span with character offsets but no milliseconds
is a citation nobody can check by listening.

**Transcripts are immutable and versioned** (5.4). A correction produces a new
version. Spans carry the version they were computed against, so a claim can
never silently come to point at different text than the one it was extracted
from -- which would make every stored claim quietly unverifiable.

**Spans quote text** (6.3). The span carries the text it refers to, and
validation checks the transcript still says that. Storing only offsets would
make a span that survived a re-transcription look valid while pointing at
something else entirely.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Iterable, Iterator, Sequence


@dataclass(frozen=True)
class Word:
    """One word with its timing. The unit that makes click-to-play work."""

    text: str
    start_ms: int
    end_ms: int
    #: ASR confidence where the engine reports it. Used by the vocabulary
    #: tuning report, not by extraction -- a low-confidence word is still
    #: quotable, it just may be wrong.
    confidence: float | None = None


@dataclass(frozen=True)
class Segment:
    """One speaker turn.

    `participant_id` comes from the platform track this segment was
    transcribed from, never from voice analysis (section 2.3). `None` means
    unattributed, which is an honest state the UI must show -- section 4.3: "A
    transcript that silently mis-attributes a commitment is worse than one
    that says 'speaker unknown.'"
    """

    segment_id: str
    participant_id: str | None
    text: str
    start_ms: int
    end_ms: int
    words: tuple[Word, ...] = ()
    #: How speaker attribution was established. Only ever "platform",
    #: "two_leg_stereo", "manual", or "none" -- never anything voice-derived.
    attribution: str = "platform"

    def __post_init__(self) -> None:
        if self.attribution not in {"platform", "two_leg_stereo", "manual", "none"}:
            raise ValueError(
                f"unknown attribution {self.attribution!r}. Speaker identity "
                "comes from platform tracks, stereo channels, or a human -- "
                "never from voice characteristics (docs/legal.md section 2.3)."
            )
        if self.attribution == "none" and self.participant_id is not None:
            raise ValueError(
                "a segment with attribution='none' must not name a participant"
            )


@dataclass(frozen=True)
class TranscriptSpan:
    """A citation into a specific version of a specific transcript."""

    transcript_version: int
    segment_id: str
    start_char: int
    end_char: int
    start_ms: int
    end_ms: int
    #: The quoted evidence. Carried, not derived, so validation can detect a
    #: span that no longer matches the transcript it claims to cite.
    text: str

    @property
    def span_id(self) -> str:
        return f"{self.segment_id}:{self.start_char}-{self.end_char}@v{self.transcript_version}"

    def overlaps(self, other: TranscriptSpan) -> bool:
        if self.segment_id != other.segment_id:
            return False
        return self.start_char < other.end_char and other.start_char < self.end_char


class SpanMismatch(ValueError):
    """A span's quoted text does not match the transcript it cites."""


class Transcript:
    """An immutable, versioned transcript.

    Corrections go through `revise()`, which returns a new Transcript at the
    next version. Nothing mutates a transcript in place, because a stored claim
    citing version 2 has to keep meaning what it meant.
    """

    def __init__(
        self,
        call_id: str,
        segments: Sequence[Segment],
        *,
        version: int = 1,
        asr_engine: str = "unknown",
        asr_model: str = "unknown",
        language: str = "en",
        #: Digest of the vocabulary hints fed to the recogniser (section
        #: 5.4). Stored because a hint list change moves proper nouns,
        #: and a transcript you cannot attribute to a hint list is one
        #: you cannot reproduce.
        vocabulary_hash: str | None = None,
        supersedes: int | None = None,
        revision_note: str | None = None,
    ) -> None:
        self.call_id = call_id
        self.segments: tuple[Segment, ...] = tuple(segments)
        self.version = version
        self.asr_engine = asr_engine
        self.asr_model = asr_model
        self.language = language
        self.vocabulary_hash = vocabulary_hash
        self.supersedes = supersedes
        self.revision_note = revision_note
        self._by_id = {s.segment_id: s for s in self.segments}
        if len(self._by_id) != len(self.segments):
            raise ValueError("segment ids must be unique within a transcript")

    # ------------------------------------------------------------- identity

    @property
    def content_hash(self) -> str:
        """Digest of everything that changes the text.

        Two transcripts with the same hash cite identically, which is what
        lets a re-run skip re-extraction. Timings are included: a span carries
        milliseconds for click-to-play, so a re-alignment that moved them
        would invalidate citations even with identical text.
        """
        h = hashlib.sha256()
        h.update(self.call_id.encode("utf-8"))
        for s in self.segments:
            h.update(s.segment_id.encode("utf-8"))
            h.update((s.participant_id or "\0").encode("utf-8"))
            h.update(s.text.encode("utf-8"))
            h.update(f"{s.start_ms}:{s.end_ms}".encode("utf-8"))
            for w in s.words:
                h.update(f"|{w.start_ms}:{w.end_ms}".encode("utf-8"))
        return h.hexdigest()[:16]

    # -------------------------------------------------------------- access

    def __iter__(self) -> Iterator[Segment]:
        return iter(self.segments)

    def __len__(self) -> int:
        return len(self.segments)

    def segment(self, segment_id: str) -> Segment | None:
        return self._by_id.get(segment_id)

    @property
    def duration_ms(self) -> int:
        return max((s.end_ms for s in self.segments), default=0)

    @property
    def word_count(self) -> int:
        return sum(len(s.text.split()) for s in self.segments)

    def full_text(self) -> str:
        return "\n".join(s.text for s in self.segments)

    def participants(self) -> tuple[str, ...]:
        seen: list[str] = []
        for s in self.segments:
            if s.participant_id and s.participant_id not in seen:
                seen.append(s.participant_id)
        return tuple(seen)

    def is_attributed(self) -> bool:
        """False when any segment lacks a speaker. Drives the UI warning and
        blocks commitment extraction -- you cannot say who committed to what
        from an unattributed transcript (section 4.3)."""
        return all(s.participant_id is not None for s in self.segments)

    def window(self, start_ms: int, end_ms: int) -> tuple[Segment, ...]:
        """Segments overlapping a time window.

        Used by consent detection to look only at the opening minutes
        (section 3.3), so the narrowly-scoped pass really is narrowly scoped.
        """
        return tuple(
            s for s in self.segments if s.start_ms < end_ms and s.end_ms > start_ms
        )

    # -------------------------------------------------------------- spans

    def text_at(self, span: TranscriptSpan) -> str | None:
        """The text this span actually points at now, or None if it cannot."""
        if span.transcript_version != self.version:
            return None
        segment = self._by_id.get(span.segment_id)
        if segment is None:
            return None
        if span.start_char < 0 or span.end_char > len(segment.text):
            return None
        return segment.text[span.start_char : span.end_char]

    def validate_span(self, span: TranscriptSpan) -> None:
        """Raise unless the span still quotes what it says it quotes."""
        actual = self.text_at(span)
        if actual is None:
            raise SpanMismatch(
                f"span {span.span_id} does not resolve in transcript "
                f"v{self.version} of call {self.call_id}"
            )
        if actual != span.text:
            raise SpanMismatch(
                f"span {span.span_id}: transcript says {actual!r}, "
                f"span claims {span.text!r}"
            )

    def make_span(self, segment_id: str, start_char: int, end_char: int) -> TranscriptSpan:
        """Build a span, interpolating timings from the word timestamps.

        Interpolated rather than approximated to the segment bounds: a
        citation that plays the whole four-minute turn instead of the eight
        words that matter is technically grounded and practically useless.
        """
        segment = self._by_id.get(segment_id)
        if segment is None:
            raise KeyError(f"no segment {segment_id!r}")
        if not (0 <= start_char < end_char <= len(segment.text)):
            raise ValueError(
                f"char range [{start_char}, {end_char}) out of bounds for "
                f"segment of length {len(segment.text)}"
            )

        start_ms, end_ms = _interpolate_ms(segment, start_char, end_char)
        return TranscriptSpan(
            transcript_version=self.version,
            segment_id=segment_id,
            start_char=start_char,
            end_char=end_char,
            start_ms=start_ms,
            end_ms=end_ms,
            text=segment.text[start_char:end_char],
        )

    # ------------------------------------------------------------ revision

    def revise(
        self,
        segments: Sequence[Segment],
        *,
        note: str,
        asr_engine: str | None = None,
        asr_model: str | None = None,
    ) -> "Transcript":
        """A corrected transcript at the next version.

        Never mutates. Claims citing the old version keep resolving against
        it, and `migrate_spans` is how they move forward deliberately.
        """
        return Transcript(
            self.call_id,
            segments,
            version=self.version + 1,
            asr_engine=asr_engine or self.asr_engine,
            asr_model=asr_model or self.asr_model,
            language=self.language,
            vocabulary_hash=self.vocabulary_hash,
            supersedes=self.version,
            revision_note=note,
        )


def _interpolate_ms(segment: Segment, start_char: int, end_char: int) -> tuple[int, int]:
    """Map a character range onto milliseconds using the word timings.

    Walks the words accumulating character offsets. Falls back to the segment
    bounds when there are no word timings, which is honest: a span that plays
    the whole turn is worse than one that plays eight words, but better than
    one that plays nothing.
    """
    if not segment.words:
        return segment.start_ms, segment.end_ms

    start_ms: int | None = None
    end_ms: int | None = None
    cursor = 0
    text = segment.text

    for word in segment.words:
        found = text.find(word.text, cursor)
        if found < 0:
            continue
        word_start, word_end = found, found + len(word.text)
        cursor = word_end

        if word_end > start_char and start_ms is None:
            start_ms = word.start_ms
        if word_start < end_char:
            end_ms = word.end_ms

    return (
        start_ms if start_ms is not None else segment.start_ms,
        end_ms if end_ms is not None else segment.end_ms,
    )


def migrate_spans(
    spans: Iterable[TranscriptSpan], new: Transcript
) -> tuple[tuple[TranscriptSpan, ...], tuple[TranscriptSpan, ...]]:
    """Move spans onto a newer transcript version by re-locating their text.

    Returns (migrated, lost). A span whose quoted text no longer appears is
    LOST, not approximated -- a citation that drifted to a different sentence
    is worse than one that is honestly missing, because it looks checked.
    """
    migrated: list[TranscriptSpan] = []
    lost: list[TranscriptSpan] = []

    for span in spans:
        segment = new.segment(span.segment_id)
        candidates = [segment] if segment else list(new.segments)
        placed = False
        for candidate in candidates:
            if candidate is None:
                continue
            index = candidate.text.find(span.text)
            if index >= 0:
                migrated.append(
                    new.make_span(candidate.segment_id, index, index + len(span.text))
                )
                placed = True
                break
        if not placed:
            lost.append(span)

    return tuple(migrated), tuple(lost)
