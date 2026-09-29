# Call Intelligence Summarizer

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
make test       # 387 tests
```

## Adding your own framework

Copy [`v1/docs/frameworks/bant.yaml`](v1/docs/frameworks/bant.yaml), rename it
(e.g. `spiced.yaml`), and edit the criteria, definitions and evidence bars. The
loader is framework-agnostic, so no code changes are needed.

## Repository layout

```
.
├── README.md          ← you are here
├── FEEDBACK.md        ← reviewer feedback by version
├── docs/
│   ├── design.md      ← full design guide (the spec code comments cite)
│   └── design.pdf     ← same guide, PDF
└── v1/                ← first implementation
    ├── src/cis/       consent, ingest, asr, transcript, redact, extract, score, crm, workflows
    ├── tests/         387 tests, incl. consent-gate call counts and a no-voiceprint scan
    ├── eval/          inter-rater agreement, extraction metrics, rerun consistency
    ├── samples/       discovery_call.json
    └── docs/          legal notes, evaluation, frameworks, spec errata
```

Start with [`v1/README.md`](v1/README.md) for the five design decisions behind the architecture.

## Versions

| Version | Summary |
|---|---|
| [v1](v1/) | Consent-gated pipeline, grounded extraction, YAML frameworks, idempotent CRM write-back, evaluation suite |
