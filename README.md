# Call Intelligence Summarizer

[![CI](https://github.com/syedjafri06193/Call-Intelligence-Summarizer-proj/actions/workflows/ci.yml/badge.svg)](https://github.com/syedjafri06193/Call-Intelligence-Summarizer-proj/actions/workflows/ci.yml)

Transcribes sales discovery calls, scores them against a qualification
framework (MEDDICC, BANT, or your own), and writes structured notes and next
steps back to the CRM — with consent, grounding and compliance enforced in code.

```
 call recording
      │
      ▼
 Consent gate ──✗── stop (nothing is fetched)
      │ ✓
      ▼
 Per-speaker ingest ── ASR ── PII redaction ── Grounded extraction ── MEDDICC / BANT scoring ── Idempotent CRM write-back
   (no voiceprints)           (before the model)  (every claim cites a span)  (ordinal anchors)       (no overwrites, no unconfirmed tasks)
```

## What makes it different

- **Consent before the fetch.** Per-participant, evidence-backed, append-only consent; the gate runs before audio is even downloaded.
- **No voiceprints.** Speakers come from per-speaker tracks, stereo channels, or a human — never vocal biometrics (avoids BIPA exposure).
- **PII redaction** on everything sent to the model, restored on the way back.
- **Grounded claims.** Every extracted fact must cite a transcript span; invented quotes are discarded and logged.
- **Pluggable rubrics.** Qualification frameworks are versioned YAML — see [`v1/docs/frameworks/`](v1/docs/frameworks/).
- **Idempotent CRM sync** that never overwrites a rep's note.

## Quick start

Runs fully offline — no API key, no network:

```bash
cd v1
make install    # pyyaml + pytest
make demo       # full pipeline on samples/discovery_call.json
make test       # 406 tests
```

## Adding your own framework

Copy [`v1/docs/frameworks/bant.yaml`](v1/docs/frameworks/bant.yaml), rename it
(e.g. `spiced.yaml`), and edit the criteria, definitions and evidence bars. The
loader is framework-agnostic, so no code changes are needed. A complete
SPICED example lives in [`v1/examples/spiced.yaml`](v1/examples/spiced.yaml); validate yours with
`python3 -m cis.cli framework --framework path/to/yours.yaml`.

## Repository layout

```
.
├── README.md          ← you are here
├── 1—Documentation/   ← all documentation (index in its README)
│   ├── design.md      ← full design guide (the spec code comments cite)
│   ├── design.pdf     ← same guide, PDF
│   ├── FEEDBACK.md    ← reviewer feedback by version
│   └── v1/            guides, legal notes, evaluation, roadmap, spec errata
└── v1/                ← first implementation
    ├── src/cis/       consent, ingest, asr, transcript, redact, extract, score, crm, workflows
    ├── tests/         406 tests, incl. consent-gate call counts and a no-voiceprint scan
    ├── eval/          inter-rater agreement, extraction metrics, rerun consistency
    ├── samples/       discovery_call.json
    ├── examples/      spiced.yaml — a custom framework template
    └── docs/frameworks/  MEDDICC and BANT rubrics (loaded by the code)
```

Start with [`v1/README.md`](v1/README.md) for the five design decisions behind the architecture.

## Versions

| Version | Summary |
|---|---|
| [v1](v1/) | Consent-gated pipeline, grounded extraction, YAML frameworks, idempotent CRM write-back, evaluation suite |

## Feedback

Feedback, bug reports and ideas are welcome.

- **Bugs or problems:** [open an issue](https://github.com/syedjafri06193/Call-Intelligence-Summarizer-proj/issues/new) and include your environment, the model or API you're using, steps to reproduce, and the summary you expected versus what you got.
- **Ideas or questions:** start a thread in [Discussions](https://github.com/syedjafri06193/Call-Intelligence-Summarizer-proj/discussions) (enable it under Settings → General → Features).
- **Code changes:** pull requests are appreciated; please describe what you changed and how you tested it.

> ⚠️ Please don't attach real call recordings or transcripts. Use synthetic or fully anonymized samples instead.

## Documentation

All documentation lives in [`1—Documentation/`](1—Documentation/README.md) — start with its index.

<!-- project-metrics:start -->
## Project Metrics

[![Stars](https://img.shields.io/github/stars/syedjafri06193/Call-Intelligence-Summarizer-proj?style=flat-square)](https://github.com/syedjafri06193/Call-Intelligence-Summarizer-proj/stargazers) [![Forks](https://img.shields.io/github/forks/syedjafri06193/Call-Intelligence-Summarizer-proj?style=flat-square)](https://github.com/syedjafri06193/Call-Intelligence-Summarizer-proj/forks) [![Open issues](https://img.shields.io/github/issues/syedjafri06193/Call-Intelligence-Summarizer-proj?style=flat-square)](https://github.com/syedjafri06193/Call-Intelligence-Summarizer-proj/issues) [![Last commit](https://img.shields.io/github/last-commit/syedjafri06193/Call-Intelligence-Summarizer-proj/improvements?style=flat-square)](https://github.com/syedjafri06193/Call-Intelligence-Summarizer-proj/commits/improvements) [![Commits](https://img.shields.io/github/commit-activity/t/syedjafri06193/Call-Intelligence-Summarizer-proj?style=flat-square)](https://github.com/syedjafri06193/Call-Intelligence-Summarizer-proj/commits/improvements) [![Top language](https://img.shields.io/github/languages/top/syedjafri06193/Call-Intelligence-Summarizer-proj?style=flat-square)](https://github.com/syedjafri06193/Call-Intelligence-Summarizer-proj) [![Repo size](https://img.shields.io/github/repo-size/syedjafri06193/Call-Intelligence-Summarizer-proj?style=flat-square)](https://github.com/syedjafri06193/Call-Intelligence-Summarizer-proj)

### Code

| Metric | Value |
|---|---|
| Lines of code | 6,264 |
| Source files | 40 |
| Tracked files | 89 |
| Documentation | 30 Markdown files (36,232 words), 1 PDF |
| Commits | 18 |
| Contributors | 2 |
| Active | 2026-09-11 → 2026-10-28 |

| Language | Lines | Share |
|---|---:|---:|
| Python | 5,972 | 95.3% |
| YAML | 259 | 4.1% |
| Makefile | 29 | 0.5% |
| INI | 4 | 0.1% |

### Performance

| Metric | Result |
|---|---|
| Model agreement vs human ceiling (4 MEDDICC criteria) | **94–96%** |
| Live run, Groq `gpt-oss-120b`, sample call | 100% coverage, **0.0% hallucination** (38/38 quotes) |

<sub>Measured figures recorded in [`1—Documentation/v1/evaluation.md`](1%E2%80%94Documentation/v1/evaluation.md).</sub>

<sub>Code stats as of 2026-10-28, counted with pygount (comments and blank lines excluded). Badges update live.</sub>
<!-- project-metrics:end -->
