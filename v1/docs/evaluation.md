# Evaluation

The rule this document exists to enforce: **measure human agreement before
measuring the model, and publish both numbers.**

A model accuracy figure on a subjective criterion is uninterpretable on its
own. 0.36 looks like failure; against a human ceiling of 0.38 it is a model
performing as well as a person, and the thing that needs fixing is the
criterion's definition, not the model.

---

## 1. The three things measured, separately

| Layer | Question | Ceiling | Where |
|---|---|---|---|
| **ASR** | Did the transcript say what was said? | None — it is objective | §5 below |
| **Extraction** | Is the claim in the transcript, and does the cited span support it? | None — it is objective | `eval/extraction_metrics.py` |
| **Scoring** | Is the anchor level right? | Human agreement | `eval/agreement.py` |

They are never combined into one number. Extraction errors are checkable and
fixable; scoring disagreements are often legitimate. A combined score cannot
tell you which one broke, which is the only thing the number would have been
for.

---

## 2. Building the evaluation set

1. **Sample 100–200 calls**, stratified across segment, deal stage, rep, and
   outcome. `eval.agreement.stratification_gaps` counts what each stratum
   actually has, because forty calls from one rep produces a ceiling for that
   rep and the failure is invisible unless something counts.
2. **Three raters score each call independently**, from the same written
   anchors in `docs/frameworks/`.
3. **Compute agreement per criterion.** Never an aggregate: the aggregate
   hides exactly the variation that should drive product decisions.
4. **Establish consensus by adjudicated discussion.** `eval.agreement.consensus`
   accepts unanimity or an adjudicated value and **raises `NoConsensus`
   otherwise**. There is no majority-vote path, because a majority vote
   manufactures a label no rater would defend and then measures the model
   against it.

---

## 3. Krippendorff's alpha, and why

Alpha rather than Cohen's or Fleiss' kappa, for three reasons that all bite
here:

* **Missing ratings.** Raters skip calls. Kappa variants either drop those
  units or require every rater on every unit.
* **Any number of raters**, varying per unit.
* **An ordinal difference function.** Anchor levels are ordinal: scoring 3
  against a consensus of 0 is a worse disagreement than scoring 3 against 2,
  and a nominal statistic cannot see the difference. `Metric.ORDINAL` is the
  default throughout this codebase.

### 3.1 The implementation is verified against published values

An agreement statistic that is subtly wrong does not fail loudly. It produces
a confident ceiling that is not the ceiling, and every model result afterwards
is measured against a number nobody checked.

`tests/test_evaluation.py::TestAlphaAgainstPublishedValues` reproduces
Krippendorff's own worked example — 15 units, 3 observers, missing ratings
throughout — on all three difference functions:

| Metric | Published | This implementation |
|---|---|---|
| Nominal | 0.691 | 0.6914 |
| Ordinal | 0.807 | 0.8067 |
| Interval | 0.811 | 0.8108 |

### 3.2 Undefined is not zero, and not one

Two cases return `NaN` rather than a number:

* **No unit was rated twice.** Nothing about agreement has been demonstrated.
* **Every rating in the set is the same value.** Three raters who give every
  call a 0 agree perfectly and have shown nothing — there is no variation for
  chance agreement to be measured against. Reporting 1.0 would claim excellent
  reliability for labels that carry no information.

Alpha **below zero** is a real state and is preserved: raters disagreeing more
than chance usually means they read the anchors differently, which is a
fixable problem with the anchors.

---

## 4. Reporting against the ceiling

`eval.agreement.measure_against_ceiling` produces the table. Both columns,
always.

```
criterion             human a  model a   vs ceiling  verdict
metrics                  0.71     0.68          96%  at the human ceiling
economic_buyer           0.79     0.74          94%  at the human ceiling
decision_criteria        0.52     0.49          94%  at the human ceiling; the criterion is what needs work
champion                 0.38     0.36          95%  at the human ceiling; the criterion is what needs work
```

The champion row is the point of the whole table. `docs/frameworks/meddicc.yaml`
carries a `warning:` on that criterion saying so, where someone editing the
definition will read it.

The model's agreement with the consensus is computed with **the same
statistic** the humans' agreement with each other is computed with. Comparing
an alpha against an accuracy would make the ratio meaningless, and the ratio
is the entire point.

### 4.1 What the distribution should do to the product

Agreement is decent on factual criteria and poor on judgment ones. That
distribution is encoded, not just observed: every criterion in a framework
file declares `kind: factual | judgment`, and

* **factual** criteria may be written to the CRM automatically, once their
  measured precision clears the floor;
* **judgment** criteria always go to a human.

`CriterionScore.requires_review` is set from that field, and
`tests/test_scoring.py` asserts the split reaches the pipeline output.

---

## 5. The second ceiling: rerun variance

`eval/consistency.py`. Temperature is pinned at 0 in `score/anchors.py`, which
reduces self-inconsistency without eliminating it — batching, hardware, and
model updates all reintroduce variation.

Run the same 50 calls ten times. The **flip rate** — the fraction of calls
whose level changed across reruns — is a floor underneath every accuracy
comparison:

```
noise floor: 12.0%. An accuracy change smaller than this is not a result.
```

`improvement_is_meaningful(delta, report)` is that sentence as a function,
because a paragraph in a report gets skipped and a boolean does not.

---

## 6. Extraction, measured objectively

`eval/extraction_metrics.py`. Four numbers, never averaged into one:

| Metric | Question | What a bad number means |
|---|---|---|
| **Precision** | Of claims made, how many are real? | Prompt problem |
| **Recall** | Of facts present, how many were found? | Usually a chunking problem |
| **Span accuracy** | Does the cited span support the claim? | Matching problem |
| **Hallucination rate** | Quotes not in the transcript | Model problem |

Span verification is the cheap label: a rater reads a quote and a claim and
answers yes or no. Thousands of those can be collected quickly, which is why
extraction can be measured far more precisely than scoring can.

**Span accuracy is not precision.** A claim can be correct and cite the wrong
line. That is a real defect even with the value right: the rep clicks the
quote, hears something unrelated, and stops trusting every quote in the
product.

The hallucination rate also counts spans that *stopped* matching — a claim
extracted against transcript v1 whose text is gone in v2. To a rep looking at
the screen, a quote that was never there and a quote that is no longer there
are the same thing.

`per_field()` splits all of it by field, because a pipeline that finds every
competitor and no economic buyer has a respectable overall F1 and one badly
broken field.

### 6.1 The monitor in production

`extract.grounded` logs a warning on every discarded quote. That log line is
the hallucination monitor. Track its rate over time: a rise after a model or
prompt change is a regression signal that is otherwise invisible, because the
output still looks fine — there are simply fewer claims in it.

---

## 7. ASR, measured separately

Everything downstream is bounded by the transcript. Keep a small gold set of
hand-corrected transcripts and track:

* Overall WER.
* **Proper-noun error rate** — the one that matters. A transcript that gets
  92% of words right and mangles the prospect's company name is failing at the
  job, and no amount of grounding fixes it: the span will faithfully quote the
  wrong name.
* **WER by audio source.** Zoom, dialer, and bridge differ a lot. 8 kHz
  narrowband is the worst, and `ingest.tracks` emits a warning naming it so
  the difference is visible per call rather than buried in an average.
* Speaker attribution accuracy where per-speaker tracks are unavailable.

Published WER figures are measured on clean read speech and do not transfer.
Realistic expectations: 10–15% on per-speaker sales call audio, 15–25% on
mixed audio with cross-talk, 20–30% on 8 kHz telephony.

---

## 8. Running it

```bash
make demo                                   # the pipeline, end to end, offline
cis agreement eval/labeled/sample_labels.json
```

`eval/labeled/sample_labels.json` is ten calls scored by three raters. It
reproduces the champion row above: α = 0.385 on champion, 1.000 on economic
buyer — a worked example of the distribution §4.1 describes, small enough to
read.
