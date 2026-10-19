# API Reference

Generated from the v1 source (signatures and the first paragraph of each docstring). Private
names (leading `_`) are omitted. Modules are listed in pipeline order. For behaviour and
rationale, follow the links to the guides.

Import paths assume `PYTHONPATH=src:.` from `v1/`, e.g. `from cis.consent.gate import may_process`
and `from eval.agreement import rater_agreement`.

Legend: **class**, *property*, `CONSTANT`.

## Contents

- [`cis.transcript.model`](#cistranscriptmodel)
- [`cis.consent.model`](#cisconsentmodel)
- [`cis.consent.gate`](#cisconsentgate)
- [`cis.consent.detect`](#cisconsentdetect)
- [`cis.ingest.tracks`](#cisingesttracks)
- [`cis.asr.vocabulary`](#cisasrvocabulary)
- [`cis.redact.pii`](#cisredactpii)
- [`cis.extract.chunk`](#cisextractchunk)
- [`cis.extract.grounded`](#cisextractgrounded)
- [`cis.extract.reconcile`](#cisextractreconcile)
- [`cis.extract.llm_extractor`](#cisextractllm_extractor)
- [`cis.extract.groq_extractor`](#cisextractgroq_extractor)
- [`cis.score.framework`](#cisscoreframework)
- [`cis.score.anchors`](#cisscoreanchors)
- [`cis.crm.writeback`](#ciscrmwriteback)
- [`cis.crm.tasks`](#ciscrmtasks)
- [`cis.crm.idempotency`](#ciscrmidempotency)
- [`cis.workflows.pipeline`](#cisworkflowspipeline)
- [`cis.samples`](#cissamples)
- [`cis.cli`](#ciscli)
- [`eval.agreement`](#evalagreement)
- [`eval.consistency`](#evalconsistency)
- [`eval.extraction_metrics`](#evalextraction_metrics)

## `cis.transcript.model`

> Transcripts, spans, and immutability (design.md sections 5.3, 5.4, 6.3).

### class `Word`

One word with its timing. The unit that makes click-to-play work.

| Field | Type | Default |
|---|---|---|
| `text` | `str` | — |
| `start_ms` | `int` | — |
| `end_ms` | `int` | — |
| `confidence` | `float \| None` | `None` |


### class `Segment`

One speaker turn.

| Field | Type | Default |
|---|---|---|
| `segment_id` | `str` | — |
| `participant_id` | `str \| None` | — |
| `text` | `str` | — |
| `start_ms` | `int` | — |
| `end_ms` | `int` | — |
| `words` | `tuple[Word, ...]` | `()` |
| `attribution` | `str` | `'platform'` |


### class `TranscriptSpan`

A citation into a specific version of a specific transcript.

| Field | Type | Default |
|---|---|---|
| `transcript_version` | `int` | — |
| `segment_id` | `str` | — |
| `start_char` | `int` | — |
| `end_char` | `int` | — |
| `start_ms` | `int` | — |
| `end_ms` | `int` | — |
| `text` | `str` | — |

- *span_id* → `str`
- `overlaps(other: TranscriptSpan)` → `bool`

### class `SpanMismatch`(ValueError)

A span's quoted text does not match the transcript it cites.


### class `Transcript`

An immutable, versioned transcript.

- `__init__(call_id: str, segments: Sequence[Segment], *, version: int=1, asr_engine: str='unknown', asr_model: str='unknown', language: str='en', vocabulary_hash: str | None=None, supersedes: int | None=None, revision_note: str | None=None)` → `None`
- *content_hash* → `str` — Digest of everything that changes the text.
- `segment(segment_id: str)` → `Segment | None`
- *duration_ms* → `int`
- *word_count* → `int`
- `full_text()` → `str`
- `participants()` → `tuple[str, ...]`
- `is_attributed()` → `bool` — False when any segment lacks a speaker. Drives the UI warning and blocks commitment extraction -- you cannot say who committed to what from an unattributed transcript (section 4.3).
- `window(start_ms: int, end_ms: int)` → `tuple[Segment, ...]` — Segments overlapping a time window.
- `text_at(span: TranscriptSpan)` → `str | None` — The text this span actually points at now, or None if it cannot.
- `validate_span(span: TranscriptSpan)` → `None` — Raise unless the span still quotes what it says it quotes.
- `make_span(segment_id: str, start_char: int, end_char: int)` → `TranscriptSpan` — Build a span, interpolating timings from the word timestamps.
- `revise(segments: Sequence[Segment], *, note: str, asr_engine: str | None=None, asr_model: str | None=None)` → `'Transcript'` — A corrected transcript at the next version.

### `migrate_spans`

```python
def migrate_spans(spans: Iterable[TranscriptSpan], new: Transcript) -> tuple[tuple[TranscriptSpan, ...], tuple[TranscriptSpan, ...]]
```

Move spans onto a newer transcript version by re-locating their text.


## `cis.consent.model`

> Consent as first-class state (design.md section 3.1).

| Constant | Value |
|---|---|
| `CONSENTING_METHODS` | `frozenset({ConsentMethod.VERBAL_ACKNOWLEDGED, ConsentMethod.PLATFOR…` |

### class `ConsentMethod`(str, Enum)

How consent was obtained, or why it is absent.

Members: `VERBAL_ACKNOWLEDGED` = 'verbal_acknowledged', `PLATFORM_CONSENT` = 'platform_consent', `WRITTEN_PRIOR` = 'written_prior', `DECLINED` = 'declined', `UNKNOWN` = 'unknown', `WITHDRAWN` = 'withdrawn'

### class `ParticipantRole`(str, Enum)

Members: `REP` = 'rep', `PROSPECT` = 'prospect', `UNKNOWN` = 'unknown'

### class `Participant`

Someone on the call.

| Field | Type | Default |
|---|---|---|
| `participant_id` | `str` | — |
| `display_name` | `str \| None` | `None` |
| `email` | `str \| None` | `None` |
| `role` | `ParticipantRole` | `ParticipantRole.UNKNOWN` |
| `jurisdiction_hint` | `str \| None` | `None` |
| `joined_late` | `bool` | `False` |


### class `ConsentRecord`

One immutable statement about one participant's consent.

| Field | Type | Default |
|---|---|---|
| `call_id` | `str` | — |
| `participant_id` | `str` | — |
| `method` | `ConsentMethod` | — |
| `evidence_ref` | `str \| None` | — |
| `recorded_at` | `dt.datetime` | — |
| `email` | `str \| None` | `None` |
| `jurisdiction_hint` | `str \| None` | `None` |
| `note` | `str \| None` | `None` |

- *permits_processing* → `bool`

### class `ConsentLedger`

Append-only store of consent records.

- `__init__(records: Iterable[ConsentRecord]=())` → `None`
- `append(record: ConsentRecord)` → `None`
- `extend(records: Iterable[ConsentRecord])` → `None`
- `all()` → `tuple[ConsentRecord, ...]`
- `for_call(call_id: str)` → `tuple[ConsentRecord, ...]`
- `history(call_id: str, participant_id: str)` → `tuple[ConsentRecord, ...]` — Every record for one participant, oldest first.
- `current(call_id: str, participant_id: str)` → `ConsentRecord | None` — The record in force now, or None if we have never heard anything.
- `effective(call_id: str, participant_id: str)` → `ConsentMethod` — What the gate should act on. Absent means UNKNOWN, never consented.
- `withdraw(call_id: str, participant_id: str, *, at: dt.datetime, note: str | None=None)` → `ConsentRecord` — Record a withdrawal. Appends; never edits.

### class `Call`

A call, before anything has been fetched or processed.

| Field | Type | Default |
|---|---|---|
| `call_id` | `str` | — |
| `external_id` | `str` | — |
| `participants` | `tuple[Participant, ...]` | — |
| `scheduled_at` | `dt.datetime` | — |
| `source_type` | `str` | `'zoom_cloud'` |
| `channels` | `int` | `1` |
| `announcement_span_id` | `str \| None` | `None` |

- `participant(participant_id: str)` → `Participant | None`

### `snapshot`

```python
def snapshot(ledger: ConsentLedger, call: Call) -> Mapping[str, ConsentMethod]
```

Current effective consent for every participant on a call.


## `cis.consent.gate`

> The consent gate (design.md section 3.2).

### class `ConsentMode`(str, Enum)

Members: `ALL_PARTY` = 'all_party', `ONE_PARTY` = 'one_party'

### class `ConsentPolicy`

Per-customer configuration, with the relaxation deliberately awkward.

| Field | Type | Default |
|---|---|---|
| `mode` | `ConsentMode` | `ConsentMode.ALL_PARTY` |
| `authorised_by` | `str \| None` | `None` |
| `authorised_at` | `dt.datetime \| None` | `None` |
| `late_joiners_require_consent` | `bool` | `True` |
| `policy_version` | `str` | `'1'` |


### class `Blocker`(str, Enum)

Members: `NO_CONSENT_RECORD` = 'no_consent_record', `DECLINED` = 'declined', `WITHDRAWN` = 'withdrawn', `LATE_JOINER` = 'late_joiner', `NO_PARTICIPANTS` = 'no_participants'

### class `ParticipantBlock`

| Field | Type | Default |
|---|---|---|
| `participant_id` | `str` | — |
| `display_name` | `str \| None` | — |
| `blocker` | `Blocker` | — |
| `detail` | `str` | — |


### class `ProcessingDecision`

The gate's answer. Carried alongside the call through the pipeline.

| Field | Type | Default |
|---|---|---|
| `allowed` | `bool` | — |
| `call_id` | `str` | — |
| `mode` | `ConsentMode` | — |
| `policy_version` | `str` | — |
| `decided_at` | `dt.datetime` | — |
| `roster_fingerprint` | `str` | — |
| `reason` | `str` | `''` |
| `remediation` | `str` | `''` |
| `blocks` | `tuple[ParticipantBlock, ...]` | `()` |
| `consenting_participants` | `tuple[str, ...]` | `()` |

- `require()` → `None` — Raise unless processing is permitted.

### class `ConsentDenied`(RuntimeError)

- `__init__(decision: ProcessingDecision)` → `None`

### `roster_fingerprint`

```python
def roster_fingerprint(participants: tuple[Participant, ...]) -> str
```

Order-independent digest of who is on the call.

### `may_process`

```python
def may_process(call: Call, ledger: ConsentLedger, policy: ConsentPolicy | None=None, *, now: dt.datetime | None=None) -> ProcessingDecision
```

Decide whether this call may be fetched and processed.

### `verify_decision_still_valid`

```python
def verify_decision_still_valid(decision: ProcessingDecision, call: Call) -> None
```

Re-check a decision against the current roster.


## `cis.consent.detect`

> Detecting verbal consent in the opening minutes (design.md section 3.3).

| Constant | Value |
|---|---|
| `CONSENT_WINDOW_MS` | `90000` |

### class `DeletionRequired`(RuntimeError)

No usable consent was found. Everything fetched must be deleted now.

- `__init__(call_id: str, reason: str)` → `None`

### class `DetectedAnnouncement`

| Field | Type | Default |
|---|---|---|
| `segment_id` | `str` | — |
| `participant_id` | `str \| None` | — |
| `span` | `TranscriptSpan` | — |
| `text` | `str` | — |


### class `ConsentDetectionRun`

A record of what the narrow pass examined.

| Field | Type | Default |
|---|---|---|
| `call_id` | `str` | — |
| `window_ms` | `int` | — |
| `segments_examined` | `int` | — |
| `ran_at` | `dt.datetime` | — |
| `announcement` | `DetectedAnnouncement \| None` | — |
| `records` | `tuple[ConsentRecord, ...]` | — |
| `unresolved_participants` | `tuple[str, ...]` | — |
| `detector_version` | `str` | `'1'` |


### `find_announcement`

```python
def find_announcement(transcript: Transcript, *, window_ms: int=CONSENT_WINDOW_MS) -> DetectedAnnouncement | None
```

Locate the recording announcement within the opening window.

### `classify_response`

```python
def classify_response(text: str) -> ConsentMethod
```

Classify one utterance.

### `detect_consent`

```python
def detect_consent(call: Call, transcript: Transcript, *, now: dt.datetime | None=None, window_ms: int=CONSENT_WINDOW_MS) -> ConsentDetectionRun
```

The narrowly-scoped pass.


## `cis.ingest.tracks`

> Audio ingestion and speaker attribution (design.md section 4.2).

### class `Attribution`(str, Enum)

How speaker identity was established. All four are non-voice.

Members: `PLATFORM_TRACK` = 'platform', `TWO_LEG_STEREO` = 'two_leg_stereo', `MANUAL` = 'manual', `NONE` = 'none'

### class `AudioTrack`

One audio stream with its speaker already known.

| Field | Type | Default |
|---|---|---|
| `participant_id` | `str \| None` | — |
| `uri` | `str` | — |
| `attribution` | `Attribution` | — |
| `sample_rate_hz` | `int` | `16000` |
| `channels` | `int` | `1` |
| `duration_ms` | `int \| None` | `None` |

- *is_narrowband* → `bool` — 8 kHz telephony. Worth surfacing: section 5.1 notes published WER figures are measured on clean read speech and do not transfer, and the gap is widest on narrowband dialer audio.

### class `NoSpeakerAttribution`(RuntimeError)

Mixed single-channel audio with no way to attribute speakers.

- `__init__(call_id: str, detail: str)` → `None`

### class `PlatformClient`(Protocol)

What a meeting platform has to provide.

- `per_speaker_tracks(external_id: str)` → `Sequence[AudioTrack]`
- `mixed_track(external_id: str)` → `AudioTrack | None`

### class `IngestResult`

| Field | Type | Default |
|---|---|---|
| `tracks` | `tuple[AudioTrack, ...]` | — |
| `attribution` | `Attribution` | — |
| `unattributed` | `bool` | `False` |
| `warnings` | `tuple[str, ...]` | `()` |


### `ingest`

```python
def ingest(call: Call, client: PlatformClient, decision: ProcessingDecision, *, require_attribution: bool=True) -> IngestResult
```

Fetch audio for a call.


## `cis.asr.vocabulary`

> Vocabulary biasing (design.md section 5.2).

| Constant | Value |
|---|---|
| `DEFAULT_MAX_HINTS` | `400` |

### class `VocabularyHints`

| Field | Type | Default |
|---|---|---|
| `terms` | `tuple[str, ...]` | — |
| `truncated` | `int` | `0` |

- *hash* → `str` — Stable digest, order-independent.

### class `CRMVocabularySource`(Protocol)

Only what section 5.2 reads.

- `account_name(call: Call)` → `str | None`
- `contact_names(call: Call)` → `Sequence[str]`
- `product_names()` → `Sequence[str]`
- `opportunity_names(call: Call)` → `Sequence[str]`

### `vocabulary_hints`

```python
def vocabulary_hints(call: Call, crm: CRMVocabularySource | None=None, *, competitors: Iterable[str]=(), jargon: Iterable[str]=(), max_hints: int=DEFAULT_MAX_HINTS) -> VocabularyHints
```

Build the hint list for one call, in priority order.


## `cis.redact.pii`

> PII redaction before third-party processing (design.md section 10.2).

### class `PiiKind`(str, Enum)

Members: `EMAIL` = 'EMAIL', `PHONE` = 'PHONE', `CARD` = 'CARD', `GOVERNMENT_ID` = 'GOVID', `ADDRESS` = 'ADDRESS', `PERSON` = 'PERSON'

### class `Detection`

| Field | Type | Default |
|---|---|---|
| `kind` | `PiiKind` | — |
| `start` | `int` | — |
| `end` | `int` | — |
| `text` | `str` | — |

- *length* → `int`

### class `RestorationMap`

Placeholder -> original text. Local only.

- `__init__(mapping: Mapping[str, str] | None=None)` → `None`
- `add(placeholder: str, original: str)` → `None`
- `original(placeholder: str)` → `str | None`
- `placeholders()` → `tuple[str, ...]`

### class `Redaction`

The result of redacting one piece of text.

| Field | Type | Default |
|---|---|---|
| `text` | `str` | — |
| `restoration` | `RestorationMap` | — |
| `detections` | `tuple[Detection, ...]` | `()` |

- *counts* → `Mapping[PiiKind, int]`

### `roster_names`

```python
def roster_names(display_names: Iterable[str | None]) -> tuple[str, ...]
```

Expand roster display names into what a transcript actually says.

### class `Redactor`

Consistent, reversible redaction across a whole call.

- `__init__(*, names: Iterable[str]=())` → `None`
- *restoration* → `RestorationMap`
- `placeholder_for(kind: PiiKind, original: str)` → `str` — Stable placeholder for one exact string.
- `detect(text: str)` → `list[Detection]`
- `redact(text: str)` → `Redaction` — Replace detected PII, recording how to put it back.
- `restore(text: str)` → `str` — Put the originals back, for display to someone entitled to see them.

### `redact`

```python
def redact(text: str, *, names: Iterable[str]=()) -> tuple[str, RestorationMap]
```

Section 10.2's signature, for one-shot use.

### class `RedactionMetrics`

Both error directions, never averaged (section 10.2).

| Field | Type | Default |
|---|---|---|
| `true_positives` | `int` | — |
| `false_positives` | `int` | — |
| `false_negatives` | `int` | — |
| `by_kind` | `Mapping[PiiKind, tuple[int, int, int]]` | `dc_field(default_factory=dict)` |

- *recall* → `float` — Of the PII that was there, how much was redacted?
- *precision* → `float` — Of what was redacted, how much was actually PII?
- `report()` → `str`

### `measure`

```python
def measure(text: str, gold: Sequence[Detection], *, names: Iterable[str]=()) -> RedactionMetrics
```

Score a redactor against hand-labelled PII spans.


## `cis.extract.chunk`

> Chunking long transcripts (design.md section 6.2).

| Constant | Value |
|---|---|
| `CHARS_PER_TOKEN` | `4` |

### class `Chunk`

A window of turns, with enough information to map offsets back.

| Field | Type | Default |
|---|---|---|
| `index` | `int` | — |
| `segments` | `tuple[Segment, ...]` | — |
| `overlap_segment_ids` | `frozenset[str]` | — |
| `transcript_version` | `int` | — |

- *text* → `str` — The chunk as the model sees it, one line per turn.
- *start_ms* → `int`
- *end_ms* → `int`
- `locate(quote: str)` → `TranscriptSpan | None` — Find a quoted string in this chunk and return a transcript span.

### `chunk_by_turns`

```python
def chunk_by_turns(transcript: Transcript, *, target_tokens: int=2500, overlap_turns: int=3) -> tuple[Chunk, ...]
```

Split a transcript into overlapping windows of whole turns.


## `cis.extract.grounded`

> Span-grounded extraction (design.md sections 6.3, 6.4, 16.1).

| Constant | Value |
|---|---|
| `EXTRACTOR_VERSION` | `'1.0.0'` |
| `ATTRIBUTION_DEPENDENT` | `frozenset({Field.ECONOMIC_BUYER, Field.CHAMPION, Field.COMMITMENT, …` |
| `GROUNDED_EXTRACTION_PROMPT` | `'You are extracting facts from a sales discovery call transcript.\n…` |

### class `Field`(str, Enum)

What extraction pulls out (section 6.1).

Members: `ECONOMIC_BUYER` = 'economic_buyer', `METRIC` = 'metric', `BUDGET` = 'budget', `TIMELINE` = 'timeline', `DECISION_PROCESS` = 'decision_process', `DECISION_CRITERIA` = 'decision_criteria', `PAIN` = 'pain', `CHAMPION` = 'champion', `COMPETITOR` = 'competitor', `COMMITMENT` = 'commitment', `NEXT_STEP` = 'next_step', `OBJECTION` = 'objection'

### class `UngroundedClaim`(ValueError)

A claim arrived with no supporting span.


### class `GroundedClaim`

A fact, and the transcript text that supports it.

| Field | Type | Default |
|---|---|---|
| `field` | `Field` | — |
| `value` | `str` | — |
| `spans` | `tuple[TranscriptSpan, ...]` | — |
| `confidence` | `float` | — |
| `extractor_version` | `str` | `EXTRACTOR_VERSION` |
| `subject_participant_id` | `str \| None` | `None` |
| `source_chunks` | `tuple[int, ...]` | `()` |

- *quote* → `str` — The evidence, as the rep will see it.
- *earliest_ms* → `int`

### class `ExtractionStats`

Hallucination monitoring (section 16.1).

| Field | Type | Default |
|---|---|---|
| `fields_returned` | `int` | `0` |
| `claims_accepted` | `int` | `0` |
| `quotes_offered` | `int` | `0` |
| `quotes_located` | `int` | `0` |
| `claims_dropped_ungrounded` | `int` | `0` |
| `claims_dropped_unattributed` | `int` | `0` |
| `dropped_quotes` | `list[tuple[str, str]]` | `field(default_factory=list)` |

- *hallucination_rate* → `float` — Fraction of offered quotes that were not in the transcript.
- `merge(other: 'ExtractionStats')` → `None`

### class `TextRedactor`(Protocol)

Just enough of `redact.pii.Redactor` to be swapped or omitted.

- `redact(text: str)`
- `restore(text: str)` → `str`

### class `ExtractionModel`(Protocol)

The LLM boundary.

- `extract(chunk_text: str, fields: Sequence[Field])` → `dict` — Return {field_name: {"value": str, "quotes": [str], "confidence": float}}.

### `extract_chunk`

```python
def extract_chunk(chunk: Chunk, transcript: Transcript, model: ExtractionModel, fields: Sequence[Field], *, attributed: bool=True, redactor: TextRedactor | None=None) -> tuple[tuple[GroundedClaim, ...], ExtractionStats]
```

Extract from one chunk, discarding anything that is not grounded.

### `validate`

```python
def validate(claim: GroundedClaim, transcript: Transcript) -> None
```

Raise unless every span still quotes the transcript exactly.

### `validate_all`

```python
def validate_all(claims: Sequence[GroundedClaim], transcript: Transcript) -> tuple[tuple[GroundedClaim, ...], tuple[tuple[GroundedClaim, str], ...]]
```

Partition claims into valid and rejected, with reasons.


## `cis.extract.reconcile`

> Reconciling per-chunk extractions (design.md section 6.2).

| Constant | Value |
|---|---|
| `SINGLE_VALUED` | `frozenset({Field.ECONOMIC_BUYER, Field.BUDGET, Field.TIMELINE, Fiel…` |

### class `Contradiction`

Two or more incompatible values for a single-valued field.

| Field | Type | Default |
|---|---|---|
| `field` | `Field` | — |
| `claims` | `tuple[GroundedClaim, ...]` | — |

- *values* → `tuple[str, ...]`
- `describe()` → `str`

### class `Extraction`

The reconciled result for a call.

| Field | Type | Default |
|---|---|---|
| `call_id` | `str` | — |
| `transcript_version` | `int` | — |
| `claims` | `tuple[GroundedClaim, ...]` | — |
| `contradictions` | `tuple[Contradiction, ...]` | — |
| `extractor_version` | `str` | — |

- `by_field(field: Field)` → `tuple[GroundedClaim, ...]`
- `has(field: Field)` → `bool`
- *contradicted_fields* → `frozenset[Field]`

### `normalize_value`

```python
def normalize_value(field: Field, value: str) -> str
```

Canonical form for deduplication.

### `reconcile`

```python
def reconcile(call_id: str, transcript_version: int, partials: Sequence[Sequence[GroundedClaim]], *, extractor_version: str='1.0.0') -> Extraction
```

Merge per-chunk claims into one extraction.


## `cis.extract.llm_extractor`

| Constant | Value |
|---|---|
| `DEFAULT_MODEL` | `os.getenv('CIS_MODEL', 'claude-sonnet-5-5')` |
| `SYSTEM_PROMPT` | `GROUNDED_EXTRACTION_PROMPT + '\nAdditional rules:\n- The transcript…` |
| `SUPPORT_LEVELS` | `{'explicit': 0.9, 'implied': 0.5}` |

### `clean_claims`

```python
def clean_claims(args: dict, allowed: set[str]) -> dict
```

Shared by every provider: validate shape, map support level to confidence.

### `build_tool`

```python
def build_tool(fields: Sequence[Field]) -> dict
```

One optional property per field. Omitted property == not found.

### `build_user_message`

```python
def build_user_message(chunk_text: str, fields: Sequence[Field]) -> str
```

### class `LLMExtractor`

Implements ExtractionModel.

- `__init__(client: Any=None, model: str=DEFAULT_MODEL, max_tokens: int=2000)`
- `extract(chunk_text: str, fields: Sequence[Field])` → `dict`


## `cis.extract.groq_extractor`

| Constant | Value |
|---|---|
| `DEFAULT_GROQ_MODEL` | `os.getenv('CIS_GROQ_MODEL', 'llama-3.3-70b-versatile')` |

### class `GroqExtractor`

Implements ExtractionModel using Groq's chat completions API.

- `__init__(client: Any=None, model: str=DEFAULT_GROQ_MODEL, max_tokens: int=2000, attempts: int=2)`
- `extract(chunk_text: str, fields: Sequence[Field])` → `dict`


## `cis.score.framework`

> Loading a versioned framework definition (design.md section 7.1).

| Constant | Value |
|---|---|
| `NO_EVIDENCE_LEVEL` | `0` |

### class `FrameworkError`(ValueError)

The framework file is not usable. Raised at load time, on purpose.


### class `CriterionKind`(str, Enum)

Whether humans agree about this criterion (section 8.1).

Members: `FACTUAL` = 'factual', `JUDGMENT` = 'judgment'

### class `Criterion`

| Field | Type | Default |
|---|---|---|
| `key` | `str` | — |
| `label` | `str` | — |
| `kind` | `CriterionKind` | — |
| `definition` | `str` | — |
| `evidence_requires` | `str` | — |
| `anchors` | `Mapping[int, str]` | — |
| `fields` | `tuple[Field, ...]` | — |
| `prospect_stated_only` | `bool` | `False` |
| `warning` | `str \| None` | `None` |

- *max_level* → `int`
- *requires_human_review* → `bool`
- `anchor(level: int)` → `str`
- `describe_anchors()` → `str` — The scale as the judge sees it, one line per level.

### class `Framework`

| Field | Type | Default |
|---|---|---|
| `name` | `str` | — |
| `version` | `int` | — |
| `criteria` | `tuple[Criterion, ...]` | — |
| `notes` | `str \| None` | `None` |
| `source_path` | `str \| None` | `None` |

- *framework_id* → `str` — What a stored score points at. `MEDDICC@3`.
- `get(key: str)` → `Criterion | None`
- *keys* → `tuple[str, ...]`
- *factual* → `tuple[Criterion, ...]`
- *judgment* → `tuple[Criterion, ...]`
- *fields_used* → `frozenset[Field]` — Every extraction field some criterion depends on.

### `load_framework`

```python
def load_framework(path: str | Path) -> Framework
```

Read and validate a framework YAML file.


## `cis.score.anchors`

> Scoring against anchors (design.md sections 7.2, 7.3, 7.4).

| Constant | Value |
|---|---|
| `JUDGE_VERSION` | `'1.0.0'` |
| `JUDGE_TEMPERATURE` | `0.0` |
| `NO_EVIDENCE_RATIONALE` | `'no supporting evidence found'` |
| `JUDGE_PREAMBLE` | `'You are assigning a level on a written ordinal scale for one crite…` |

### class `InvalidAnchorLevel`(ValueError)

The judge returned a level that is not on this criterion's scale.


### class `EvidenceItem`

One claim, with who said it resolved from the roster.

| Field | Type | Default |
|---|---|---|
| `claim` | `GroundedClaim` | — |
| `stated_by` | `str \| None` | — |
| `stated_by_role` | `ParticipantRole` | — |

- *quote* → `str`

### class `Evidence`

Everything the judge is allowed to see about one criterion.

| Field | Type | Default |
|---|---|---|
| `criterion_key` | `str` | — |
| `items` | `tuple[EvidenceItem, ...]` | — |
| `excluded` | `tuple[tuple[EvidenceItem, str], ...]` | `()` |

- *spans* → `tuple[TranscriptSpan, ...]`
- `render(redactor: 'TextRedactor | None'=None)` → `str` — The evidence block, as the judge sees it.

### `gather_evidence`

```python
def gather_evidence(criterion: Criterion, claims: Sequence[GroundedClaim], transcript: Transcript, call: Call) -> Evidence
```

Collect the claims that can support a criterion, and say who said them.

### class `CriterionScore`

One criterion's level, with the evidence that produced it.

| Field | Type | Default |
|---|---|---|
| `criterion_key` | `str` | — |
| `level` | `int` | — |
| `rationale` | `str` | — |
| `spans` | `tuple[TranscriptSpan, ...]` | — |
| `framework_id` | `str` | — |
| `requires_review` | `bool` | — |
| `judge_version` | `str` | `JUDGE_VERSION` |
| `excluded` | `tuple[str, ...]` | `()` |

- *has_evidence* → `bool`
- *quote* → `str`

### class `CallScore`

A whole call scored against one version of one framework.

| Field | Type | Default |
|---|---|---|
| `call_id` | `str` | — |
| `framework_id` | `str` | — |
| `transcript_version` | `int` | — |
| `extractor_version` | `str` | — |
| `scores` | `tuple[CriterionScore, ...]` | — |
| `judge_version` | `str` | `JUDGE_VERSION` |
| `temperature` | `float` | `JUDGE_TEMPERATURE` |

- *levels* → `Mapping[str, int]`
- *needs_review* → `tuple[CriterionScore, ...]`
- *auto_writable* → `tuple[CriterionScore, ...]`
- *coverage* → `float` — Fraction of criteria with any evidence at all.

### class `TextRedactor`(Protocol)

Just enough of `redact.pii.Redactor`. See `extract.grounded`.

- `redact(text: str)`
- `restore(text: str)` → `str`

### class `AnchorJudge`(Protocol)

The model boundary for scoring.

- `choose_level(prompt: str)` → `dict` — Return {"level": int, "rationale": str}.

### `build_prompt`

```python
def build_prompt(criterion: Criterion, evidence: Evidence, redactor: 'TextRedactor | None'=None) -> str
```

Render the judge prompt.

### `score_criterion`

```python
def score_criterion(criterion: Criterion, evidence: Evidence, judge: AnchorJudge, *, framework_id: str, redactor: 'TextRedactor | None'=None) -> CriterionScore
```

Assign one criterion's level.

### `score_call`

```python
def score_call(extraction: Extraction, framework: Framework, judge: AnchorJudge, transcript: Transcript, call: Call, *, redactor: 'TextRedactor | None'=None) -> CallScore
```

Score a call against every criterion in a framework.


## `cis.crm.writeback`

> CRM writeback (design.md sections 9.1, 9.2, 9.5).

| Constant | Value |
|---|---|
| `AUTO_PRECISION_FLOOR` | `0.95` |
| `AI_FIELD_PATTERN` | `('ai_', 'AI_')` |

### class `WriteMode`(str, Enum)

Members: `AUTO` = 'auto', `REVIEW` = 'review', `SUGGEST_ONLY` = 'suggest_only'

### class `WriteAction`(str, Enum)

Members: `WRITTEN` = 'written', `APPENDED` = 'appended', `SUGGESTED` = 'suggested', `SKIPPED` = 'skipped', `FAILED` = 'failed'

### class `PolicyError`(ValueError)

A field policy that cannot be honoured.


### class `FieldPolicy`

Section 9.2's policy, with the promotion rule enforced.

| Field | Type | Default |
|---|---|---|
| `field` | `str` | — |
| `mode` | `WriteMode` | — |
| `min_confidence` | `float` | `0.0` |
| `requires_span` | `bool` | `True` |
| `measured_precision` | `float \| None` | `None` |
| `append` | `bool` | `False` |

- *is_dedicated_ai_field* → `bool`

### class `WriteResult`

| Field | Type | Default |
|---|---|---|
| `action` | `WriteAction` | — |
| `field` | `str` | — |
| `reason` | `str` | — |
| `suggestion` | `str \| None` | `None` |
| `record_id` | `str \| None` | `None` |
| `spans` | `tuple` | `()` |

- *landed* → `bool`

### `attribution_marker`

```python
def attribution_marker(call_id: str) -> str
```

The marker on appended content (section 9.1, pattern 2).

### `write_field`

```python
def write_field(crm: CRM, record_id: str, field: str, value: str, claim: GroundedClaim | None, policy: FieldPolicy, *, call_id: str='') -> WriteResult
```

Write one field, or explain why it was not written.

### class `RateLimited`(RuntimeError)

The CRM asked us to slow down. Carries the hint if it gave one.

- `__init__(retry_after: float=1.0)` → `None`

### class `JournalEntry`

| Field | Type | Default |
|---|---|---|
| `key` | `str` | — |
| `payload` | `Mapping[str, Any]` | — |
| `attempts` | `int` | `0` |
| `last_error` | `str \| None` | `None` |
| `done` | `bool` | `False` |

- *failed* → `bool`

### class `WritebackJournal`

Durable record of every write attempt (section 9.5).

| Field | Type | Default |
|---|---|---|
| `entries` | `list[JournalEntry]` | `dc_field(default_factory=list)` |

- `record(key: str, payload: Mapping[str, Any])` → `JournalEntry`
- `pending()` → `tuple[JournalEntry, ...]` — Everything that has not landed. Surfaced in the UI, not logged.
- `failures()` → `tuple[JournalEntry, ...]`
- *all_landed* → `bool`

### `with_backoff`

```python
def with_backoff(fn: Callable[[], Any], *, attempts: int=5, base_delay: float=0.5, sleep: Callable[[float], None]=time.sleep) -> Any
```

Retry on rate limiting, backing off exponentially.

### `write_payload`

```python
def write_payload(crm: CRM, journal: WritebackJournal, call_id: str, kind: str, payload: Mapping[str, Any], *, provenance: Provenance | None=None, discriminator: str | None=None, sleep: Callable[[float], None]=time.sleep) -> JournalEntry
```

Upsert one object, journalled and retried.

### `summarise`

```python
def summarise(results: Sequence[WriteResult]) -> Mapping[WriteAction, int]
```


## `cis.crm.tasks`

> Next steps become tasks -- carefully (design.md section 9.3).

| Constant | Value |
|---|---|
| `TASK_THRESHOLD` | `0.7` |
| `TASK_FIELDS` | `frozenset({Field.COMMITMENT, Field.NEXT_STEP})` |

### class `TaskMustBeConfirmed`(ValueError)

Someone set `requires_confirmation=False`.


### class `ProposedTask`

A task, pending a human's nod.

| Field | Type | Default |
|---|---|---|
| `title` | `str` | — |
| `evidence` | `tuple[TranscriptSpan, ...]` | — |
| `confidence` | `float` | — |
| `due` | `dt.date \| None` | `None` |
| `owner_participant_id` | `str \| None` | `None` |
| `owner_email` | `str \| None` | `None` |
| `requires_confirmation` | `bool` | `True` |
| `due_note` | `str \| None` | `None` |

- *quote* → `str`

### `resolve_due`

```python
def resolve_due(text: str, call_date: dt.date) -> tuple[dt.date | None, str | None]
```

Resolve a relative date against the call date.

### `resolve_owner`

```python
def resolve_owner(claim: GroundedClaim, call: Call) -> tuple[str | None, str | None]
```

Who owns this task.

### `propose_tasks`

```python
def propose_tasks(claims: Sequence[GroundedClaim], call: Call, *, attributed: bool=True, threshold: float=TASK_THRESHOLD) -> tuple[ProposedTask, ...]
```

Turn commitments into proposed tasks.

### `rep_of`

```python
def rep_of(call: Call) -> str | None
```


## `cis.crm.idempotency`

> Idempotent writeback keys (design.md section 9.4).

### class `Provenance`

What produced the values currently in a record.

| Field | Type | Default |
|---|---|---|
| `call_id` | `str` | — |
| `transcript_version` | `int` | — |
| `extractor_version` | `str` | — |
| `framework_id` | `str \| None` | `None` |
| `judge_version` | `str \| None` | `None` |

- `as_fields()` → `Mapping[str, Any]`

### `identity_key`

```python
def identity_key(call_id: str, kind: str, discriminator: str | None=None) -> str
```

The external key for one CRM object.

### `task_fingerprint`

```python
def task_fingerprint(title: str) -> str
```

A stable discriminator for one commitment.

### class `CRM`(Protocol)

The CRM boundary.

- `find_by_external_key(key: str)` → `Any | None`
- `create(payload: Mapping[str, Any], *, external_key: str)` → `Any`
- `update(record_id: str, payload: Mapping[str, Any])` → `Any`
- `get_field(record_id: str, field: str)` → `str | None`
- `is_ai_authored(record_id: str, field: str)` → `bool`

### `upsert`

```python
def upsert(crm: CRM, key: str, payload: Mapping[str, Any], *, provenance: Provenance | None=None) -> Any
```

Create or update by external key. Never both.


## `cis.workflows.pipeline`

> The pipeline, end to end (design.md section 13.2).

### class `Stage`(str, Enum)

Members: `CONSENT` = 'consent', `INGEST` = 'ingest', `TRANSCRIBE` = 'transcribe', `REDACT` = 'redact', `EXTRACT` = 'extract', `SCORE` = 'score', `STAGE_FOR_REVIEW` = 'stage_for_review'

### class `Outcome`(str, Enum)

Members: `COMPLETED` = 'completed', `SKIPPED` = 'skipped', `FAILED` = 'failed'

### class `Transcriber`(Protocol)

The ASR boundary.

- `transcribe(tracks: Sequence[AudioTrack], hints: VocabularyHints)` → `Transcript`

### class `PipelineResult`

| Field | Type | Default |
|---|---|---|
| `call_id` | `str` | — |
| `outcome` | `Outcome` | — |
| `reason` | `str` | `''` |
| `failed_stage` | `Stage \| None` | `None` |
| `decision` | `ProcessingDecision \| None` | `None` |
| `ingest_result` | `IngestResult \| None` | `None` |
| `transcript` | `Transcript \| None` | `None` |
| `extraction` | `Extraction \| None` | `None` |
| `score` | `CallScore \| None` | `None` |
| `tasks` | `tuple[ProposedTask, ...]` | `()` |
| `stats` | `ExtractionStats` | `dc_field(default_factory=ExtractionSt…` |
| `redactions` | `int` | `0` |
| `warnings` | `tuple[str, ...]` | `()` |

- *ok* → `bool`
- *needs_review* → `tuple` — Everything a human has to look at before it leaves the product.

### `default_fields`

```python
def default_fields(framework: Framework) -> tuple[Field, ...]
```

What to extract when the caller does not say.

### class `MissingWordTimings`(RuntimeError)

A transcript arrived without word-level timings (section 5.3).


### `run`

```python
def run(call: Call, ledger: ConsentLedger, client: PlatformClient, transcriber: Transcriber, model: ExtractionModel, judge: AnchorJudge, framework: Framework, *, policy: ConsentPolicy | None=None, fields: Sequence[Field] | None=None, target_tokens: int=2500, overlap_turns: int=3, now: dt.datetime | None=None, redact_before_model: bool=True, require_attribution: bool=True) -> PipelineResult
```

Process one call.


## `cis.samples`

> Loading the bundled sample call, and the scripted stand-ins for the models.

| Constant | Value |
|---|---|
| `SAMPLES` | `Path(__file__).resolve().parents[2] / 'samples'` |
| `DEFAULT_SAMPLE` | `SAMPLES / 'discovery_call.json'` |
| `MS_PER_WORD` | `380` |

### class `SampleCall`

- `__init__(call: Call, ledger: ConsentLedger, transcript: Transcript)`
- *tracks* → `tuple[AudioTrack, ...]` — Per-speaker tracks, as a platform that supports them would return.

### `load_sample`

```python
def load_sample(path: Path | str=DEFAULT_SAMPLE) -> SampleCall
```

### class `SamplePlatform`

A platform client backed by the sample.

- `__init__(sample: SampleCall)`
- `per_speaker_tracks(external_id: str)`
- `mixed_track(external_id: str)`

### class `SampleTranscriber`

- `__init__(sample: SampleCall)`
- `transcribe(tracks, hints)`

### class `ScriptedModel`

A stand-in extractor. No network, no key, no model.

- `__init__(*, fabricate: bool=True)`
- `extract(chunk_text: str, fields)` → `dict`

### class `ScriptedJudge`

Picks a level from the evidence count. Not a model; not pretending to be.

- `__init__()`
- `choose_level(prompt: str)` → `dict`


## `cis.cli`

> Command line entry point.

| Constant | Value |
|---|---|
| `FRAMEWORKS` | `Path(__file__).resolve().parents[2] / 'docs' / 'frameworks'` |
| `DEFAULT_FRAMEWORK` | `FRAMEWORKS / 'meddicc.yaml'` |
| `RULE` | `'-' * 72` |

### `cmd_demo`

```python
def cmd_demo(args: argparse.Namespace) -> int
```

### `cmd_gate`

```python
def cmd_gate(args: argparse.Namespace) -> int
```

### `cmd_framework`

```python
def cmd_framework(args: argparse.Namespace) -> int
```

### `cmd_agreement`

```python
def cmd_agreement(args: argparse.Namespace) -> int
```

### `build_parser`

```python
def build_parser() -> argparse.ArgumentParser
```

### `main`

```python
def main(argv: Sequence[str] | None=None) -> int
```


## `eval.agreement`

> Inter-rater agreement -- the ceiling (design.md sections 8.1, 8.2, 16.2).

| Constant | Value |
|---|---|
| `UNDEFINED` | `float('nan')` |

### class `Metric`(str, Enum)

The difference function.

Members: `NOMINAL` = 'nominal', `ORDINAL` = 'ordinal', `INTERVAL` = 'interval'

### class `CriterionAgreement`

Agreement on one criterion, and what it was computed from.

| Field | Type | Default |
|---|---|---|
| `criterion_key` | `str` | — |
| `alpha` | `float` | — |
| `units_used` | `int` | — |
| `units_total` | `int` | — |
| `raters` | `int` | — |
| `ratings` | `int` | — |
| `metric` | `Metric` | `Metric.ORDINAL` |

- *defined* → `bool`
- *reliable_enough_to_report* → `bool` — Krippendorff's own convention: 0.800 for conclusions, 0.667 as the lowest at which tentative conclusions are acceptable.
- `describe()` → `str`

### class `AgreementReport`

Per criterion, never aggregated.

| Field | Type | Default |
|---|---|---|
| `by_criterion` | `Mapping[str, CriterionAgreement]` | — |
| `metric` | `Metric` | `Metric.ORDINAL` |

- *weakest* → `CriterionAgreement | None`
- `unreliable()` → `tuple[CriterionAgreement, ...]` — Criteria whose definitions need work before their models do.
- `table()` → `str`

### `krippendorff_alpha`

```python
def krippendorff_alpha(ratings: Mapping[str, Mapping[str, int]], *, metric: Metric=Metric.ORDINAL) -> float
```

Krippendorff's alpha over `ratings[item_id][rater_id] -> level`.

### `rater_agreement`

```python
def rater_agreement(labels: Mapping[str, Mapping[str, Mapping[str, int]]], *, metric: Metric=Metric.ORDINAL) -> AgreementReport
```

Per-criterion agreement.

### class `NoConsensus`(ValueError)

The raters did not converge and nobody adjudicated.


### `consensus`

```python
def consensus(per_rater: Mapping[str, int], *, adjudicated: int | None=None) -> int
```

The agreed level for one call and one criterion.

### class `CriterionResult`

One row of section 8.2's table.

| Field | Type | Default |
|---|---|---|
| `criterion_key` | `str` | — |
| `human_alpha` | `float` | — |
| `model_alpha` | `float` | — |
| `n` | `int` | — |

- *fraction_of_ceiling* → `float` — Model agreement as a fraction of human agreement.
- *verdict* → `str` — What this row should make someone do.

### class `ModelReport`

| Field | Type | Default |
|---|---|---|
| `rows` | `tuple[CriterionResult, ...]` | — |

- `table()` → `str` — Section 8.2's table. Both columns, always.

### `measure_against_ceiling`

```python
def measure_against_ceiling(human: AgreementReport, consensus_labels: Mapping[str, Mapping[str, int]], model_labels: Mapping[str, Mapping[str, int]], *, metric: Metric=Metric.ORDINAL) -> ModelReport
```

Build section 8.2's table.

### `stratification_gaps`

```python
def stratification_gaps(strata: Mapping[str, Iterable[str]], labelled: Iterable[str]) -> Mapping[str, int]
```

How many labelled calls each stratum has.


## `eval.consistency`

> Rerun variance (design.md section 7.4).

### class `CriterionConsistency`

| Field | Type | Default |
|---|---|---|
| `criterion_key` | `str` | — |
| `unanimous_fraction` | `float` | — |
| `modal_agreement` | `float` | — |
| `mean_stdev` | `float` | — |
| `max_spread` | `int` | — |
| `calls` | `int` | — |
| `runs` | `int` | — |

- *flip_rate* → `float` — Fraction of calls that did not produce the same level every time.
- `describe()` → `str`

### class `ConsistencyReport`

| Field | Type | Default |
|---|---|---|
| `by_criterion` | `Mapping[str, CriterionConsistency]` | — |
| `runs` | `int` | — |

- *worst* → `CriterionConsistency | None`
- `noise_floor()` → `float` — The largest flip rate across criteria.
- `table()` → `str`

### `rerun_consistency`

```python
def rerun_consistency(runs: Sequence[Mapping[str, Mapping[str, int]]]) -> ConsistencyReport
```

Compare repeated scoring runs over the same calls.

### `improvement_is_meaningful`

```python
def improvement_is_meaningful(delta: float, report: ConsistencyReport, *, criterion_key: str | None=None) -> bool
```

Is a measured accuracy change bigger than the rerun noise?


## `eval.extraction_metrics`

> Extraction measured objectively (design.md section 8.3).

### class `GoldFact`

A fact a human confirmed is in the transcript.

| Field | Type | Default |
|---|---|---|
| `call_id` | `str` | — |
| `field` | `Field` | — |
| `value` | `str` | — |
| `ambiguous` | `bool` | `False` |

- *key* → `tuple[str, Field, str]`

### class `SpanVerdict`

A rater's yes/no on whether a cited span supports its claim.

| Field | Type | Default |
|---|---|---|
| `call_id` | `str` | — |
| `field` | `Field` | — |
| `value` | `str` | — |
| `quote` | `str` | — |
| `supports` | `bool` | — |

- *key* → `tuple[str, Field, str]`

### class `ExtractionMetrics`

| Field | Type | Default |
|---|---|---|
| `true_positives` | `int` | — |
| `false_positives` | `int` | — |
| `false_negatives` | `int` | — |
| `spans_judged` | `int` | — |
| `spans_supporting` | `int` | — |
| `quotes_offered` | `int` | — |
| `quotes_located` | `int` | — |
| `spans_not_in_transcript` | `int` | — |
| `spans_checked` | `int` | `0` |

- *precision* → `float` — Of claims made, how many are supported by their cited span?
- *recall* → `float` — Of facts present in the transcript, how many were found?
- *f1* → `float`
- *span_accuracy* → `float` — Does the cited span actually support the claim?
- *hallucination_rate* → `float` — Quotes the model produced that are not in the transcript.
- `report()` → `str`

### `evaluate_extraction`

```python
def evaluate_extraction(predicted: Mapping[str, Sequence[GroundedClaim]], gold: Iterable[GoldFact], *, transcripts: Mapping[str, Transcript] | None=None, span_verdicts: Iterable[SpanVerdict]=(), quotes_offered: int=0, quotes_located: int=0) -> ExtractionMetrics
```

Score a run against a gold set.

### `per_field`

```python
def per_field(predicted: Mapping[str, Sequence[GroundedClaim]], gold: Iterable[GoldFact]) -> Mapping[Field, ExtractionMetrics]
```

The same metrics, split by field.

