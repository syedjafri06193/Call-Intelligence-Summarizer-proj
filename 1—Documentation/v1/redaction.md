# PII Redaction

Package: `cis.redact` · Design: §10.2 · Policy: [`v1/docs/legal.md` §4.1](../v1/docs/legal.md)

> "If transcripts go to a hosted LLM, redact first. Detect and replace, keeping a local mapping
> so you can restore for display." — §10.2

## Where it happens

```
transcript chunk ──redact──► model ──quotes──restore──► locate in real transcript ──► span
evidence block   ──redact──► judge ──rationale──restore──► CriterionScore.rationale
```

Redaction is wired **through** extraction and scoring rather than beside them: the model sees
placeholders, its quotes are restored before they are located, so spans still cite what was
actually said (both §10.2 and §6.3 hold at once).

## What is detected

| `PiiKind` | Placeholder | Detector |
|---|---|---|
| `EMAIL` | `[EMAIL_n]` | Regex |
| `PHONE` | `[PHONE_n]` | Regex: optional country code, `(ddd)`/`ddd`, separators required |
| `CARD` | `[CARD_n]` | 13–19 digits with optional separators, **Luhn-checked** (avoids redacting order numbers) |
| `GOVERNMENT_ID` | `[GOVID_n]` | US SSN shape `ddd-dd-dddd` |
| `ADDRESS` | `[ADDRESS_n]` | Number + up to 4 capitalised words + street suffix (Street, Ave., Rd., …) |
| `PERSON` | `[PERSON_n]` | **Roster names only** — no name regex, no NER |

### Names come from the roster

There is no pattern for a name, and an NER model would be a second model with its own error
rate in the sensitive path. Instead `roster_names()` expands participant display names:

- `"Dana Chen"` → `"Dana Chen"`, `"Dana"`, `"Chen"`
- Parts under 3 characters dropped (initials match everywhere)
- Parts that are ordinary words dropped (`will`, `mark`, `grace`, `may`, `june`, `frank`, …)

Longest match wins, so "Dana Chen" is one placeholder, not two.

**Known gap, by design:** names not on the roster are not redacted. In the sample call,
"Priya Raman" (the economic buyer, not on the call) reaches the model. If that matters for your
deployment, add CRM contacts for the account to the name list, or run a self-hosted model.

### Overlap resolution

Detections are resolved longest-first, so the more specific detector wins (an email beats the
phone pattern inside it; an address beats digits a card pattern might reach).

## API

```python
from cis.redact.pii import Redactor, roster_names, redact

r = Redactor(names=roster_names(p.display_name for p in call.participants))
red = r.redact("Dana, mail me at dana@acme.example")
red.text          # "[PERSON_1], mail me at [EMAIL_1]"
red.counts        # {PiiKind.PERSON: 1, PiiKind.EMAIL: 1}  (property)
r.restore(red.text)   # original text
len(r.restoration)    # distinct values redacted so far

text, mapping = redact(text, names=[...])   # one-shot form (§10.2's signature)
```

| Member | Behaviour |
|---|---|
| `Redactor.placeholder_for(kind, original)` | Stable across the whole call; keyed on exact text (not case-folded, so restored quotes still match the transcript) |
| `Redactor.detect(text)` | `list[Detection(kind, start, end, text)]` |
| `Redactor.redact(text)` | `Redaction(text, restoration, detections)` |
| `Redactor.restore(text)` | Replaces placeholders with originals |

### The restoration map never leaves

`RestorationMap` has **no serialisation method** and its `repr` shows counts, not values, so it
cannot be logged or persisted by accident — the commonest way redaction systems leak is the
mapping ending up in a log line. Keep it in memory for the life of the call's processing.

## Measuring redaction

```python
from cis.redact.pii import measure, Detection, PiiKind
m = measure(text, gold=[Detection(PiiKind.EMAIL, 10, 27, "dana@acme.example")], names=[...])
m.recall      # of the PII present, how much was redacted
m.precision   # of what was redacted, how much was PII
print(m.report())   # per kind, both directions — never averaged
```

Recall failures leak data; precision failures destroy business content the extractor needs.
They are reported separately because they are fixed differently.

## Turning it off

`run(..., redact_before_model=False)` or `cis demo --no-redaction`. Only appropriate when the
model runs on your own infrastructure.

## Limitations

- English/US-centric patterns (SSN shape, US phone formats, English street suffixes).
- No detection of dates of birth, bank account numbers, or health information.
- Spoken-form PII ("five five five, one two one two") is not detected by the digit patterns.
