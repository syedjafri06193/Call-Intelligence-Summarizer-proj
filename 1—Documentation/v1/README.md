# Documentation

Documentation for the Call Intelligence Summarizer (CIS). The **design guide**
([`design.md`](design.md)) is the specification; everything else here describes the
implementation in [`v1/`](../v1/) — how it works, how to run it, and how to extend it.

## Start here

| If you want to… | Read |
|---|---|
| Run it in two minutes | [Getting started](getting-started.md) |
| Understand the shape of the system | [Architecture](architecture.md) |
| Follow one call through every stage | [Pipeline](pipeline.md) |
| Plug in your own qualification framework | [Scoring & frameworks](scoring-and-frameworks.md#writing-your-own-framework) |
| Run against a real LLM | [Live providers](live-providers.md) |
| Look up a type or function | [API reference](api-reference.md) |

## Guides by stage

1. [Consent](consent.md) — the gate that runs before audio is fetched
2. [Ingestion & ASR](ingestion-and-asr.md) — per-speaker tracks, no voiceprints, vocabulary hints
3. [Transcripts & spans](transcripts-and-spans.md) — immutable versioned transcripts and citations
4. [PII redaction](redaction.md) — what the model never sees
5. [Extraction](extraction.md) — chunking, grounding, reconciliation
6. [Scoring & frameworks](scoring-and-frameworks.md) — MEDDICC, BANT, your own YAML
7. [CRM write-back & tasks](crm-writeback.md) — never overwrite a human, never auto-create a task

## Reference

- [CLI](cli.md) — `cis demo | gate | framework | agreement`
- [API reference](api-reference.md) — every public type, by module
- [Data formats](data-formats.md) — sample call JSON, framework YAML, labels JSON
- [Evaluation tooling](evaluation-tooling.md) — the `eval/` package as code
- [Live providers](live-providers.md) — Groq and Anthropic extractors, env vars
- [Testing](testing.md) — what the 405 tests guard
- [Glossary](glossary.md)

## Project

- [Project status & known issues](project-status.md)
- [Contributing](contributing.md)
- [Changelog](../CHANGELOG.md)
- [Roadmap](../v1/ROADMAP.md)

## Existing v1 documents (unchanged)

| Document | Purpose |
|---|---|
| [`v1/docs/legal.md`](../v1/docs/legal.md) | Legal and consent policy — the "why" behind the consent gate |
| [`v1/docs/evaluation.md`](../v1/docs/evaluation.md) | Evaluation methodology: ceilings, alpha, extraction metrics |
| [`v1/docs/notes-on-the-spec.md`](../v1/docs/notes-on-the-spec.md) | Where v1 deliberately diverges from `design.md`, and why |
| [`v1/docs/frameworks/`](../v1/docs/frameworks/) | `meddicc.yaml`, `bant.yaml` |

## Conventions

- "§N.M" refers to a section of [`design.md`](design.md). The code cites the same numbers in its
  docstrings, so `grep -rn "section 9.4" v1/src` finds the implementation of §9.4.
- Paths are relative to the repository root unless they start with `src/`, `tests/` or
  `eval/`, which are relative to `v1/`.
