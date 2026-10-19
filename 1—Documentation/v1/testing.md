# Testing

```bash
cd v1
make test                         # or: PYTHONPATH=src:. python3 -m pytest
python3 -m pytest tests/test_consent_gate.py -q
python3 -m pytest -k "late_joiner"
```

405 tests, ~1 second, no network, no API key. `pytest.ini` sets `testpaths = tests`,
`pythonpath = src .`, and `--strict-markers`.

## What each file guards

| File | Tests | Guards |
|---|--:|---|
| `test_no_voiceprints.py` | 73 | **Legal commitment (§2.3, §16.3).** Scans `src/` and `eval/` for diarization / speaker-embedding imports (`pyannote`, `speechbrain`, `resemblyzer`, `diart`, NeMo diarizers, …) and call-level markers (e.g. `SpeakerRecognition`). Also asserts the scan actually found files, so it can't pass vacuously. |
| `test_consent_gate.py` | 58 | All-party default, one-party needs authoriser and never overrides refusal, late joiners, roster change between gate and fetch, append-only ledger and withdrawal, verbal-consent detection window, host-only consent → deletion |
| `test_crm.py` | 56 | Never overwrite human content, append with marker, auto requires precision ≥ 0.95, idempotent keys survive reprocessing, task fingerprints, journal and backoff, task gates, due dates against call date, owner resolution |
| `test_scoring.py` | 48 | Framework loader strictness, level 0 by code, off-scale / zero levels rejected, judge prompt contains no transcript or prior score, prospect-only exclusion, redaction to the judge, factual vs judgment routing |
| `test_grounding.py` | 45 | Quotes must locate (whitespace-only fuzziness), spans quote transcript text, chunking never splits turns and makes forward progress, reconciliation dedupes and surfaces contradictions, money normalisation edge cases ("Q4", "2026"), transcript versioning and span migration |
| `test_evaluation.py` | 40 | Krippendorff's alpha against published values, undefined ≠ 0/1, consensus without majority vote, ceiling table, rerun consistency, extraction metrics |
| `test_redaction.py` | 30 | Each PII kind, Luhn check, roster names and skip-words, overlap resolution, stable placeholders, restoration map never serialised/logged, round-trip |
| `test_pipeline.py` | 23 | **The fetch never happens when consent says no** (fake platform client's fetch count stays 0), end-to-end outcomes per stage, missing word timings, redaction before model, unattributed audio drops attribution fields and tasks |
| `test_cli.py` | 18 | Each command and exit code on the bundled sample |
| `test_groq_extractor.py` | 8 | Groq adapter with a fake client: tool parsing, retries, support → confidence, participant ID |
| `test_llm_extractor.py` | 6 | Anthropic adapter with a fake client |

## Fixtures (`tests/conftest.py`)

| Fixture / helper | Provides |
|---|---|
| `words(text, start_ms, ms_per_word=400)` | Word timings for a text |
| `segment(...)` | A `Segment` with timings |
| `participants` | Rep + prospects roster |
| `call` | A `Call` from that roster |
| `consented_ledger` | Ledger where everyone consented |
| `opening_transcript` | Opening minutes with an announcement and responses |

## Writing tests

- **No network.** Live adapters are tested with fake clients (see `test_groq_extractor.py`).
- **Inject time.** Pass `now=` to `may_process`/`detect_consent`, `call_date` to
  `resolve_due`, `sleep=` to `with_backoff`/`write_payload`.
- **Fake boundaries via protocols**: `PlatformClient`, `Transcriber`, `ExtractionModel`,
  `AnchorJudge`, `CRM`. Count calls on fakes to prove ordering (`test_pipeline.py` asserts the
  platform client fetched nothing after a refusal).
- **Every invariant in [architecture.md](architecture.md#invariants) needs a test that fails
  if it's broken**, not only one that passes when it holds.
- A behaviour change that alters pipeline output must update the relevant test in the same PR.

## Gaps

- No tests run against a live provider (by design; manual runs are recorded in the roadmap).
- No property-based / fuzz tests for regexes (dates, money, PII).
- The Anthropic adapter's tests don't cover `support`/`participant_id`, which is how the
  `LLMExtractor` parsing gap went unnoticed ([project-status.md](project-status.md)).
- No CI workflow runs the suite automatically.
