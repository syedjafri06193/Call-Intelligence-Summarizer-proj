# Getting Started

Everything below runs **offline**: no API key, no network, no cost. The demo uses a scripted
extractor and judge so you can watch the control flow without a model.

## Requirements

| | |
|---|---|
| Python | 3.10+ (uses `X \| Y` union types at runtime). Tested on 3.13. |
| Runtime dependency | `pyyaml >= 6.0` |
| Test dependency | `pytest >= 8.0` |
| Optional (live mode) | `groq >= 1.7`, `anthropic >= 1.0` |

## Install

A virtual environment is recommended (the Makefile's `install` target uses
`--break-system-packages`, which writes into the system Python):

```bash
git clone https://github.com/syedjafri06193/Call-Intelligence-Summarizer-proj.git
cd Call-Intelligence-Summarizer-proj/v1
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Or, if you accept the system-wide install: `make install`.

The package is not pip-installable yet; the Makefile and `pytest.ini` put `src` and `.` on
`PYTHONPATH`. When running modules by hand, do the same:

```bash
export PYTHONPATH=src:.
```

## Run

```bash
make test        # 405 tests, ~1 s
make demo        # full pipeline on samples/discovery_call.json
make gate        # consent gate, then again with p_marco's consent removed
make framework   # MEDDICC as loaded: criteria, kinds, anchors
make agreement   # Krippendorff's alpha on the bundled 10-call label set
make lint        # compile-check every module
make clean
```

## What the demo shows

`make demo` prints one block per stage. Abridged:

```
consent gate  (call_demo_001)
  allowed:      True
  mode:         all_party
  consenting:   p_rep, p_dana, p_marco

redaction (before the model saw anything)
  values redacted: 4
    [p_rep] [PERSON_1], [PERSON_2], thanks for making the time.

extraction
  budget             $50k approval threshold     “anything over fifty thousand”  [84s]
  budget             $80k                         “it's more like eighty thousand” [151s]
  economic_buyer     Priya Raman, finance         “Priya Raman on the finance side signs off…” [81s]
  ...
  quotes offered 30, located 29, hallucination rate 3.3%
    discarded champion: “Marco said he would champion this to the exec team”

contradictions (surfaced, not collapsed)
  budget: '$50k approval threshold' (at 84s) then '$80k' (at 151s)

score  (MEDDICC@3)
  Metrics              3  [auto]   2 supporting quote(s) in evidence
                          excluded: '300% first-year return': stated by the rep…
  Champion             2  [review] 1 supporting quote(s) in evidence
  ...

proposed tasks (every one awaiting confirmation)
  Send the security questionnaire by Friday
     owner:   sam@northwind.example
     due:     2026-09-18
```

Each line is a design decision made visible:

| Output | Decision | Doc |
|---|---|---|
| Gate before anything else | Consent gates the *fetch* | [consent.md](consent.md) |
| `[PERSON_1]` in model input | Redaction before the model | [redaction.md](redaction.md) |
| Discarded champion quote | The scripted model fabricates one quote on purpose; grounding drops it | [extraction.md](extraction.md) |
| Two budgets | Contradictions surfaced, not collapsed | [extraction.md](extraction.md#3-reconciliation-extractreconcilepy) |
| Rep's "300%" excluded | `prospect_stated_only` criteria ignore the rep | [scoring-and-frameworks.md](scoring-and-frameworks.md) |
| `[auto]` vs `[review]` | Factual vs judgment criteria | [scoring-and-frameworks.md](scoring-and-frameworks.md#factual-vs-judgment) |
| Due 2026-09-18 | "by Friday" resolved against the **call** date (Wed 16 Sep) | [crm-writeback.md](crm-writeback.md#due-dates) |

### Demo variations

```bash
python3 -m cis.cli demo --no-fabrication       # hallucination rate 0.0%
python3 -m cis.cli demo --no-redaction         # model sees raw names/emails
python3 -m cis.cli demo --target-tokens 5000   # one chunk, no reconciliation work
python3 -m cis.cli demo --framework docs/frameworks/bant.yaml
python3 -m cis.cli gate --drop p_marco p_dana  # gate refuses, lists blockers
```

## Next steps

- Read the [architecture](architecture.md), then [pipeline](pipeline.md).
- Try a live model: [live-providers.md](live-providers.md).
- Write a framework: [scoring-and-frameworks.md](scoring-and-frameworks.md#writing-your-own-framework).

## Troubleshooting

| Symptom | Fix |
|---|---|
| `ModuleNotFoundError: No module named 'cis'` | `export PYTHONPATH=src:.` from `v1/`, or use `make` |
| `error: externally-managed-environment` on `pip install` | Use a venv, or `make install` (adds `--break-system-packages`) |
| `ModuleNotFoundError: groq` with `--live` | `pip install -r requirements-live.txt` |
| `FrameworkError: … anchor 0 must mean absence` | Your YAML's level-0 anchor must contain "not discussed" or "no evidence" |
