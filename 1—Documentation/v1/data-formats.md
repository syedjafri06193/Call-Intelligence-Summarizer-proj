# Data Formats

## Sample call

File: `v1/samples/discovery_call.json`, loaded by `cis.samples.load_sample(path)`. Use it as the
template for new synthetic calls (`cis demo --sample my_call.json`).

```json
{
  "call": {
    "call_id": "call_demo_001",
    "external_id": "zoom_88812345",
    "scheduled_at": "2026-09-16T15:00:00+00:00",
    "source_type": "zoom_cloud",
    "channels": 3,
    "participants": [
      {"participant_id": "p_rep",  "display_name": "Sam Rivera", "email": "sam@northwind.example", "role": "rep"},
      {"participant_id": "p_dana", "display_name": "Dana Chen",  "email": "dana@acme.example",     "role": "prospect"}
    ]
  },
  "consent": [
    {"participant_id": "p_rep",  "method": "platform_consent",    "evidence_ref": "zoom_consent_event_4471", "recorded_at": "2026-09-16T15:00:04+00:00"},
    {"participant_id": "p_dana", "method": "verbal_acknowledged", "evidence_ref": "span:s2",                 "recorded_at": "2026-09-16T15:00:19+00:00"}
  ],
  "transcript": {
    "asr_engine": "whisper",
    "asr_model": "large-v3",
    "segments": [
      {"segment_id": "s0", "participant_id": "p_rep", "start_ms": 0, "text": "Dana, thanks for making the time."}
    ]
  }
}
```

| Path | Required | Notes |
|---|:-:|---|
| `call.call_id` | ✔ | Must not contain `:` (used in CRM keys) |
| `call.external_id` | ✔ | Platform meeting ID |
| `call.scheduled_at` | ✔ | ISO 8601 with offset; tasks' relative dates resolve against its date |
| `call.source_type` | ✔ | `zoom_cloud`, `dialer_two_leg`, … |
| `call.channels` | | Default 1 |
| `participants[].participant_id` | ✔ | |
| `participants[].display_name`, `email` | | Names drive redaction and vocabulary hints |
| `participants[].role` | | `rep`, `prospect`, `unknown` (default) |
| `consent[].method` | ✔ | A `ConsentMethod` value |
| `consent[].evidence_ref` | | Strongly recommended for consenting methods |
| `consent[].recorded_at` | ✔ | ISO 8601 |
| `transcript.segments[].segment_id` | ✔ | Unique |
| `transcript.segments[].participant_id` | | `null` = unattributed |
| `transcript.segments[].start_ms` | ✔ | |
| `transcript.segments[].text` | ✔ | |

The loader synthesises `end_ms` and word timings at 380 ms per word (`MS_PER_WORD`); real ASR
output would supply them. `joined_late`, `jurisdiction_hint` and per-record `note`/`email` are
not read by the sample loader.

**Never commit real recordings or transcripts.** Use synthetic or fully anonymised data with
`.example` email domains.

## Framework YAML

See [scoring-and-frameworks.md → Schema](scoring-and-frameworks.md#schema). Examples:
[`meddicc.yaml`](../../v1/docs/frameworks/meddicc.yaml), [`bant.yaml`](../../v1/docs/frameworks/bant.yaml),
[`v1/examples/spiced.yaml`](../../v1/examples/spiced.yaml).

## Rater labels

Used by `cis agreement` and `eval.agreement.rater_agreement`:

```json
{
  "call_001": {
    "r1": {"economic_buyer": 3, "champion": 3},
    "r2": {"economic_buyer": 3, "champion": 3},
    "r3": {"economic_buyer": 3, "champion": 2}
  }
}
```

`{call_id: {rater_id: {criterion_key: level}}}`. Raters may skip calls or criteria (alpha
handles missing ratings). Levels must be anchor levels of the framework in use.
Bundled: `v1/eval/labeled/sample_labels.json` (10 calls × 3 raters × 2 criteria).

## Consistency runs

For `eval.consistency.rerun_consistency`: a list of runs, each
`{call_id: {criterion_key: level}}` — e.g. `[score.levels for each rerun]` keyed by call.

## Model extraction output (`ExtractionModel.extract`)

```json
{
  "<field>": {
    "value": "string",
    "quotes": ["verbatim transcript text"],
    "confidence": 0.9,
    "subject_participant_id": "p_dana"
  }
}
```

`confidence` and `subject_participant_id` optional (default 0.5 / none). Unknown fields are
ignored. `null` values are skipped.

## Judge output (`AnchorJudge.choose_level`)

```json
{"level": 2, "rationale": "one sentence naming the deciding evidence"}
```

`level` must be an integer on the criterion's scale and not 0.

## CRM external keys

```
call:<call_id>:<kind>[:<discriminator>]
call:call_demo_001:summary
call:call_demo_001:task:0c1ef0e76f124e50
```

Provenance fields written with every upsert: `ai_call_id`, `ai_transcript_version`,
`ai_extractor_version`, `ai_framework`, `ai_judge_version`, plus `external_key`.

## Span IDs

```
<segment_id>:<start_char>-<end_char>@v<transcript_version>      e.g. s7:12-58@v1
```

Consent records reference transcript evidence as `span:<segment_id>` in the sample, or a full
span ID when produced by `detect_consent`.
