# Legal and consent policy

**This is the controlling document for this system.** Where it and the code
disagree, the code is wrong. `tests/test_no_voiceprints.py` and
`tests/test_consent_gate.py` exist to keep that from happening quietly.

It is a summary of the positions this implementation takes and the reasoning
behind them. **It is not legal advice**, and none of it substitutes for
counsel who knows your jurisdictions, your customers, and your contracts.

---

## 1. Why this document exists first

The design document this implementation follows opens its milestone ladder
with M0 — *"Legal and consent design, before writing any pipeline code"* — and
that ordering is not ceremonial. Every architectural decision below was
determined by a legal constraint, and each one turned out to also be the
better engineering decision:

| Constraint | Architectural consequence | Engineering benefit |
|---|---|---|
| All-party consent (§2.1) | Per-participant consent ledger, gate before fetch | Reprocessing is safe; the audit trail is the source of truth |
| Otter: third-party eavesdropper (§2.2) | Host consent is never sufficient | The failure is loud instead of silent |
| BIPA voiceprints (§2.3) | Per-speaker tracks, never diarization | Better attribution, better WER, no cross-talk |
| GDPR Art. 17 (§5) | Deletion designed up front | Idempotent keys make deletion enumerable |

---

## 2. Consent

### 2.1 All-party consent

Roughly a dozen US states require **all parties** to a communication to
consent to its recording. The list is not static and the boundaries are
litigated, so the system does not encode a state list as a switch: it
**defaults to all-party consent for every call** and treats anything less as a
per-deployment configuration that has to be argued for in writing.

Two rules make a state list an unreliable control even when it is accurate:

* **The interstate rule.** *Kearney v. Salomon Smith Barney* (Cal. 2006) held
  that California's all-party rule applied to a call between a California
  resident and an out-of-state party. A participant's location does not settle
  which law applies, and neither does yours.
* **Residency, not location.** Illinois BIPA attaches on the basis of
  residency, not where someone happened to be sitting.

`Participant.jurisdiction_hint` exists for the UI. **It is not an input to the
gate**, and the code says so at the field.

### 2.2 What the Otter ruling held

*In re Otter.AI Privacy Litigation*, No. 5:25-cv-06911 (N.D. Cal.), Judge Eumi
K. Lee, order of 13 August 2026, allowed CIPA §631 claims to proceed on the
theory that a notetaking vendor is not a mere extension of the party that
invited it — it is a **third-party eavesdropper** — where it *independently
collects, retains, and uses recordings for its own commercial purposes*.

Three consequences this implementation takes as settled for design purposes:

1. **Single-host consent is not a defence.** The complaint's allegation is
   that consent was obtained "at most, from the host who added the assistant."
   `ConsentLedger` is therefore per participant, and
   `detect.DeletionRequired` is raised when an announcement was made and
   **nobody other than the announcer** affirmatively consented.
2. **The deploying company is exposed too.** Customers were named as
   co-defendants. "Our vendor handles compliance" is not a position a customer
   can take, which is why this system surfaces the consent state to the
   deploying company rather than absorbing it.
3. **Using the data for your own purposes is the hinge.** Training on customer
   audio moves a vendor toward the "independent commercial purpose" the order
   turns on. Hence the commitment in §4.

### 2.3 Do not build voiceprints — **no voiceprints**

**This system never derives speaker identity from vocal characteristics.**

Illinois BIPA treats a "voiceprint" as a biometric identifier. Damages are
**$1,000 for negligent and $5,000 for intentional violations, per identifier**,
with a private right of action and no requirement to show injury. A
voice-embedding pipeline run over a year of sales calls produces a per-person
identifier count that is an existential number, not a line item.

The commitments, stated so they can be quoted back at us:

* **No voiceprints.** No voice embeddings, speaker embeddings, x-vectors,
  d-vectors, or voice-characteristic speaker models are computed, stored, or
  transmitted — not by us, and not by a sub-processor on our behalf.
* **Speaker identity comes from platform account identity**, per-speaker audio
  tracks, stereo channel assignment, or a human labelling turns. All four are
  non-voice.
* **No training on customer data.** Customer audio, transcripts, and
  extractions are not used to train or fine-tune any model, ours or a
  provider's. Provider zero-retention / no-training tiers are a contractual
  requirement, not a preference.
* **Where attribution is unavailable, we refuse rather than infer.**
  `ingest.NoSpeakerAttribution` is raised, and the exception message carries
  the three supported alternatives (§3.2).

`tests/test_no_voiceprints.py` enforces the first commitment mechanically: an
AST scan of every module under `src/` and `eval/` for imports of
speaker-recognition libraries, a pattern scan for voice-embedding call
surfaces, a scan of the dependency files, and a check that this document still
states the commitments. One file -- `ingest/tracks.py` -- is allowlisted for
*mentioning* diarization, and a further test asserts that its mention is a
refusal rather than a use. A third test fails if an allowlist entry stops
naming a forbidden term at all, because a dead entry silently exempts a whole
file from the scan while looking like a documented exception.

### 2.4 Outside the US

* **GDPR.** A recording is personal data; a transcript usually contains
  special-category data eventually, because people volunteer health and
  financial details while explaining their situation. Lawful basis is normally
  consent for recording; legitimate interest is a weak basis for recording a
  conversation, and consent must be as easy to withdraw as to give —
  `ConsentLedger.withdraw()` appends a withdrawal record rather than editing
  one.
* **ePrivacy.** Member-state implementations of the confidentiality of
  communications generally require all-party consent.
* **PIPEDA (Canada)** requires knowledge and consent, and meaningful notice.

---

## 3. What the system does about it

### 3.1 The gate runs before the fetch

`consent.gate.may_process` is evaluated **before any audio is fetched**, not
before transcription and not before storage. Section 3.2 of the design
document puts the reason plainly: if you have downloaded the recording, you
have already arguably intercepted it.

`workflows.pipeline.run` calls the gate first and returns before touching the
platform client. `tests/test_pipeline.py` asserts this as a **call count on
the platform client**, because "processing stopped" and "nothing was fetched"
are different claims and only the second one is the control.

`ProcessingDecision` carries a `roster_fingerprint`, and
`verify_decision_still_valid` re-checks it inside `ingest()`. Minutes pass
between the gate and the fetch in a durable workflow, and a participant
joining in that window is exactly the case the gate exists to catch.

### 3.2 When attribution is unavailable

`ingest()` raises `NoSpeakerAttribution`, whose message lists the supported
options in the design document's order of preference:

1. **Transcribe unattributed** (`require_attribution=False`). Usable for
   search and topics; **not** usable for "who committed to what". The result
   is marked, and commitment, next-step, champion, and economic-buyer
   extraction refuse to run against it.
2. **Ask the rep to label the turns** in the review UI.
3. **Skip the call.** Sometimes correct.

Voice diarization is not on the list and is not a fallback.

### 3.3 Silence is not consent

`ConsentMethod.UNKNOWN` covers silence, a late joiner who missed the
announcement, and a disclaimer in a calendar invite. None of them is consent,
and `CONSENTING_METHODS` is a single frozenset so that widening what counts as
consent is a one-line diff someone has to defend in review.

---

## 4. Retention, deletion, and access

* **Published retention schedule.** BIPA requires one for biometric data. We
  should not hold biometric data at all (§2.3), and the discipline applies to
  transcripts regardless.
* **Deletion is complete or it is not deletion.** Audio, transcripts,
  extractions, scores, CRM writebacks, search indexes, logs, and backups. A
  vector index and a log aggregator are meaningfully harder to delete from
  than a database row, which is why `crm.idempotency.identity_key` produces a
  key that is stable across reprocessing and prefixed by the call
  (`call:{call_id}:...`). Deletion enumerates by that prefix, so the CRM
  integration has to support prefix search on the external key, or the
  writeback journal has to be retained as the record of what was written.
  One of the two is a requirement, not an optimisation.
* **Deletion on request** (GDPR Art. 17) with a documented SLA.
* **Per-participant deletion.** One participant may ask for removal from a
  call others consented to. Decide the answer before someone asks.
* **Access is scoped and audited.** A rep sees their own calls, a manager
  their team's. Access to a recording is itself an auditable event.

### 4.1 Redaction before third-party processing

`redact.pii` replaces emails, phone numbers, card numbers, government IDs, and
street addresses before a transcript reaches a hosted model, and pseudonymises
roster names consistently. This applies to **every** model in the pipeline,
not only the extractor: the scoring judge receives an evidence block that
quotes the transcript, and a quote naming who signs off on spend carries an
email address along with it. The restoration map never leaves our
infrastructure: `RestorationMap` has no serialisation method and its `repr`
withholds its contents, because the commonest leak is not a missed pattern but
a mapping in a log line.

Roster names are expanded into the full name and its parts, because a
transcript says "Dana", not "Dana Chen"; parts that are ordinary English words
("will", "mark", "grace") are skipped, since redacting those removes more
business content than PII.

**Stated limitation:** names are detected from the call roster, not by
pattern. A person named on the call who is not *on* the call — a CFO discussed
in the third person — is not redacted. The bundled demo shows exactly this
case and says so on screen. Redaction quality is measured in both
directions (`redact.pii.measure`), because over-redaction silently degrades
extraction and a single accuracy figure would let one be traded for the other
without anyone noticing.

---

## 5. Sub-processors

Using a hosted LLM or ASR provider makes them a sub-processor of your
customer's data:

* Disclose them in the DPA.
* Be on a zero-retention, no-training tier. Get it in writing.
* Data residency: an EU customer may require EU processing.
* Your customer's own DPA with *their* customers may prohibit specific
  sub-processors. Ask before assuming.

---

## 6. What to do when this document is wrong

It will be. Rulings land, statutes change, and a customer will present a
requirement none of this anticipated.

Change **this document first**, then the tests that assert it, then the code.
That order is why `test_no_voiceprints.py` asserts the presence of the
commitments in this file: it fails if the code and the document drift apart,
whichever one moved.
