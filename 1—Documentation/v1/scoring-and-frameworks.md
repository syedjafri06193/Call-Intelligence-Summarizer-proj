# Scoring & Frameworks

Package: `cis.score` · Design: §7, §8.1 · Frameworks: [`v1/docs/frameworks/`](../v1/docs/frameworks/)

A call is scored against a **framework**: a versioned YAML file of criteria, each with a
definition, an evidence requirement and an ordinal **anchor** scale. The framework is data —
adding one needs no code change.

## How a score is produced

```
Extraction.claims ──► gather_evidence(criterion) ──► Evidence(items, excluded)
                                                        │
                       no items? ──► level 0 "not discussed"   (code; no model call)
                                                        │ items
                                                        ▼
                                  build_prompt (definition + anchors + evidence only)
                                                        │
                                  judge.choose_level ──► {"level": n, "rationale": "…"}
                                                        │ validated: integer, on scale, ≠ 0
                                                        ▼
                                                  CriterionScore
```

Three rules, each defeating a known failure:

| Rule | Defeats | Enforced by |
|---|---|---|
| **Score from extracted claims, not the transcript** | Verbosity bias (longer calls score higher) | `build_prompt` / `score_criterion` have no transcript parameter |
| **Ordinal anchors, not numbers** | Uncalibrated numeric scores | Judge picks a level; off-scale or non-integer → `InvalidAnchorLevel` |
| **Level 0 is code's job** | A model talking itself into evidence | No evidence → 0 without a model call; judge returning 0 with evidence → `InvalidAnchorLevel` |

The prompt also contains no prior score, suggested level or other rater's answer (§7.4 lists
these as things a judge anchors on). Judge temperature is fixed at `0.0`. Evidence quotes are
redacted on the way to the judge and the rationale is restored on the way back.

### Who said it

`gather_evidence` uses the transcript for one thing only: mapping each claim's earliest span to
the speaking participant and their role. For criteria with `prospect_stated_only: true`,
evidence from the rep (or an unknown speaker) is **excluded** with a reason — vendor-asserted
ROI is not the prospect's metric. Exclusions are kept on the score and shown to the rep.

### Evidence block (what the judge sees)

```
1. [prospect] Priya Raman, finance
     quote: "Priya Raman on the finance side signs off on anything over fifty thousand"
```

## Factual vs judgment

Every criterion has `kind`:

| Kind | Meaning | Product behaviour |
|---|---|---|
| `factual` | Human raters agree well | `CallScore.auto_writable` — may be written back automatically |
| `judgment` | Human raters disagree | `requires_review=True` → `CallScore.needs_review` — always goes to a human |

Set `kind` from **measured** inter-rater agreement ([evaluation-tooling.md](evaluation-tooling.md)),
not intuition. In the bundled labels, champion has α = 0.385 while economic buyer has 1.000.

## Results

### `CriterionScore`

`criterion_key`, `level`, `rationale`, `spans` (all evidence spans), `framework_id`,
`requires_review`, `judge_version`, `excluded` (notes on ruled-out evidence);
properties `has_evidence`, `quote`.

### `CallScore`

`call_id`, `framework_id` (e.g. `MEDDICC@3`), `transcript_version`, `extractor_version`,
`scores`, `judge_version`, `temperature`; properties:

| Property | Returns |
|---|---|
| `levels` | `{criterion_key: level}` |
| `needs_review` | Judgment-criterion scores |
| `auto_writable` | Factual-criterion scores |
| `coverage` | Fraction of criteria with any evidence |

Scores from different frameworks (or versions) are **not comparable**; every stored score names
the framework and version.

## The judge boundary

```python
class AnchorJudge(Protocol):
    def choose_level(self, prompt: str) -> dict: ...   # {"level": int, "rationale": str}
```

v1 ships only `ScriptedJudge` (level 3 if ≥ 2 quotes, else 2) — not a model. A live judge is
on the [roadmap](../v1/ROADMAP.md). Implementing one:

```python
class ClaudeJudge:
    def __init__(self, client, model):
        self.client, self.model = client, model
    def choose_level(self, prompt):
        msg = self.client.messages.create(
            model=self.model, max_tokens=200, temperature=0,
            messages=[{"role": "user", "content": prompt}],
            tools=[{"name": "choose_level", "description": "Pick one level.",
                    "input_schema": {"type": "object", "required": ["level", "rationale"],
                                     "properties": {"level": {"type": "integer"},
                                                    "rationale": {"type": "string"}}}}],
            tool_choice={"type": "tool", "name": "choose_level"})
        return next(b.input for b in msg.content if b.type == "tool_use")
```

(Illustrative.) Keep temperature 0, and measure rerun variance with `eval/consistency.py`.

## Bundled frameworks

| File | Id | Criteria |
|---|---|---|
| `meddicc.yaml` | `MEDDICC@3` | metrics (F), economic_buyer (F), decision_criteria (J), decision_process (F), identify_pain (J), champion (J), competition (F) |
| `bant.yaml` | `BANT@1` | budget (F), authority (F), need (J), timing (F) |

F = factual, J = judgment. Note BANT's "Authority" collapses economic buyer and champion —
exactly the conflation MEDDICC exists to undo.

## Writing your own framework

1. Copy [`bant.yaml`](../v1/docs/frameworks/bant.yaml) to e.g. `spiced.yaml`.
2. Edit criteria. A complete working example is in [`examples/spiced.yaml`](examples/spiced.yaml).
3. Validate: `python3 -m cis.cli framework --framework path/to/spiced.yaml`
4. Run: `python3 -m cis.cli demo --framework path/to/spiced.yaml`

### Schema

```yaml
version: 1                    # integer, required — bump on any definition change
name: SPICED                  # required; framework_id = "SPICED@1"
notes: >                      # optional
  Free text.

criteria:                     # non-empty list, unique keys
  - key: critical_event       # required
    label: Critical Event     # required
    kind: factual             # required: factual | judgment
    fields: [timeline]        # required, non-empty, each a valid extraction Field
    prospect_stated_only: false   # optional (default false)
    definition: >             # required
      A date or event after which the prospect suffers consequences.
    evidence_requires: >      # required
      a date together with what happens if it is missed
    anchors:                  # required: contiguous integers from 0, at least 2 levels
      0: not discussed        # level 0 MUST contain "not discussed" or "no evidence"
      1: urgency without a date
      2: a date with no stated consequence
      3: a date with a stated consequence
    warning: >                # optional; shown to reviewers
      Low human agreement on this one.
```

Valid `fields`: `economic_buyer`, `metric`, `budget`, `timeline`, `decision_process`,
`decision_criteria`, `pain`, `champion`, `competitor`, `commitment`, `next_step`, `objection`.

### Load-time errors (`FrameworkError`)

| Problem | Message (abridged) |
|---|---|
| Missing file / bad YAML | `no framework file at …` / `not valid YAML` |
| No `name`; non-integer `version` | `version must be an integer` (a string version sorts wrong) |
| Empty / duplicate criteria | `criteria must be a non-empty list` / `duplicate criterion key` |
| Missing `label` / `definition` / `evidence_requires` | `` `X` is required `` |
| Bad `kind` | `kind must be 'factual' or 'judgment'` |
| Empty or unknown `fields` | `… is not an extraction field. Known fields: …` |
| Gap in anchors | `anchor levels must run contiguously from 0` |
| Level 0 not absence | `anchor 0 must mean absence` |

The loader is strict on purpose: a gap makes the scale non-ordinal, and a level 0 that means
anything other than "no evidence" breaks the guarantee that code can assign it.

### Authoring tips

- Make each anchor **observable** ("a date named together with the event that fixes it"), not
  evaluative ("strong timeline").
- One step between adjacent anchors should feel like the same size of step.
- If raters disagree, fix the definition before blaming the model — the champion `warning` in
  MEDDICC says exactly this.
- If a criterion needs a field that isn't extracted (e.g. "use case"), that is a code change:
  add a `Field`, its `_FIELD_HELP` text, and tests.

## API

```python
from cis.score.framework import load_framework
from cis.score.anchors import score_call, gather_evidence, score_criterion, build_prompt

fw = load_framework("docs/frameworks/meddicc.yaml")
fw.framework_id        # "MEDDICC@3"
fw.keys, fw.factual, fw.judgment, fw.fields_used, fw.get("champion")

score = score_call(extraction, fw, judge, transcript, call, redactor=redactor)
```
