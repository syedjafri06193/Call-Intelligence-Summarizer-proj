"""Chunking long transcripts (design.md section 6.2).

A 45-minute call is roughly 7,000 words. That fits in context, but
**extraction quality degrades for material in the middle of long inputs** --
the well-documented "lost in the middle" effect. A single pass over a full
transcript will reliably miss things from minute 20.

Two decisions:

**Overlap by conversational turns, not by token count.** Splitting mid-turn
loses the context that makes a statement interpretable. "We'd need that by
then" is meaningless without the turn before it.

**Chunks keep their segment identity.** A span located inside a chunk has to
resolve against the transcript, not against the chunk, so every chunk carries
enough to translate its local offsets back.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..transcript.model import Segment, Transcript, TranscriptSpan

#: Roughly four characters per token for English prose. Used only to decide
#: where to split -- nothing downstream depends on the estimate being exact,
#: and a real tokenizer would be a dependency for no benefit here.
CHARS_PER_TOKEN = 4


@dataclass(frozen=True)
class Chunk:
    """A window of turns, with enough information to map offsets back."""

    index: int
    segments: tuple[Segment, ...]
    #: Segments that also appear in the previous chunk. Carried so
    #: reconciliation knows a duplicate claim from the overlap is the same
    #: claim rather than two.
    overlap_segment_ids: frozenset[str]
    transcript_version: int

    @property
    def text(self) -> str:
        """The chunk as the model sees it, one line per turn.

        Speaker prefixes are included because who said something is load
        bearing -- section 7.1's MEDDICC definition of Metrics requires the
        number to be "stated by the prospect", and vendor-asserted ROI does
        not count. A chunk without speakers cannot support that distinction.
        """
        return "\n".join(
            f"[{s.participant_id or 'unknown'}] {s.text}" for s in self.segments
        )

    @property
    def start_ms(self) -> int:
        return self.segments[0].start_ms if self.segments else 0

    @property
    def end_ms(self) -> int:
        return self.segments[-1].end_ms if self.segments else 0

    def locate(self, quote: str) -> TranscriptSpan | None:
        """Find a quoted string in this chunk and return a transcript span.

        Fuzzy on whitespace, strict on content (section 16.1). A model
        reformats whitespace constantly -- collapsing a newline, adding a
        space after a dash -- and rejecting those would discard good claims.
        Changing a word is different in kind: that is the model writing rather
        than quoting, and it is exactly what grounding exists to catch.

        Returns None when the quote is not present, which the caller treats as
        a hallucination and logs.
        """
        needle = _normalize(quote)
        if not needle:
            return None

        for segment in self.segments:
            span = _locate_in_segment(segment, needle, self.transcript_version)
            if span is not None:
                return span
        return None


def _normalize(text: str) -> str:
    """Collapse whitespace. The only fuzziness permitted."""
    return re.sub(r"\s+", " ", text).strip()


def _locate_in_segment(
    segment: Segment, needle: str, version: int
) -> TranscriptSpan | None:
    """Locate normalized `needle` in a segment, returning original offsets.

    Builds a map from normalized positions back to original ones so the span
    quotes the transcript's own text, not the model's reformatting of it. A
    span whose `text` came from the model would pass validation against itself
    and prove nothing.
    """
    original = segment.text
    normalized_chars: list[str] = []
    index_map: list[int] = []

    previous_was_space = False
    for i, ch in enumerate(original):
        if ch.isspace():
            if previous_was_space or not normalized_chars:
                continue
            normalized_chars.append(" ")
            index_map.append(i)
            previous_was_space = True
        else:
            normalized_chars.append(ch)
            index_map.append(i)
            previous_was_space = False

    haystack = "".join(normalized_chars).strip()
    # `.strip()` may have removed a leading space; recompute the offset base.
    lead = len("".join(normalized_chars)) - len("".join(normalized_chars).lstrip())

    found = haystack.find(needle)
    if found < 0:
        return None

    start = index_map[found + lead]
    end_index = found + lead + len(needle) - 1
    end = index_map[end_index] + 1

    return TranscriptSpan(
        transcript_version=version,
        segment_id=segment.segment_id,
        start_char=start,
        end_char=end,
        start_ms=_ms_at(segment, start, end)[0],
        end_ms=_ms_at(segment, start, end)[1],
        text=original[start:end],
    )


def _ms_at(segment: Segment, start_char: int, end_char: int) -> tuple[int, int]:
    from ..transcript.model import _interpolate_ms

    return _interpolate_ms(segment, start_char, end_char)


def chunk_by_turns(
    transcript: Transcript,
    *,
    target_tokens: int = 2_500,
    overlap_turns: int = 3,
) -> tuple[Chunk, ...]:
    """Split a transcript into overlapping windows of whole turns.

    Never splits a turn. A turn longer than the target becomes its own
    oversized chunk rather than being cut, because half a sentence extracted
    out of context is worse than a chunk that is 20% over budget.
    """
    if overlap_turns < 0:
        raise ValueError("overlap_turns must be >= 0")
    if not transcript.segments:
        return ()

    target_chars = target_tokens * CHARS_PER_TOKEN
    chunks: list[Chunk] = []
    start = 0
    index = 0

    while start < len(transcript.segments):
        size = 0
        end = start
        while end < len(transcript.segments):
            segment_chars = len(transcript.segments[end].text) + 1
            # Always take at least one turn, however long it is.
            if size and size + segment_chars > target_chars:
                break
            size += segment_chars
            end += 1

        segments = transcript.segments[start:end]
        overlap_ids = (
            frozenset(s.segment_id for s in chunks[-1].segments)
            & frozenset(s.segment_id for s in segments)
            if chunks
            else frozenset()
        )
        chunks.append(
            Chunk(
                index=index,
                segments=segments,
                overlap_segment_ids=overlap_ids,
                transcript_version=transcript.version,
            )
        )
        index += 1

        if end >= len(transcript.segments):
            break

        # Step back by the overlap, but always make forward progress. Without
        # the max(), a chunk shorter than the overlap would loop forever.
        #
        # Overlap is the thing that yields here, deliberately. When the budget
        # only fits one turn, honouring an overlap of 2 would mean either
        # re-emitting that turn forever or emitting chunks at twice the
        # budget. What survives is the guarantee that matters: every turn
        # appears in some chunk, in order, exactly once at minimum.
        start = max(start + 1, end - overlap_turns)

    return tuple(chunks)
