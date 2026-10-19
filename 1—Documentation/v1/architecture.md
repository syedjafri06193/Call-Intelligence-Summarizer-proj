# Architecture

## Purpose

CIS turns a recorded sales discovery call into three things a rep can trust:

1. **Grounded facts** — budget, economic buyer, pain, timeline … each with the exact quote and
   timestamp that supports it.
2. **A qualification score** against a versioned framework (MEDDICC, BANT, or your own).
3. **Proposed CRM updates and tasks**, none of which overwrite human work or land without
   confirmation.

The system is built around a small number of non-negotiable invariants, most of them legal
or trust properties rather than engineering preferences.

## Data flow

```
 Call + ConsentLedger
        │
        ▼
 ┌────────────────┐  ✗ → PipelineResult(outcome=SKIPPED, failed_stage=CONSENT)
 │ consent.gate   │      nothing fetched
 └──────┬─────────┘
        │ ProcessingDecision(allowed=True, roster_fingerprint)
        ▼
 ┌────────────────┐  re-checks roster; ✗ → SKIPPED at INGEST
 │ ingest.tracks  │  per-speaker tracks | two-leg stereo | (opt-in) unattributed
 └──────┬─────────┘
        │ IngestResult(tracks, attribution)
        ▼
 ┌────────────────┐  asr.vocabulary → VocabularyHints (roster, account, products)
 │ Transcriber    │  (protocol; sample uses SampleTranscriber)
 └──────┬─────────┘
        │ Transcript v1 (immutable, word timings required)
        ▼
 ┌────────────────┐
 │ redact.pii     │  Redactor(roster names) — mapping stays local
 └──────┬─────────┘
        ▼
 ┌────────────────────────────────────────────────────┐
 │ extract: chunk_by_turns → extract_chunk (per chunk) │  model sees redacted text;
 │          → reconcile                                │  quotes restored, located,
 └──────┬─────────────────────────────────────────────┘  unlocatable quotes dropped
        │ Extraction(claims, contradictions)
        ▼
 ┌────────────────┐  judge never sees the transcript; level 0 assigned by code
 │ score.anchors  │  against score.framework (YAML)
 └──────┬─────────┘
        │ CallScore
        ▼
 ┌────────────────┐  proposals only; requires_confirmation=True
 │ crm.tasks      │
 └──────┬─────────┘
        ▼
 PipelineResult(outcome=COMPLETED) ──► review UI (planned) ──► crm.writeback / idempotency
```

`workflows/pipeline.py::run()` wires the stages together; the CRM write-back modules are
used after human review and are **not** called by `run()`.

## Packages

| Package | Responsibility | Key types | Design § |
|---|---|---|---|
| `cis.consent` | Consent state, the gate, verbal-consent detection | `ConsentLedger`, `ConsentRecord`, `ConsentPolicy`, `ProcessingDecision`, `may_process`, `detect_consent` | 2, 3 |
| `cis.ingest` | Fetch audio with speaker identity from non-voice sources | `AudioTrack`, `Attribution`, `PlatformClient`, `ingest` | 4 |
| `cis.asr` | Vocabulary biasing for the recogniser | `VocabularyHints`, `vocabulary_hints` | 5.2 |
| `cis.transcript` | Immutable, versioned transcripts; citation spans | `Transcript`, `Segment`, `Word`, `TranscriptSpan`, `migrate_spans` | 5.3, 5.4, 6.3 |
| `cis.redact` | Reversible PII redaction | `Redactor`, `RestorationMap`, `PiiKind`, `measure` | 10.2 |
| `cis.extract` | Chunking, grounded extraction, reconciliation, live LLM adapters | `Chunk`, `GroundedClaim`, `Field`, `ExtractionModel`, `Extraction`, `GroqExtractor`, `LLMExtractor` | 6 |
| `cis.score` | Framework loading and anchor-based judging | `Framework`, `Criterion`, `AnchorJudge`, `CallScore` | 7 |
| `cis.crm` | Write-back policy, tasks, idempotency, retries | `FieldPolicy`, `write_field`, `propose_tasks`, `upsert`, `WritebackJournal` | 9 |
| `cis.workflows` | End-to-end orchestration | `run`, `PipelineResult`, `Stage`, `Outcome` | 13.2 |
| `cis.samples` | Offline sample data and scripted model stand-ins | `load_sample`, `ScriptedModel`, `ScriptedJudge` | — |
| `cis.cli` | Command line | `main` | — |
| `eval` | Inter-rater agreement, extraction metrics, rerun consistency | `krippendorff_alpha`, `evaluate_extraction`, `rerun_consistency` | 8, 7.4 |

## Invariants

These are enforced in code and guarded by tests. Changing any of them is a design change,
not a refactor.

| # | Invariant | Where enforced | Test |
|---|---|---|---|
| 1 | Consent is checked **before audio is fetched** | `run()` returns before touching `client`; `ingest()` re-verifies the decision | `test_pipeline.py` (fake client fetch count = 0) |
| 2 | Default mode is **all-party**; relaxing it needs a named authoriser | `ConsentPolicy.__post_init__` | `test_consent_gate.py` |
| 3 | A one-party policy never overrides an explicit refusal | `may_process` | `test_consent_gate.py` |
| 4 | **No voiceprints**: speaker identity never comes from voice | `ingest`, `Attribution` enum | `test_no_voiceprints.py` (source scan) |
| 5 | Transcripts are immutable; corrections are new versions | `Transcript.revise` | `test_grounding.py` |
| 6 | Word-level timings are required | `MissingWordTimings` in `run()` | `test_pipeline.py` |
| 7 | Hosted models see redacted text; the restoration map never leaves | `extract_chunk`, `score_call`, `RestorationMap` (no serialisation) | `test_redaction.py` |
| 8 | Every claim cites a span that exists in the transcript | `extract_chunk`, `Chunk.locate`, `validate` | `test_grounding.py` |
| 9 | Contradictions are surfaced, never silently resolved | `reconcile` | `test_grounding.py` |
| 10 | The judge never sees the transcript; level 0 is assigned by code | `score_criterion` signature | `test_scoring.py` |
| 11 | Every score names `framework@version` | `CallScore.framework_id` | `test_scoring.py` |
| 12 | Human-entered CRM content is never replaced | `write_field` | `test_crm.py` |
| 13 | `auto` write mode requires measured precision ≥ 0.95 | `FieldPolicy.__post_init__` | `test_crm.py` |
| 14 | Tasks are proposals; `requires_confirmation` cannot be `False` | `ProposedTask` | `test_crm.py` |
| 15 | Relative dates resolve against the **call** date, never the clock | `resolve_due` | `test_crm.py` |
| 16 | Reprocessing never duplicates CRM objects | `identity_key`, `upsert` | `test_crm.py` |

## Boundaries (protocols)

External systems are reached only through `typing.Protocol`s, so each can be faked in tests
and swapped in production:

| Protocol | Module | Methods | Implementations in repo |
|---|---|---|---|
| `PlatformClient` | `ingest.tracks` | `per_speaker_tracks`, `mixed_track` | `SamplePlatform` |
| `Transcriber` | `workflows.pipeline` | `transcribe(tracks, hints)` | `SampleTranscriber` |
| `CRMVocabularySource` | `asr.vocabulary` | `account_name`, `contact_names`, `product_names`, `opportunity_names` | — |
| `ExtractionModel` | `extract.grounded` | `extract(chunk_text, fields) -> dict` | `ScriptedModel`, `GroqExtractor`, `LLMExtractor` |
| `TextRedactor` | `extract.grounded`, `score.anchors` | `redact`, `restore` | `Redactor` |
| `AnchorJudge` | `score.anchors` | `choose_level(prompt) -> dict` | `ScriptedJudge` |
| `CRM` | `crm.idempotency` | `find_by_external_key`, `create`, `update`, `get_field`, `is_ai_authored` | test fakes |

## Error model

`run()` **returns** rather than raises for outcomes the product must display:

| Situation | `outcome` | `failed_stage` |
|---|---|---|
| Consent missing / refused | `SKIPPED` | `CONSENT` |
| No way to attribute speakers | `SKIPPED` | `INGEST` |
| ASR error or missing word timings | `FAILED` | `TRANSCRIBE` |
| Extraction / reconciliation error | `FAILED` | `EXTRACT` |
| Judge error (e.g. `InvalidAnchorLevel`) | `FAILED` | `SCORE` |
| Everything ran | `COMPLETED` | `None` |

Programming errors and policy misconfiguration (`ConsentPolicy` without authoriser,
`FieldPolicy` auto without precision, malformed framework YAML) raise at construction or
load time, on purpose.

## Versioning of outputs

Every stored artefact records what produced it, so it can be interpreted later:

| Artefact | Version fields |
|---|---|
| `Transcript` | `version`, `asr_engine`, `asr_model`, `vocabulary_hash`, `content_hash`, `supersedes` |
| `TranscriptSpan` | `transcript_version`, plus the quoted `text` |
| `GroundedClaim` | `extractor_version` (`EXTRACTOR_VERSION = "1.0.0"`) |
| `CallScore` | `framework_id` (`MEDDICC@3`), `judge_version`, `temperature`, `transcript_version`, `extractor_version` |
| `ProcessingDecision` | `policy_version`, `roster_fingerprint`, `decided_at` |
| CRM writes | `Provenance` (call, transcript, extractor, framework, judge versions) |

## Why a plain function and not a workflow engine

§13.2 recommends Temporal. v1 keeps `run()` a plain function: each step is a pure function of
its inputs with failures as exceptions, so it maps one-to-one onto workflow activities later,
without adding an operational dependency now. `ingest()` already re-checks the roster
fingerprint because, in a durable workflow, minutes may pass between gate and fetch.
