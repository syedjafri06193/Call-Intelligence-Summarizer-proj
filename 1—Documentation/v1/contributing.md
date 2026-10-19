# Contributing

Bug reports, ideas and pull requests are welcome.

- **Bugs:** [open an issue](https://github.com/syedjafri06193/Call-Intelligence-Summarizer-proj/issues/new)
  with environment, model/API used, steps to reproduce, and expected vs actual output.
- **Ideas / questions:** [Discussions](https://github.com/syedjafri06193/Call-Intelligence-Summarizer-proj/discussions).
- **Code:** pull requests describing what changed and how it was tested.

> ⚠️ **Never attach or commit real call recordings or transcripts.** Use synthetic or fully
> anonymised samples with `.example` domains.

## Before you start

1. Read [architecture.md → Invariants](architecture.md#invariants). Changing an invariant is a
   design discussion, not a PR detail.
2. Find the design section your change touches; code docstrings cite `design.md` sections.
3. If you disagree with the design, record it in [`v1/docs/notes-on-the-spec.md`](../v1/docs/notes-on-the-spec.md)
   alongside the change — that is how v1 handled its divergences.

## Development loop

```bash
cd v1
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
make test && make lint && make demo
```

## Rules of the codebase

| Rule | Why |
|---|---|
| No speaker identification from voice. No diarization libraries. | BIPA exposure; `test_no_voiceprints.py` will fail |
| Nothing may run before the consent gate in `run()` | It is the legal control |
| External systems only through `Protocol`s | Testable offline |
| No network in tests | Suite stays fast and free |
| Inject time (`now`, `call_date`, `sleep`) | Determinism |
| Misconfiguration fails at construction/load | Policies that can't be honoured shouldn't exist |
| Never log `RestorationMap` contents or raw transcript text with PII | Most redaction leaks are log lines |
| Models emit ordinal choices; code owns numbers | Self-reported confidence isn't measurement |
| Bump `EXTRACTOR_VERSION` / `JUDGE_VERSION` / framework `version` when behaviour changes | Stored outputs must be interpretable later |
| Docstrings explain *why*, citing the design section | The repository's point is the decisions |

## Common contributions

**New framework** — add YAML, validate with `cis framework --framework`, no code change. See
[scoring-and-frameworks.md](scoring-and-frameworks.md#writing-your-own-framework).

**New extraction field** — add to `Field`, give it `_FIELD_HELP` text, decide whether it is
`SINGLE_VALUED` and/or `ATTRIBUTION_DEPENDENT`, update `ScriptedModel` if the demo should show it,
add tests, bump `EXTRACTOR_VERSION`.

**New LLM provider** — implement `ExtractionModel` with `clean_claims`, add `--provider` choice,
add an offline fake-client test. See [live-providers.md](live-providers.md#adding-a-provider).

**New PII kind** — regex + `PiiKind` member + placeholder + precision/recall tests with both
positive and look-alike negative cases.

**Platform / CRM / ASR connector** — implement the protocol in a new module; keep SDK imports
lazy; never fetch bytes before `ingest()` verifies the decision.

## Pull request checklist

- [ ] `make test` and `make lint` pass
- [ ] New behaviour has a test that would fail without it
- [ ] Relevant `docs/` page updated
- [ ] Version constants bumped if outputs change
- [ ] No secrets, no real call data
