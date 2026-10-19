# Command Line

```
python3 -m cis.cli {demo,gate,framework,agreement} [options]
```

Run from `v1/` with `PYTHONPATH=src:.` (the Makefile sets this). Every command runs offline
except `demo --live`. There is no installed `cis` executable yet; "`cis`" in docs means
`python3 -m cis.cli`.

## `demo`

Runs the full pipeline on a sample call and prints each stage.

```
cis demo [--sample PATH] [--framework PATH] [--target-tokens N]
         [--no-redaction] [--no-fabrication] [--live] [--provider {groq,anthropic}]
```

| Option | Default | Effect |
|---|---|---|
| `--sample` | `samples/discovery_call.json` | Sample call file ([format](data-formats.md#sample-call)) |
| `--framework` | `docs/frameworks/meddicc.yaml` | Framework YAML |
| `--target-tokens` | `90` | Chunk size; small so the short sample exercises overlap and reconciliation |
| `--no-redaction` | off | Send unredacted text to the model (self-hosted only) |
| `--no-fabrication` | off | Stop the scripted model inventing a quote |
| `--live` | off | Use a real LLM for extraction ([live-providers.md](live-providers.md)) |
| `--provider` | `groq` | `groq` or `anthropic` with `--live` |

Fixed in the demo: `overlap_turns=2`, the consent decision time is the call's `scheduled_at`,
and the judge is always `ScriptedJudge` (even with `--live`).

Output sections: consent gate · transcript · redaction · extraction (with stats and discarded
quotes) · contradictions · score · proposed tasks · warnings.

Exit codes: `0` completed or skipped at a gate, `1` failed stage or framework/file error.

## `gate`

Runs only the consent gate against the sample's ledger.

```
cis gate [--sample PATH] [--drop PARTICIPANT_ID ...]
```

`--drop` removes those participants' consent records first, to show a refusal:

```
$ cis gate --drop p_marco
gate  (call_demo_001)
  allowed:     False
  policy:      1 / all_party
  roster:      05294bf2370d3ddb
  reason:      1 of 3 participant(s) without recorded consent
  remediation: announce and capture acknowledgment, ask the platform to prompt, or exclude the call
    blocked: p_marco -- no_consent_record
```

Exit codes: `0` allowed, `2` refused.

## `framework`

Prints a framework as the loader sees it: id, criteria, kind, fields, evidence requirement,
prospect-only note, anchors. Use it to validate a YAML file you've written.

```
cis framework [--framework PATH]
```

Exit codes: `0` valid, `1` `FrameworkError` (message on stderr).

## `agreement`

Krippendorff's alpha per criterion from multi-rater labels.

```
cis agreement LABELS.json [--metric {nominal,ordinal,interval}]
```

```
$ cis agreement eval/labeled/sample_labels.json
criterion             alpha    n   raters
champion              0.385   10       3
economic_buyer        1.000   10       3

  below 0.667 -- fix the definition before the model:
    champion: alpha=0.385 (n=10/10 units, 3 raters)
```

Labels format: [data-formats.md](data-formats.md#rater-labels). Default metric `ordinal`
(anchor levels are ordered). Exit code `0`.

## Makefile shortcuts

| Target | Runs |
|---|---|
| `make demo` | `cis demo` |
| `make gate` | `cis gate`, then `cis gate --drop p_marco` (non-zero ignored) |
| `make framework` | `cis framework` |
| `make agreement` | `cis agreement eval/labeled/sample_labels.json` |
| `make test` | `pytest` |
| `make lint` | `compileall` on `src`, `eval`, `tests` |
| `make install` | `pip install --break-system-packages -r requirements.txt` |
| `make clean` | Remove `__pycache__`, `.pytest_cache` |

`PYTHON` can be overridden: `make test PYTHON=python3.12`.
