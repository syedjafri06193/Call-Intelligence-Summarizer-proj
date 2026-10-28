# Ingestion & ASR

Packages: `cis.ingest`, `cis.asr` · Design: §4, §5

## The decision: per-speaker tracks, no diarization

Diarization — working out who spoke when from a mixed recording — is the least reliable stage
in a call pipeline (sales calls have a lot of overlapping speech), and voice-characteristic
speaker identification is what the Otter plaintiffs allege is voiceprint extraction under
BIPA. Both problems disappear with **per-speaker tracks**: each track has one speaker, already
tied to a platform identity.

**CIS never models voices.** Speaker identity comes from one of four non-voice sources:

| `Attribution` | Value | Source |
|---|---|---|
| `PLATFORM_TRACK` | `platform` | Meeting platform delivers one track per participant |
| `TWO_LEG_STEREO` | `two_leg_stereo` | Dialer records rep and other party on separate channels |
| `MANUAL` | `manual` | A human labels segments |
| `NONE` | `none` | Unattributed (opt-in; restricts output) |

`tests/test_no_voiceprints.py` scans `src/` and `eval/` for diarization/speaker-embedding
libraries and terms, so a dependency that would introduce voice biometrics fails CI.

## `ingest(call, client, decision, *, require_attribution=True) -> IngestResult`

Order of attempts:

1. **Verify consent decision** against the current roster (`verify_decision_still_valid`).
2. **Per-speaker tracks** from `client.per_speaker_tracks(external_id)`. If any track has no
   `participant_id`, raise `NoSpeakerAttribution` — the tracks are not really per-speaker.
3. **Two-leg stereo** if `client.mixed_track()` exists, `call.channels == 2` and
   `call.source_type == "dialer_two_leg"`. Requires exactly one `rep` and one other participant;
   channel 0 → rep, channel 1 → other party (URIs suffixed `#channel=0/1`).
4. If `require_attribution` → `NoSpeakerAttribution` (pipeline outcome `SKIPPED`).
5. Otherwise the mixed track with `Attribution.NONE`, `unattributed=True`.

### Consequences of unattributed audio

An unattributed transcript can still be summarised, but the pipeline will not produce:

- `economic_buyer`, `champion`, `commitment`, `next_step` claims (`ATTRIBUTION_DEPENDENT`)
- tasks (an owner cannot be resolved)
- `prospect_stated_only` evidence (speaker role unknown → excluded)

The UI must show "speaker unknown" rather than guess (§4.3).

### Types

```python
@dataclass(frozen=True)
class AudioTrack:
    participant_id: str | None
    uri: str
    attribution: Attribution
    sample_rate_hz: int = 16_000
    channels: int = 1
    duration_ms: int | None = None
    # .is_narrowband -> sample_rate_hz <= 8000

class PlatformClient(Protocol):
    def per_speaker_tracks(self, external_id: str) -> Sequence[AudioTrack]: ...
    def mixed_track(self, external_id: str) -> AudioTrack | None: ...
```

`IngestResult(tracks, attribution, unattributed=False, warnings=())`. Narrowband (8 kHz)
audio adds a warning: published WER figures don't transfer to telephony audio, and proper
nouns suffer most.

### Implementing a platform client

```python
class ZoomClient:
    def per_speaker_tracks(self, external_id):
        files = zoom.recordings(external_id, file_type="audio_only", per_participant=True)
        return [AudioTrack(participant_id=f.participant_id, uri=f.download_url,
                           attribution=Attribution.PLATFORM_TRACK,
                           sample_rate_hz=f.sample_rate) for f in files]

    def mixed_track(self, external_id):
        return None
```

(Illustrative; no real platform client ships in v1.) The client must not download bytes until
`ingest()` has verified the decision — return URIs, fetch later.

## Vocabulary biasing (`asr/vocabulary.py`)

Proper nouns are the worst ASR category and the one that matters most: an economic buyer
transcribed as "Preeya Ramen" is not actionable, and grounding will faithfully quote the wrong
name. Feeding known names to the recogniser is the highest-value tuning (§5.2).

```python
hints = vocabulary_hints(call, crm=None, competitors=(), jargon=(), max_hints=400)
hints.terms      # tuple[str, ...] in priority order, de-duplicated
hints.truncated  # how many were cut by max_hints
hints.hash       # stable, order-independent digest → Transcript.vocabulary_hash
```

Priority (truncation cuts from the end):

1. Participant display names, and each name part
2. CRM account name, then contact names, opportunity names and product names (via `CRMVocabularySource`)
3. Competitors
4. Jargon

De-duplication is case-insensitive; entries shorter than 2 characters and stop-words (`inc`, `llc`, `the` …) are dropped. The hash is stored on the transcript so a
change in hints is visible as a change in input.

```python
class CRMVocabularySource(Protocol):
    def account_name(self, call) -> str | None: ...
    def contact_names(self, call) -> Sequence[str]: ...
    def product_names(self) -> Sequence[str]: ...
    def opportunity_names(self, call) -> Sequence[str]: ...
```

## Transcriber boundary

```python
class Transcriber(Protocol):
    def transcribe(self, tracks: Sequence[AudioTrack], hints: VocabularyHints) -> Transcript: ...
```

Requirements on an implementation:

- One `Segment` per speaker turn, `participant_id` taken from the track.
- **Word-level timings** on every segment (`Segment.words`), or `run()` fails with
  `MissingWordTimings`.
- Merge tracks into time order.
- Set `asr_engine`, `asr_model`, `vocabulary_hash=hints.hash` on the transcript.

v1 ships only `SampleTranscriber` (reads the sample JSON, synthesises word timings at
`MS_PER_WORD = 380`). Real ASR (Whisper or hosted) is on the [roadmap](ROADMAP.md).

## Measuring ASR

Measure ASR separately from extraction (§8.4): WER by source type, and proper-noun error rate
on participant and account names. See [`v1/docs/evaluation.md` §7](evaluation.md).
