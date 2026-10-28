# Consent

Package: `cis.consent` · Design: §2, §3 · Policy rationale: [`v1/docs/legal.md`](legal.md)

Consent is the first thing the system does and the gate on everything else. It is modelled as
**state** (an append-only ledger of evidence-backed records) rather than a flag on a call.

> This page documents what the code does. It is not legal advice; [`legal.md`](legal.md)
> explains the reasoning and flags where counsel's view is needed.

## Model (`consent/model.py`)

### `Participant`

| Field | Type | Notes |
|---|---|---|
| `participant_id` | `str` | Stable platform ID (e.g. `p_dana`) |
| `display_name` | `str \| None` | Used for redaction and vocabulary hints |
| `email` | `str \| None` | Used for task ownership |
| `role` | `ParticipantRole` | `rep`, `prospect`, `unknown` — from calendar/CRM, never inferred from voice |
| `jurisdiction_hint` | `str \| None` | Informational |
| `joined_late` | `bool` | Late joiners did not hear the announcement |

### `ConsentMethod`

| Value | Permits processing |
|---|:-:|
| `verbal_acknowledged` | ✔ |
| `platform_consent` | ✔ |
| `written_prior` | ✔ |
| `declined` | ✘ |
| `withdrawn` | ✘ |
| `unknown` | ✘ (the default when no record exists) |

### `ConsentRecord`

Immutable (`frozen` dataclass). Fields: `call_id`, `participant_id`, `method`,
`evidence_ref` (platform event ID, document reference, or `span:<id>`), `recorded_at`,
optional `email`, `jurisdiction_hint`, `note`. A consenting method without evidence is not
evidence of consent.

### `ConsentLedger`

Append-only. There is no edit or delete.

| Method | Purpose |
|---|---|
| `append(record)`, `extend(records)` | Add records |
| `for_call(call_id)` | All records for a call |
| `history(call_id, participant_id)` | Oldest first |
| `current(call_id, participant_id)` | Latest record, or `None` |
| `effective(call_id, participant_id)` | Method the gate acts on; `UNKNOWN` if none |
| `withdraw(call_id, participant_id, *, at, note=None)` | Appends a `WITHDRAWN` record |

`snapshot(ledger, call)` returns `{participant_id: ConsentMethod}` for the whole roster.

### `Call`

`call_id`, `external_id` (platform meeting ID), `participants`, `scheduled_at`,
`source_type` (default `zoom_cloud`; `dialer_two_leg` enables stereo attribution), `channels`,
`announcement_span_id`.

## The gate (`consent/gate.py`)

```python
from cis.consent.gate import may_process, ConsentPolicy, ConsentMode

decision = may_process(call, ledger)                 # all-party by default
decision.require()                                   # raises ConsentDenied if not allowed
```

### Policy

```python
ConsentPolicy(
    mode=ConsentMode.ALL_PARTY,          # or ONE_PARTY
    authorised_by=None,                  # REQUIRED for ONE_PARTY
    authorised_at=None,
    late_joiners_require_consent=True,   # False also requires authorised_by
    policy_version="1",
)
```

Relaxations raise `ValueError` at construction without a named authoriser — an unattributed
relaxation of a consent rule cannot be explained afterwards.

### Per-participant blockers

| `Blocker` | When |
|---|---|
| `declined` | Effective method is `DECLINED` |
| `withdrawn` | Effective method is `WITHDRAWN` |
| `no_consent_record` | Effective method is `UNKNOWN` — silence and calendar boilerplate are not consent (§3.4) |
| `late_joiner` | `joined_late` and the only consent is `verbal_acknowledged` (they did not hear the announcement) |
| `no_participants` | Empty roster |

### Decision rules

| Mode | Condition | Result |
|---|---|---|
| any | empty roster | ✘ |
| `all_party` | any blocker | ✘ — lists every blocked participant |
| `all_party` | no blockers | ✔ |
| `one_party` | anyone declined or withdrew | ✘ — **one-party never overrides an explicit refusal** |
| `one_party` | ≥ 1 consenting participant | ✔ (reason names the authoriser) |
| `one_party` | nobody consenting | ✘ |

### `ProcessingDecision`

| Field | Meaning |
|---|---|
| `allowed` | The answer |
| `call_id`, `mode`, `policy_version`, `decided_at` | Audit trail |
| `roster_fingerprint` | Order-independent SHA digest of participant IDs |
| `reason`, `remediation` | Human-readable why / what to do |
| `blocks` | `ParticipantBlock(participant_id, display_name, blocker, detail)` per blocked person |
| `consenting_participants` | IDs that passed |

### Roster re-verification

`verify_decision_still_valid(decision, call)` is called by `ingest()` immediately before any
bytes move. It raises `ConsentDenied` if the decision was for another call or the roster
fingerprint changed (someone joined between gate and fetch).

## Verbal consent detection (`consent/detect.py`)

The chicken-and-egg problem: you need a transcript to detect verbal consent, but consent gates
transcription. The design resolves it with a **narrowly scoped pass** whose only possible
outputs are consent records or deletion — and flags it as the weakest point in the design,
for counsel to review.

```python
from cis.consent.detect import detect_consent, DeletionRequired

try:
    run = detect_consent(call, opening_transcript)
except DeletionRequired as e:
    delete_everything(e.call_id)        # no announcement found
else:
    ledger.extend(run.records)
    # run.unresolved_participants stay UNKNOWN → gate will block in all-party mode
```

| Constant / rule | Value |
|---|---|
| `CONSENT_WINDOW_MS` | 90 000 ms. A module constant so widening it is a reviewed diff |
| Announcement | Regexes like "recording this call", "AI notetaker", "taking notes … record" |
| Responses considered | Only segments **after** the announcement, inside the window, with a known speaker; first response per participant counts |
| Affirmative | "yes", "sure", "that's fine", "no problem", "go ahead", "I'm fine with that" … → `VERBAL_ACKNOWLEDGED` |
| Negative | "I'd rather not", "please don't", "not comfortable with", "turn it off", "I don't consent" … → `DECLINED` |
| Announcer | Recorded as `VERBAL_ACKNOWLEDGED` with the announcement span as evidence |
| Silence | Participant listed in `unresolved_participants`; **no record is invented** |
| Host-only consent | If nobody other than the announcer affirms, `DeletionRequired` (the Otter allegation; see notes-on-the-spec §7) |

`ConsentDetectionRun` records exactly what was examined: `window_ms`, `segments_examined`,
`announcement`, `records`, `unresolved_participants`, `detector_version`, `ran_at`.

Helpers: `find_announcement(transcript)`, `classify_response(text) -> ConsentMethod`.

### Limitations

- Pattern-based and English-only. Sarcasm, conditional answers ("sure, as long as…") and
  non-English responses are not understood.
- A response is attributed by the segment's `participant_id`, which comes from per-speaker
  tracks; with unattributed audio nobody can be credited.
- Treat this as a capture aid, not a substitute for platform consent prompts
  (`platform_consent`) or written consent.

## CLI

```bash
make gate                                   # allowed, then refused with p_marco dropped
python3 -m cis.cli gate --drop p_dana p_marco
```

## Extending

- **New consent source** (e.g. a platform webhook): create `ConsentRecord(method=PLATFORM_CONSENT,
  evidence_ref="<event id>")` and `ledger.append` it. No gate change needed.
- **Persisting the ledger**: implement storage that only inserts. Do not add update/delete
  paths; withdrawal is a new record.
