# Project Status & Known Issues

Snapshot of `main` as of October 2026. Forward-looking plans live in
[`v1/ROADMAP.md`](ROADMAP.md); this page records what exists and what is wrong with it.

## Implemented (v1)

| Area | State |
|---|---|
| Consent model, ledger, gate, verbal detection | ✔ Complete, heavily tested |
| Ingestion: per-speaker tracks, two-leg stereo, opt-in unattributed | ✔ Interfaces + logic; no real platform client |
| Vocabulary hints | ✔ |
| Transcripts, spans, versioning, span migration | ✔ |
| PII redaction (regex + roster names) | ✔ |
| Chunking, grounded extraction, reconciliation | ✔ |
| Live extraction: Groq | ✔ Run against a real API |
| Live extraction: Anthropic | ⚠ Implemented, not run live; see issue 1 |
| Framework loader, MEDDICC, BANT | ✔ |
| Anchor scoring | ✔ Logic; only `ScriptedJudge` |
| CRM write policy, idempotency, journal, tasks | ✔ Logic; no real CRM connector |
| Evaluation: alpha, ceiling table, consistency, extraction metrics | ✔ |
| CLI + offline demo | ✔ |
| Tests | ✔ 405 passing |

## Not implemented

Real ASR, real meeting-platform client, real CRM connector, live judge, review UI, persistence
(ledger, transcripts, journal), durable workflow engine, retention/deletion jobs, access
control, packaging (`pyproject.toml`), CI. Notes on deliberate scope:
[`notes-on-the-spec.md` §11](notes-on-the-spec.md).

## Known issues

| # | Issue | Impact | Suggested fix |
|---|---|---|---|
| 1 | `LLMExtractor.extract` parses tool output with its own loop instead of `clean_claims`, dropping `support` and `participant_id` | Anthropic path: every claim 0.5 confidence → **no tasks ever proposed**, no owners | Use `clean_claims(block.input or {}, allowed)`; add tests for both keys |
| 2 | `groq_extractor.py` defines `_clean` twice (first is dead), imports `sys` unused, no module docstring, imports out of order | Confusing; first `_clean` silently ignores `support` if someone deletes the second | Delete the first `_clean` and `import sys` |
| 3 | Duplicate tasks: same sentence as `commitment` and `next_step` with different wording | 6 tasks for 2 promises in the live run | Dedupe by shared quote span in `reconcile` / `propose_tasks` |
| 4 | Participant ID leaks into `value` (e.g. champion `p_dana`) | False contradictions | Tighten `participant_id` tool description and system prompt |
| 5 | "Jan" vs "January" reported as a timeline contradiction | Noise in contradictions | Timeline normalisation (month names) |
| 6 | One `metric` value can hold two facts | Merged evidence | "One fact per entry" instruction or protocol change |
| 7 | `run()` catches `NoSpeakerAttribution` from ingest but not `ConsentDenied` | A roster change between gate and fetch (only possible once split into activities) raises instead of returning `SKIPPED` | Catch `ConsentDenied` → `SKIPPED` at `INGEST` |
| 8 | `run()` calls `reconcile(...)` without `extractor_version=EXTRACTOR_VERSION`; the default is a literal `"1.0.0"` | Version drift when `EXTRACTOR_VERSION` changes | Pass it explicitly |
| 9 | Live `--live` demo still uses `ScriptedJudge` | Scores aren't from a model even in live mode | Implement a live `AnchorJudge` |
| 10 | README says 387 tests; suite has 405 | Stale | Update count or drop the number |
| 11 | `v1/README.md` is a copy of the root README; its links (`v1/docs/frameworks/…`) break when read from `v1/` | Broken links, duplicate content | Replace with a v1-specific README (done in this docs change) |
| 12 | Empty `1—Documentation/v1/README.md` (em-dash folder name) | Confusing, awkward in shells | Delete |
| 13 | ROADMAP refers to `gpt-oss-120b`; code default is `llama-3.3-70b-versatile` | Reproducing the recorded run needs `CIS_GROQ_MODEL` | Document or change default |
| 14 | `make install` uses `--break-system-packages` | Modifies system Python | Recommend a venv |
| 15 | `requirements.txt` mixes runtime (`pyyaml`) and test (`pytest`) deps | Production installs get pytest | Split, or move to `pyproject.toml` extras |
| 16 | No package metadata; `PYTHONPATH` required; no `cis` console script | Friction | `pyproject.toml` with `[project.scripts] cis = "cis.cli:main"` |
| 17 | `cis framework | head` raises `BrokenPipeError` traceback | Cosmetic | Handle `BrokenPipeError` in `main` |
| 18 | `FEEDBACK.md` mixes reviewer feedback, an offer of services and an unattributed remark | Reads as unedited | Curate or move to Discussions |
| 19 | Sample loader ignores `joined_late`, `jurisdiction_hint` | Can't demo late-joiner blocking from JSON | Read optional fields |
| 20 | Redaction misses names not on the roster (e.g. "Priya Raman") | Third-party names reach hosted models | Add CRM contacts to the name list; document per deployment |

Issues 3–6 and 9 are from the live run recorded in the roadmap.

## Versioning of this repository

Versions are directories (`v1/`). Reviewer feedback questions this ("git is VCS already"). If
a v2 is started, consider tags/branches instead and keep `docs/` describing the current code.
