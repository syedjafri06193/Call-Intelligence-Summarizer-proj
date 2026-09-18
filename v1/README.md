# Call Intelligence Summarizer — v1

Transcribes sales discovery calls, scores them against a qualification
framework, and writes structured notes and next steps back to the CRM.

The interesting part of this project is not the summarisation. It is
everything the summarisation is not allowed to do: record without consent,
guess who spoke, claim something the transcript does not say, overwrite a
rep's note, or create a task nobody confirmed. Those constraints shape the
architecture, and most of them are enforced by code rather than by convention.

```bash
make demo      # the whole pipeline on a sample call, offline, no API key
make test      # 387 tests
```

---

## The five decisions that shaped this

**1. Consent is state, and the gate runs before the fetch.**
Not before transcription, not before storage — before the audio is fetched, on
the reasoning that downloading a recording is already arguably intercepting
it. Consent is per participant, evidence-backed, and append-only: withdrawal
adds a record rather than editing one. `tests/test_pipeline.py` asserts the
gate as a **call count on the platform client**, because "processing stopped"
and "nothing was fetched" are different claims and only the second is the
control.

**2. No voiceprints, ever.**
Speaker identity comes from per-speaker platform tracks, stereo channels, or a
human — never from vocal characteristics. This removes the least reliable
stage in any call pipeline and the one that creates BIPA exposure at
$1,000–$5,000 *per identifier*, in one decision. Where per-speaker audio is
unavailable the pipeline **raises**, and the exception carries the three
supported alternatives. `tests/test_no_voiceprints.py` scans every module for
speaker-recognition imports, voice-embedding call surfaces, and forbidden
dependencies, and checks that `docs/legal.md` still says so.

**3. Every claim cites a span, or it does not exist.**
`GroundedClaim` raises in `__post_init__` on an empty span list — the
constraint is structural, not a convention. A quote the model invented cannot
be located in the transcript, so it is discarded and logged, and that log line
is the hallucination monitor. Spans are located against the *transcript's*
text, not the model's reformatting of it, so a span cannot pass validation
against itself.

**4. Ordinal anchors, and a judge that cannot be led.**
Never "a score out of 10" — a number emitted by a language model is a token
sequence, not a measurement. The judge picks a level from a written scale, and
each of the known judge biases has a structural mitigation: the scoring
functions never receive a transcript (verbosity), there is no parameter
through which a prior score could arrive (sycophancy), the anchors and the
evidence bar are in the prompt (leniency drift), and rerun variance is
measured and published (self-inconsistency).

**5. Human agreement is measured before model accuracy.**
A model at 0.36 on "champion" looks broken until you know that three
experienced humans only reach 0.38 on the same calls. Both columns are
published, always. The alpha implementation is verified against Krippendorff's
published worked example on all three difference functions, because an
agreement statistic that is subtly wrong produces a confident ceiling that is
not the ceiling.

---

## Layout

```
v1/
├── src/cis/
│   ├── consent/      model.py, gate.py (runs before the fetch), detect.py
│   ├── ingest/       tracks.py — per-speaker only; NO voice diarization
│   ├── asr/          vocabulary.py — per-call hints, hashed onto the transcript
│   ├── transcript/   immutable, versioned, word-level timings
│   ├── extract/      chunk.py, grounded.py (span validation), reconcile.py
│   ├── score/        framework.py (versioned YAML), anchors.py
│   ├── redact/       pii.py — redact outbound, restore inbound
│   ├── crm/          writeback.py, tasks.py, idempotency.py
│   ├── workflows/    pipeline.py — the ordering is the legal control
│   ├── samples.py    the bundled call and the offline stand-ins
│   └── cli.py
├── eval/             agreement.py (the ceiling), extraction_metrics.py, consistency.py
├── docs/
│   ├── legal.md              ← the controlling document
│   ├── evaluation.md         agreement ceilings and how to report against them
│   ├── notes-on-the-spec.md  where this diverges from the design doc, and why
│   └── frameworks/           meddicc.yaml, bant.yaml
├── samples/          discovery_call.json
└── tests/
```

`docs/legal.md` is the controlling document. Where it and the code disagree,
the code is wrong.

---

## The demo

```
$ make demo

consent gate  (call_demo_001)
------------------------------------------------------------------------
  allowed:      True
  consenting:   p_rep, p_dana, p_marco

extraction
------------------------------------------------------------------------
  budget             $50k approval threshold
                     "anything over fifty thousand"  [84s]
  budget             $80k
                     "it's more like eighty thousand"  [151s]
  ...
  quotes offered 30, located 29, hallucination rate 3.3%
    discarded champion: "Marco said he would champion this to the exec team"

contradictions (surfaced, not collapsed)
------------------------------------------------------------------------
  budget: '$50k approval threshold' (at 84s) then '$80k' (at 151s)

score  (MEDDICC@3)
------------------------------------------------------------------------
  Metrics              3  [auto]    2 supporting quote(s) in evidence
                          excluded: '300% first-year return': stated by the rep
  Champion             2  [review]  1 supporting quote(s) in evidence
  ...

proposed tasks (every one awaiting confirmation)
------------------------------------------------------------------------
  Send the security questionnaire by Friday
     owner:   sam@northwind.example
     due:     2026-09-18
     confirm: True
```

Five things are visible there, each of which is a design position:

* **The fabricated quote is caught.** The stand-in model invents one quote on
  purpose, so the hallucination counter has something real to report. A demo
  that prints 0.0% every time teaches nothing about the check.
* **The budget contradiction is surfaced, not collapsed.** "$50k then $80k" is
  usually the most interesting fact on the call. A pipeline that silently
  picks one is discarding it.
* **Vendor-asserted ROI is excluded with its reason.** MEDDICC's Metrics
  definition requires the number to come from the prospect. "We heard a number
  but the rep said it" is a different state from "nobody mentioned a number",
  and the rep can tell them apart.
* **Judgment criteria are marked for review**, factual ones for auto-write,
  from the framework file — which sets it from measured inter-rater agreement,
  not from taste.
* **The due date resolves against the call date.** Friday means the Friday
  after the call, not after the batch job. Nothing in `crm/tasks.py` can read
  the clock, and a test asserts that by scanning the source.

Other commands:

```bash
cis gate --drop p_marco      # watch the gate refuse, with a remediation
cis framework                # MEDDICC as loaded, anchors and all
cis agreement eval/labeled/sample_labels.json
```

---

## What is enforced by code

| Rule | Where it cannot be worked around |
|---|---|
| Consent before fetch | `pipeline.run` returns before touching the client; asserted as a fetch count |
| Host consent is not enough | `detect` raises `DeletionRequired` unless a non-announcer consented |
| No voice diarization | `ingest` raises; AST + pattern + dependency scans in tests |
| No ungrounded claim | `GroundedClaim.__post_init__` raises on empty spans |
| No attribution ⇒ no commitments | `ATTRIBUTION_DEPENDENT` fields refused on an unattributed transcript |
| Level 0 means "not discussed" | Assigned by code; a judge returning it raises |
| No prior score in the prompt | No parameter exists; asserted with `inspect.signature` |
| No auto-write without a number | `FieldPolicy.__post_init__` raises below the precision floor |
| Never overwrite a human | `write_field` returns a suggestion instead |
| Every task confirmed | `ProposedTask.__post_init__` rejects `requires_confirmation=False` |
| No silent write failure | `WritebackJournal` records the attempt before it is made |

---

## Running it

Python 3.11+. One runtime dependency (`pyyaml`), one test dependency
(`pytest`).

```bash
make install
make test
make demo
```

There is no network access and no API key anywhere in the demo or the tests.
The model and judge boundaries are protocols — `extract(chunk_text, fields)`
and `choose_level(prompt)` — and everything the pipeline guarantees holds on
either side of them, which is what makes the guarantees testable without a
model.

---

## Reading order

1. `docs/legal.md` — the constraints everything else follows from
2. `src/cis/consent/gate.py` and `src/cis/ingest/tracks.py` — the two refusals
3. `src/cis/extract/grounded.py` — why a claim without a quote does not exist
4. `docs/evaluation.md` — why a model number without a human number is noise
5. `docs/notes-on-the-spec.md` — where this diverges from the design document
