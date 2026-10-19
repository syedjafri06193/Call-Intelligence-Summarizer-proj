# The Pipeline

`cis.workflows.pipeline.run()` processes one call end to end. This page follows a call through
it, stage by stage, with the exact behaviour at each step.

## Signature

```python
from cis.workflows.pipeline import run

result = run(
    call,            # consent.model.Call
    ledger,          # consent.model.ConsentLedger
    client,          # ingest.tracks.PlatformClient
    transcriber,     # workflows.pipeline.Transcriber
    model,           # extract.grounded.ExtractionModel
    judge,           # score.anchors.AnchorJudge
    framework,       # score.framework.Framework
    *,
    policy=None,                 # ConsentPolicy; default all-party
    fields=None,                 # Sequence[Field]; default: see default_fields below
    target_tokens=2_500,         # chunk size
    overlap_turns=3,             # chunk overlap in whole turns
    now=None,                    # datetime for the consent decision; default UTC now
    redact_before_model=True,    # False only for self-hosted models
    require_attribution=True,    # False allows unattributed mixed audio (limited output)
)
```

Returns a `PipelineResult`.

## Stages

| # | `Stage` | Function(s) | Stops with |
|---|---|---|---|
| 1 | `CONSENT` | `consent.gate.may_process` | `SKIPPED` |
| 2 | `INGEST` | `ingest.tracks.ingest` | `SKIPPED` (`NoSpeakerAttribution`) |
| — | — | `asr.vocabulary.vocabulary_hints` | — |
| 3 | `TRANSCRIBE` | `transcriber.transcribe` + word-timing check | `FAILED` |
| 4 | `REDACT` | `redact.pii.Redactor(names=roster_names(...))` | — |
| 5 | `EXTRACT` | `chunk_by_turns` → `extract_chunk` × N → `reconcile` | `FAILED` |
| 6 | `SCORE` | `score.anchors.score_call` | `FAILED` |
| 7 | — | `crm.tasks.propose_tasks` | — |

`Stage.STAGE_FOR_REVIEW` exists in the enum for the review step that follows `run()`.

### 1. Consent gate

```python
decision = may_process(call, ledger, policy, now=now)
if not decision.allowed:
    return SKIPPED   # client has not been touched
```

This ordering **is the legal control** (§3.2, §13.2). Nothing may be moved above it. See
[consent.md](consent.md).

### 2. Ingest

`ingest(call, client, decision, require_attribution=...)`

1. `verify_decision_still_valid` — recomputes the roster fingerprint; if someone joined since
   the gate ran, raises `ConsentDenied`. (Inside `run()` the decision is fresh, so this cannot
   fire; it matters when gate and fetch are separate workflow activities. Note `run()` does not
   catch `ConsentDenied`, so it would propagate as an exception.)
2. Per-speaker platform tracks → `Attribution.PLATFORM_TRACK`.
3. Else a two-leg stereo dialer recording (`channels == 2`, `source_type == "dialer_two_leg"`)
   → `Attribution.TWO_LEG_STEREO`.
4. Else, if `require_attribution` → `NoSpeakerAttribution` → `SKIPPED`.
5. Else mixed audio with `Attribution.NONE` and `unattributed=True`.

Warnings (e.g. narrowband 8 kHz telephony) are added to `result.warnings`. See
[ingestion-and-asr.md](ingestion-and-asr.md).

### 3. Transcribe

`vocabulary_hints(call)` builds the recogniser hint list (participants first). The
`Transcriber` returns a `Transcript`; `_require_word_timings` raises `MissingWordTimings` if
any segment lacks word-level timings, because click-to-play verification depends on them.

### 4. Redaction setup

A `Redactor` is built with the roster's display names (expanded to first/last names). It is
passed into extraction and scoring as `redactor` when `redact_before_model=True`. The
restoration map stays in-process. See [redaction.md](redaction.md).

### 5. Extract

```python
for chunk in chunk_by_turns(transcript, target_tokens=..., overlap_turns=...):
    claims, stats = extract_chunk(chunk, transcript, model, wanted,
                                  attributed=attributed, redactor=outbound)
extraction = reconcile(call.call_id, transcript.version, partials)
```

- Fields requested default to `default_fields(framework)`: every field the framework scores,
  plus `commitment`, `next_step` (tasks need them), `budget`, `timeline` (the CRM wants them even
  though MEDDICC does not score them) and `objection`.
- Quotes are restored, then located in the chunk; unlocatable quotes are dropped and counted.
- If the transcript is unattributed, attribution-dependent fields (`economic_buyer`, `champion`,
  `commitment`, `next_step`) are dropped.

See [extraction.md](extraction.md).

### 6. Score

`score_call(extraction, framework, judge, transcript, call, redactor=outbound)` — one
`CriterionScore` per criterion. See [scoring-and-frameworks.md](scoring-and-frameworks.md).

### 7. Tasks

`propose_tasks(extraction.claims, call, attributed=attributed)` — proposals only. See
[crm-writeback.md](crm-writeback.md#tasks-crmtaskspy).

### Final warnings

- One `contradiction: …` warning per contradiction.
- `hallucination rate X% (N quotes offered)` if any quote was dropped.

## `PipelineResult`

| Field | Type | Meaning |
|---|---|---|
| `call_id` | `str` | |
| `outcome` | `Outcome` | `COMPLETED`, `SKIPPED`, `FAILED` |
| `reason` | `str` | Human-readable reason when not completed |
| `failed_stage` | `Stage \| None` | Where it stopped |
| `decision` | `ProcessingDecision \| None` | Always set (gate always runs) |
| `ingest_result` | `IngestResult \| None` | |
| `transcript` | `Transcript \| None` | |
| `extraction` | `Extraction \| None` | |
| `score` | `CallScore \| None` | |
| `tasks` | `tuple[ProposedTask, ...]` | |
| `stats` | `ExtractionStats` | Merged across chunks |
| `redactions` | `int` | Distinct values redacted |
| `warnings` | `tuple[str, ...]` | |

Properties: `ok` (completed), `needs_review` (judgment-criterion scores plus every proposed
task). Contradictions are reported separately in `extraction.contradictions` and `warnings`.

## Minimal example (offline)

```python
from cis.samples import load_sample, SamplePlatform, SampleTranscriber, ScriptedModel, ScriptedJudge
from cis.score.framework import load_framework
from cis.workflows.pipeline import run

sample = load_sample()
fw = load_framework("docs/frameworks/meddicc.yaml")
result = run(sample.call, sample.ledger, SamplePlatform(sample), SampleTranscriber(sample),
             ScriptedModel(), ScriptedJudge(), fw, now=sample.call.scheduled_at)

print(result.outcome, result.score.levels, [t.title for t in result.tasks])
```

## Tuning

| Knob | Default | Guidance |
|---|---|---|
| `target_tokens` | 2 500 (CLI demo: 90) | Smaller = better recall in long calls, more API calls. The demo uses 90 to force several chunks on a short sample. |
| `overlap_turns` | 3 (CLI demo: 2) | Turns repeated between chunks so statements keep context |
| `redact_before_model` | `True` | Set `False` only when the model runs on your infrastructure |
| `require_attribution` | `True` | `False` yields transcripts without commitments, tasks or buyer/champion claims |
