# Notes on the design document

Places where this implementation departs from `Documentation/README.md`, and
why. Every entry is a deliberate divergence with a test behind it, not a
shortcut.

---

## 1. The idempotency key does not achieve its own goal (§9.4)

**The document says:**

> Reprocessing a call — a better model, a bug fix, a retry — must not
> duplicate anything.

and then:

```python
key = f"call:{call_id}:tv{transcript_version}:ev{extraction_version}"
```

**The problem.** Reprocess with a better model. `extraction_version` changes,
so the key changes, so `find_by_external_key` misses, so `upsert` takes the
`create` branch — and the CRM now holds two notes for one call. The three
cases the section names as its motivation are exactly the three cases that
change a version in the key. Only a bare retry, with both versions unchanged,
actually deduplicates.

**What this implementation does.** The key identifies the *thing*, not the
*run*:

```
call:{call_id}:{kind}              one summary per call, forever
call:{call_id}:task:{fingerprint}  one task per distinct commitment
```

The versions move into the payload as `Provenance`, where they answer the
question they are actually good for — what produced the values currently in
this record — and a stale record announces itself without having been
duplicated to do so.

The trade is real: a stable key means reprocessing **overwrites** the previous
result. That is the correct default for a CRM field, and the protection
against overwriting a *human's* edit lives in `writeback.write_field`, where
it belongs.

`tests/test_crm.py::TestIdempotency::test_reprocessing_with_a_better_model_does_not_duplicate`

---

## 2. `criterion.key in c.field` does not link criteria to fields (§7.2)

**The document says:**

```python
relevant = [c for c in claims if criterion.key in c.field]
```

**The problem.** It is a substring test across two vocabularies that only
happen to line up, and in MEDDICC they already do not: the criterion key is
`identify_pain` and the field is `pain`, so `"identify_pain" in "pain"` is
false and the criterion can never score above 0. Silently — a criterion with
no evidence looks exactly like a criterion nobody discussed.

**What this implementation does.** Each criterion declares its fields in the
framework file:

```yaml
- key: identify_pain
  fields: [pain]
```

and the loader rejects a criterion with an empty or unknown field list at load
time, rather than at the first call of a batch. BANT's `authority` reads two
fields, which the substring version could not express at all.

`tests/test_scoring.py::TestFrameworkIsData::test_the_field_link_is_declared_not_inferred`

---

## 3. Level 0 is assigned by code, and a judge cannot return it (§7.2, §7.3)

**The document's sketch** returns level 0 with "no supporting evidence found"
when no claims match, and otherwise hands off to the model.

**Extended here.** The same rule, taken to its conclusion: when evidence *does*
exist, level 0 — "not discussed" — is false as a matter of record, so it is
removed from the scale offered to the judge, and a judge that returns it
raises `InvalidAnchorLevel`. A judge that thinks the evidence is weak has
level 1 for that.

This also makes the loader's requirement that anchor 0 mean absence
load-bearing rather than stylistic: the shortcut of assigning 0 without a
model call is only sound while 0 means the same thing in every framework.

---

## 4. The framework's own fields are not enough to run the pipeline (§7.1, §9.3)

MEDDICC has seven criteria and not one of them is a commitment. A pipeline
that extracted only what the loaded framework scores would never produce a
task — and next steps are the document's own "highest-value output" (§9.3).

`workflows.pipeline.default_fields` therefore extracts the framework's fields
**plus** the task fields, plus budget and timeline: MEDDICC does not score
them, the CRM still wants them.

`tests/test_pipeline.py::TestEndToEnd::test_a_commitment_becomes_a_task_awaiting_confirmation`

---

## 5. Overlap yields to forward progress (§6.2)

Not a contradiction in the document, but an unspecified case that has to be
decided somewhere.

When the token budget fits only a single turn, honouring an overlap of 3 would
mean either re-emitting that turn forever or emitting chunks at several times
the budget. The guarantee that survives is forward progress: every turn
appears in some chunk, in order, at least once. The overlap is what gives way,
and `tests/test_grounding.py` asserts both halves — the overlap when chunks
hold more turns than the overlap width, and the clean single-turn walk when
they do not.

---

## 6. Redaction is wired through extraction, not beside it (§10.2)

**The document** gives `redact()` its signature and the workflow sketch lists
`redact` as an activity between `transcribe` and `extract`. It does not say
what happens to grounding, and the obvious reading breaks it: if the model
sees redacted text, the model quotes redacted text, and a span quoting
`[EMAIL_1]` does not quote the transcript.

**What this implementation does.** Redact outbound, restore inbound.
`extract_chunk` takes an optional redactor, redacts the chunk before the model
sees it, and restores placeholders in each returned quote before locating it.
The model never sees the PII; the stored span quotes the words that were
actually said and validates against the transcript.

`tests/test_pipeline.py::TestRedactionCrossesTheModelBoundary`

---

## 7. Host-only consent triggers deletion (§2.2, §3.3)

The document says single-host consent is not a defence, and separately that an
announcement nobody answers means the audio should not be retained.

The first version of `consent.detect` checked "did anyone consent?", which the
announcer's own record satisfied — so a call where the rep announced and
nobody replied passed. That is precisely the Otter allegation.

The threshold is now **at least one affirmative consent from someone other
than the announcer**. `tests/test_consent_gate.py` splits the case in two:
host-only consent demands deletion, one other participant responding is enough
to continue.

---

## 8. Who said it is a fact about the transcript, not a model output (§7.1)

`GroundedClaim.subject_participant_id` is who a claim is *about*, and it comes
from the model. An earlier version of `gather_evidence` used it as *who said
it*, falling back to the transcript only when it was absent.

That made the "vendor-asserted ROI does not count" rule defeatable by the
thing it is protecting against: a model that tagged the rep's own "most
customers see a three hundred percent return" as being about the customer -- a
natural thing to emit -- would have scored MEDDICC Metrics 3 on vendor
marketing. The speaker now comes from the segment the span sits in, always,
which is platform identity and not model output.

`tests/test_scoring.py::TestWhoSaidItComesFromTheTranscript`

---

## 9. Redaction covers the judge as well as the extractor (§10.2)

The workflow sketch in §13.2 lists `redact` as one activity between
`transcribe` and `extract`, which reads as though the extractor is the only
model in the pipeline. The judge is one too, and the evidence block it
receives quotes the transcript -- a quote naming who signs off on spend
carries an email address along with it. `score_call` takes the same redactor,
and the rationale is restored on the way back.

---

## 10. A one-party policy does not override an explicit refusal (§3.2)

The design document describes one-party mode as a relaxation of how many
people must have consented. Implemented literally, one consenting participant
lets processing proceed past another participant's `DECLINED` or `WITHDRAWN`
record.

That cannot be right: "one party consented" answers "did anyone say yes", not
"did anyone say no", and `docs/legal.md` commits to withdrawal being as easy
as consent. `DECLINED` and `WITHDRAWN` now block in every mode. `UNKNOWN` does
not -- silence is the case one-party mode exists for.

Relatedly, `late_joiners_require_consent=False` now requires `authorised_by`,
exactly as `ONE_PARTY` does. It relaxes §3.4, and an unattributed relaxation
of a consent rule is the thing that cannot be explained afterwards.

`tests/test_consent_gate.py::TestOnePartyDoesNotOverrideARefusal`

---

## 11. Scope deliberately not implemented

Stated plainly rather than left for someone to discover:

* **Temporal / durable workflow (§13.2).** `workflows/pipeline.py` is a plain
  function. Every stage is already a pure function of its inputs with failures
  as exceptions, which is what makes it mechanically translatable into
  activities; `PipelineResult` carries the per-stage outcome a workflow engine
  would otherwise provide. Adding the engine would add an operational
  dependency to a repository whose point is the decisions above it.
* **Real ASR (§5).** `Transcriber` is a protocol with a sample implementation.
  `_require_word_timings` enforces §5.3 against whatever is plugged in.
* **Platform clients (`ingest/zoom.py`, `teams.py`, `dialer.py`).**
  `PlatformClient` is a protocol. Note what it does *not* expose: there is no
  `download_mixed_recording` for the rest of the pipeline to reach for.
* **Review UI (§M6).** `PipelineResult.needs_review` is the data it would
  render.
* **Cost model (§11) and access control (§10.5).** Not modelled.

---

## 12. Reference implementations, as built

| Document | Here |
|---|---|
| §16.1 span-validated extraction | `extract/grounded.py` |
| §16.2 `krippendorff_alpha` | `eval/agreement.py`, verified against published values |
| §16.3 `test_no_voice_embeddings_persisted` | `tests/test_no_voiceprints.py`, extended to `eval/` and to the dependency files |
| §9.1 `write_field` | `crm/writeback.py` |
| §9.2 `FieldPolicy` | `crm/writeback.py`, with the auto-write floor enforced in `__post_init__` |
| §9.3 `propose_tasks` | `crm/tasks.py`, with `requires_confirmation=False` rejected by the constructor |
| §7.2 `score` | `score/anchors.py` |
