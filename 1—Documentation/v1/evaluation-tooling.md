# Evaluation Tooling

Package: `eval/` (inside `v1/`) · Methodology: [`v1/docs/evaluation.md`](evaluation.md) ·
Design: §8, §7.4

The methodology document explains **why**; this page documents the **code**. Three things are
measured separately and never averaged together:

| What | Ceiling / nature | Module |
|---|---|---|
| Scoring agreement | Capped by how well humans agree | `eval/agreement.py` |
| Rerun variance | A second ceiling under the first | `eval/consistency.py` |
| Extraction quality | Objective — no human-agreement cap | `eval/extraction_metrics.py` |

Order matters (§8, milestones M4 before M5): measure human agreement **before** building the
scoring model, or a model result is uninterpretable.

## `eval/agreement.py` — the human ceiling

Krippendorff's alpha, chosen because it handles missing ratings (raters skip calls), any
number of raters, and ordinal data. The implementation is verified against published values.

```python
from eval.agreement import rater_agreement, Metric

report = rater_agreement(labels, metric=Metric.ORDINAL)   # labels[call][rater][criterion] -> level
print(report.table())
report.by_criterion["champion"].alpha       # 0.385 on the bundled labels
report.weakest                              # lowest-alpha criterion
report.unreliable()                         # alpha < 0.667: fix the definition first
```

| Function / type | Purpose |
|---|---|
| `krippendorff_alpha(ratings, *, metric)` | `ratings[item][rater] -> level`; returns `UNDEFINED` (`nan`) when there is no variation — undefined is neither 0 nor 1 |
| `Metric` | `nominal`, `ordinal`, `interval` |
| `CriterionAgreement` | `alpha`, `units_used`, `units_total`, `raters`, `ratings`; `.defined`, `.reliable_enough_to_report` (≥ 0.800 conclusions, ≥ 0.667 tentative), `.describe()` |
| `AgreementReport` | Per criterion, **never aggregated**: `.weakest`, `.unreliable()`, `.table()` |
| `consensus(per_rater, *, adjudicated=None)` | Unanimous value, or the adjudicated one; otherwise `NoConsensus`. **No majority vote.** |
| `measure_against_ceiling(human, consensus_labels, model_labels)` | §8.2 table: model-vs-consensus alpha beside human alpha |
| `CriterionResult` | `human_alpha`, `model_alpha`, `n`; `.fraction_of_ceiling`, `.verdict` |
| `ModelReport.table()` | Both columns, always |
| `stratification_gaps(strata, labelled)` | Labelled-call count per stratum (segment, stage, rep, outcome) — catches "forty calls from one rep" |

### Verdicts

| Condition | `verdict` |
|---|---|
| human α undefined | "human agreement undefined — label more calls" |
| human α ≤ 0 | "criterion is broken" |
| human α < 0.667, model ≥ 90 % of ceiling | "at the human ceiling; the criterion is what needs work" |
| human α < 0.667, model below | "criterion is unreliable AND the model trails it" |
| human α ≥ 0.667, model ≥ 90 % | "at the human ceiling" |
| otherwise | "below the ceiling; model has room" |

## `eval/consistency.py` — rerun variance

```python
from eval.consistency import rerun_consistency, improvement_is_meaningful

report = rerun_consistency([run1, run2, run3])   # each: run[call][criterion] -> level
print(report.table())
report.noise_floor()                  # largest flip rate across criteria
improvement_is_meaningful(0.03, report, criterion_key="champion")   # |delta| > flip rate?
```

`CriterionConsistency`: `unanimous_fraction`, `modal_agreement`, `mean_stdev`, `max_spread`,
`calls`, `runs`; `.flip_rate` = share of calls not identical across runs. Calls missing from a
run are skipped, not counted as flips (a pipeline failure is a different defect). Needs ≥ 2 runs.

## `eval/extraction_metrics.py` — extraction, objectively

```python
from eval.extraction_metrics import GoldFact, SpanVerdict, evaluate_extraction, per_field

m = evaluate_extraction(
    predicted={"call_001": extraction.claims},
    gold=[GoldFact("call_001", Field.BUDGET, "$80k")],
    transcripts={"call_001": transcript},     # optional: re-validate every span
    span_verdicts=[SpanVerdict(...)],         # optional: rater yes/no per quote
    quotes_offered=stats.quotes_offered,
    quotes_located=stats.quotes_located,
)
print(m.report())
per_field(predicted, gold)    # same metrics split by Field
```

| Metric | Question | Usually fixed by |
|---|---|---|
| `precision` | Of claims made, how many match a gold fact? | Prompt |
| `recall` | Of gold facts, how many were found? | Chunking |
| `f1` | | |
| `span_accuracy` | Does the cited span actually support the claim? (from `SpanVerdict`s) | Prompt / field definitions |
| `hallucination_rate` | Quotes offered that aren't in the transcript | Model / prompt |

Matching is on `(call_id, field, normalize_value(field, value))` — the **same normaliser as
reconciliation**, so metrics measure the pipeline rather than a normaliser disagreement.
`GoldFact.ambiguous=True` marks facts raters weren't sure about. Span verification is a
yes/no task, far cheaper to label than full extraction, so you can label thousands.

## Redaction metrics

In `cis.redact.pii`, not `eval/`: `measure(text, gold, names=...)` → `RedactionMetrics`
with `recall`, `precision` and a per-kind `report()`. See [redaction.md](redaction.md).

## Building an evaluation set

From §8.1 and the roadmap:

1. Sample 100–200 calls (20–30 to start), stratified by segment, deal stage, rep and outcome.
   Check with `stratification_gaps`.
2. Have ≥ 3 raters score each call against the framework **independently**.
3. Compute `rater_agreement`. Criteria below 0.667 need definition work, not model work.
4. Adjudicate disagreements into consensus labels (`consensus(..., adjudicated=)`).
5. Label gold extraction facts and span verdicts.
6. Run the pipeline; report `measure_against_ceiling` and `evaluate_extraction`.
7. Run scoring ≥ 3 times; report `rerun_consistency` and gate claimed improvements with
   `improvement_is_meaningful`.
8. Promote CRM fields to `AUTO` only when measured precision ≥ 0.95.

Do not use real customer recordings in the repository. Use synthetic or fully anonymised calls.
