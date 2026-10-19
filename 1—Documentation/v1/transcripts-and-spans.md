# Transcripts & Spans

Package: `cis.transcript` · Design: §5.3, §5.4, §6.3

Grounding works only if a citation can be checked, and keeps meaning the same thing later.
Three properties of the transcript model make that true.

| Property | Why |
|---|---|
| **Word-level timestamps required** | Click-to-play makes a cited quote verifiable in two seconds. A span with character offsets but no milliseconds is a citation nobody can check by listening. |
| **Immutable and versioned** | A correction is a new version. Spans record the version they were computed against, so a claim can never silently point at different text. |
| **Spans carry their text** | Validation checks the transcript still says what the span quotes. Offsets alone would make a span that survived re-transcription look valid while pointing at something else. |

## Types

```python
@dataclass(frozen=True)
class Word:
    text: str
    start_ms: int
    end_ms: int
    confidence: float | None = None   # used for vocabulary tuning reports, not extraction

@dataclass(frozen=True)
class Segment:                         # one speaker turn
    segment_id: str
    participant_id: str | None        # from the platform track; None = unattributed
    text: str
    start_ms: int
    end_ms: int
    words: tuple[Word, ...] = ()
    attribution: str = "platform"

@dataclass(frozen=True)
class TranscriptSpan:                  # a citation
    transcript_version: int
    segment_id: str
    start_char: int                    # offsets into Segment.text
    end_char: int
    start_ms: int
    end_ms: int
    text: str                          # the quoted text, from the transcript itself
    # .span_id  -> "s7:12-58@v1"
    # .overlaps(other) -> same segment and overlapping char range
```

### `Transcript`

```python
Transcript(call_id, segments, *, version=1, asr_engine="unknown", asr_model="unknown",
           language="en", vocabulary_hash=None, supersedes=None, revision_note=None)
```

| Member | Purpose |
|---|---|
| `version`, `supersedes`, `revision_note` | Version chain |
| `content_hash` | Digest of everything that changes the text |
| `segment(id)`, `segments`, `len(t)` | Access |
| `duration_ms`, `word_count`, `full_text`, `participants` | Summary |
| `is_attributed` | `False` if any segment lacks a speaker — blocks commitment extraction and tasks |
| `window(start_ms, end_ms)` | Segments overlapping a time window (used by consent detection) |
| `make_span(segment_id, start_char, end_char)` | Build a span; ms interpolated from word timings |
| `text_at(span)` | What the span points at **now** (or `None`) |
| `validate_span(span)` | Raises `SpanMismatch` unless the version matches and the text still matches |
| `revise(segments, *, note, asr_engine=None, asr_model=None)` | New `Transcript` at `version + 1`; never mutates |

### Timing interpolation

`make_span` walks the segment's `words`, matching each word's text in order, and takes the
first word ending after `start_char` and the last word starting before `end_char`. Without
word timings it falls back to the segment bounds (plays the whole turn — worse than eight
words, better than nothing).

## Migrating spans to a new version

```python
v2 = v1.revise(corrected_segments, note="fixed 'Preeya Ramen' -> 'Priya Raman'")
migrated, lost = migrate_spans(claim.spans, v2)
```

Each span's `text` is re-located in the new version (same segment first, then any segment).
A span whose text no longer appears is returned in `lost` — **never approximated**, because a
citation that drifted to a different sentence looks checked when it isn't.

Implication for corrections: if a correction changes words inside a quoted span, that claim
must be re-extracted, not migrated.

## Using spans in a UI

```text
<audio src=…>            seek to span.start_ms, stop at span.end_ms
highlight segment span.segment_id, chars [start_char, end_char)
show "transcript v{span.transcript_version}" if not the latest
```

## Storage guidance

- Store every version; never update in place.
- Index spans by `(call_id, transcript_version, segment_id)`.
- Persist `content_hash` and `vocabulary_hash` with the transcript for audit.
