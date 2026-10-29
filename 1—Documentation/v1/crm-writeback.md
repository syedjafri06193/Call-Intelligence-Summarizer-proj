# CRM Write-back & Tasks

Package: `cis.crm` · Design: §9

> "This is where trust is won or lost, permanently. A model that clobbers a rep's carefully
> written note has ended the product's credibility for that rep and everyone they talk to."

`run()` does **not** write to the CRM. It returns scores, claims and proposed tasks; write-back
happens after review using the functions below. v1 has no real CRM connector — the `CRM`
protocol is implemented by fakes in `tests/test_crm.py`.

## Field writes (`crm/writeback.py`)

### Field policy

```python
FieldPolicy(
    field="AI_Summary__c",
    mode=WriteMode.AUTO,          # AUTO | REVIEW | SUGGEST_ONLY
    min_confidence=0.0,           # 0..1
    requires_span=True,
    measured_precision=0.97,      # REQUIRED for AUTO, must be >= 0.95
    append=False,
)
```

`mode=AUTO` without `measured_precision`, or with precision below
`AUTO_PRECISION_FLOOR = 0.95`, raises `PolicyError` at construction. "It seems good" is not a
threshold; the number comes from the evaluation set.

A field whose name starts with `ai_` / `AI_` is a **dedicated AI field** — it has never held
human writing, so overwriting it cannot destroy anyone's work. This is the safest default
(§9.1, pattern 1).

### `write_field(crm, record_id, field, value, claim, policy, *, call_id="") -> WriteResult`

Checks, in order:

| # | Check | Result |
|---|---|---|
| 1 | `requires_span` and the claim has no spans | `SKIPPED` (with suggestion) |
| 2 | `claim.confidence < min_confidence` | `SUGGESTED` |
| 3 | Field has content, isn't a dedicated AI field, and `crm.is_ai_authored` is false → **human-written** | `SUGGESTED` — never replaced (unless `append`) |
| 4 | `mode == SUGGEST_ONLY` | `SUGGESTED` |
| 5 | `mode == REVIEW` | `SUGGESTED` (a human accepts/edits first) |
| 6 | `append=True` | `APPENDED` below existing text after `[AI note from call <call_id>]` |
| 7 | otherwise (`AUTO`) | `WRITTEN` |

Every non-write returns a `suggestion`, so the rep sees what the pipeline found and declined
to write — a silent skip would look like "found nothing".

`WriteResult(action, field, reason, suggestion, record_id, spans)`; `.landed` is true for
`WRITTEN`/`APPENDED`. `summarise(results)` counts by `WriteAction`
(`written`, `appended`, `suggested`, `skipped`, `failed`).

### Recommended policies

| Field type | Policy |
|---|---|
| `AI_Summary__c`, `AI_MEDDICC__c` | `AUTO` once precision measured ≥ 0.95 |
| Factual criteria (`kind: factual`) to standard fields | `REVIEW` until measured, then `AUTO` |
| Judgment criteria | `SUGGEST_ONLY` / `REVIEW` permanently |
| Rep notes / description | `append=True` with `REVIEW` |

## Idempotency (`crm/idempotency.py`)

> "Reprocessing a call — a better model, a bug fix, a retry — must not duplicate anything." §9.4

**v1 deliberately diverges from §9.4.** The design's key
`call:{id}:tv{transcript_version}:ev{extraction_version}` changes exactly when you reprocess
with a new model or transcript, so it creates a second note — failing its own goal. v1's key
identifies the **thing**, and versions go into the payload as provenance instead
([notes-on-the-spec §1](notes-on-the-spec.md)).

```python
identity_key("call_demo_001", "summary")          # "call:call_demo_001:summary"
identity_key("call_demo_001", "task",
             task_fingerprint("Send the security questionnaire."))
# "call:call_demo_001:task:0c1ef0e76f124e50"
```

- `:` is forbidden in `call_id` and `kind`.
- `task_fingerprint` normalises case, whitespace and trailing punctuation, then SHA-256 (16 hex).
  Nothing cleverer: merging two genuinely different commitments loses a task, which is worse
  than a duplicate.

`Provenance(call_id, transcript_version, extractor_version, framework_id, judge_version)`
→ `as_fields()` = `ai_call_id`, `ai_transcript_version`, `ai_extractor_version`,
`ai_framework`, `ai_judge_version`, written on every upsert.

`upsert(crm, key, payload, *, provenance=None)` — find by external key → update, else create.
Never both. The find-then-create race (two workers) is acknowledged; swap to the CRM's native
external-ID upsert in this one function.

### The `CRM` protocol

```python
class CRM(Protocol):
    def find_by_external_key(self, key: str) -> Any | None: ...
    def create(self, payload: Mapping[str, Any], *, external_key: str) -> Any: ...
    def update(self, record_id: str, payload: Mapping[str, Any]) -> Any: ...
    def get_field(self, record_id: str, field: str) -> str | None: ...
    def is_ai_authored(self, record_id: str, field: str) -> bool: ...
```

Records returned may be mappings with `"id"` or objects with `.id`.

Mapping to real CRMs (guidance):

| | Salesforce | HubSpot |
|---|---|---|
| External key | Custom External ID field + upsert endpoint | Unique custom property |
| `is_ai_authored` | Field history `CreatedById` = integration user | Property history `sourceType` = integration |
| Dedicated AI field | `AI_Summary__c` | `ai_summary` |

## Rate limits and failures (§9.5)

```python
journal = WritebackJournal()
entry = write_payload(crm, journal, call_id, "summary", payload,
                      provenance=prov, discriminator=None)
journal.pending()      # not yet landed — surface these in the UI, don't just log
journal.failures()
journal.all_landed
```

- The journal entry is recorded **before** the attempt, so a crash still leaves it pending.
- `with_backoff(fn, attempts=5, base_delay=0.5, sleep=time.sleep)` retries on `RateLimited`,
  sleeping `max(retry_after, delay)` and doubling the delay. `sleep` is injectable for tests
  and durable-workflow timers.
- After the final failure, `entry.last_error` is set and the entry stays pending.

## Tasks (`crm/tasks.py`)

> "The highest-value output and the highest-risk one, because a hallucinated task is an action
> taken in the world." §9.3

`propose_tasks(claims, call, *, attributed=True, threshold=0.7)` turns `commitment` and
`next_step` claims into `ProposedTask`s. Four gates:

| Gate | Effect |
|---|---|
| Ungrounded | Never a task (`ProposedTask` with no evidence raises) |
| `confidence < TASK_THRESHOLD (0.7)` | Never a task — live models' `implied` (0.5) never passes, `explicit` (0.9) does |
| Unattributed transcript | No tasks at all (no owner can be resolved) |
| `requires_confirmation` | Always `True`; setting `False` raises `TaskMustBeConfirmed` |

### `ProposedTask`

`title`, `evidence`, `confidence`, `due`, `due_note`, `owner_participant_id`, `owner_email`,
`requires_confirmation=True`; `.quote`.

### Owners

`resolve_owner` uses only the roster: the claim's `subject_participant_id` → that participant's
ID and email. No match → no owner; the rep assigns it at confirmation. Guessing "probably the
rep" puts tasks on the wrong person's list. (`rep_of(call)` returns the rep's `participant_id`, for UIs that
want to pre-fill the picker.)

### Due dates

`resolve_due(text, call_date)` resolves against the **call date** and has no access to the
clock — a call processed the following Monday would otherwise produce a task due in the past.
Examples for a call on **Wednesday 16 Sep 2026**:

| Phrase | Due |
|---|---|
| today / tonight / end of day / Wednesday | 2026-09-16 |
| tomorrow | 2026-09-17 |
| by Friday / this week / end of the week | 2026-09-18 |
| next Monday | 2026-09-21 |
| next week / end of next week | 2026-09-25 (Friday) |
| end of the month / end of quarter | 2026-09-30 |
| in two weeks | 2026-09-30 |
| next month | 2026-10-01 (first of the month) |
| by the 15th · mid next month · before November · in a few weeks | `None`, note "could not resolve …" |
| sometime soon | `None`, no note |

Unresolvable but date-like phrases return `(None, note)` rather than a guess; the note goes to
the review UI. A bare weekday resolves to the next occurrence, including the call day itself.

## Known issue

Duplicate tasks: one sentence extracted as both `commitment` and `next_step`, with slightly
different values, yields two proposals with different fingerprints. Fix planned in
reconciliation (dedupe by shared span) — see [`v1/ROADMAP.md`](ROADMAP.md).
