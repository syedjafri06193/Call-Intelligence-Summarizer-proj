"""Inter-rater agreement -- the ceiling (design.md sections 8.1, 8.2, 16.2).

    "MEDDIC scoring is subjective. Two experienced sales managers scoring the
    same call will disagree, and how much they disagree is the ceiling on what
    your model can be measured against."

This module exists before the scoring model does, which is the order section
8's milestone ladder insists on (M4 comes before M5). The reason is that
without the ceiling, a model result is uninterpretable: 0.36 on "champion"
looks like a broken model and is in fact a model performing at the limit of
what the criterion can support.

Krippendorff's alpha rather than Cohen's or Fleiss' kappa, for three reasons
that all matter here:

* It handles **missing ratings**. Raters skip calls -- section 16.2 says so
  explicitly -- and kappa variants either drop those units or need every rater
  on every unit.
* It handles **any number of raters**, including a different number per unit.
* It has an **ordinal** difference function. Anchor levels are ordinal:
  scoring 3 when the consensus is 0 is a worse disagreement than scoring 3
  when the consensus is 2, and a nominal statistic cannot see that difference.

The implementation follows Krippendorff's own coincidence-matrix formulation
and is checked against his published worked example in the tests, because an
agreement statistic that is subtly wrong is worse than none: it produces a
confident ceiling that is not the ceiling.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Iterable, Mapping, Sequence


class Metric(str, Enum):
    """The difference function.

    ORDINAL is the right one for anchor levels and the default everywhere in
    this codebase. The others are here because the reference values used to
    verify the implementation are published for all three, and a test that can
    only check one path checks less.
    """

    NOMINAL = "nominal"
    ORDINAL = "ordinal"
    INTERVAL = "interval"


#: Returned when agreement is undefined rather than zero. They are different
#: statements and collapsing them is how a report ends up claiming that raters
#: disagreed completely when in fact nobody rated anything twice.
UNDEFINED = float("nan")


@dataclass(frozen=True)
class CriterionAgreement:
    """Agreement on one criterion, and what it was computed from."""

    criterion_key: str
    alpha: float
    #: Units with at least two ratings. Everything else contributes nothing to
    #: alpha and is reported so a suspiciously high number can be traced to a
    #: suspiciously small n.
    units_used: int
    units_total: int
    raters: int
    ratings: int
    metric: Metric = Metric.ORDINAL

    @property
    def defined(self) -> bool:
        return not math.isnan(self.alpha)

    @property
    def reliable_enough_to_report(self) -> bool:
        """Krippendorff's own convention: 0.800 for conclusions, 0.667 as the
        lowest at which tentative conclusions are acceptable.

        Sales qualification criteria routinely fall below both. That is a fact
        about the criteria, and the point of section 8.2 is to publish it
        rather than to hide it behind a model accuracy number.
        """
        return self.defined and self.alpha >= 0.667

    def describe(self) -> str:
        if not self.defined:
            return f"{self.criterion_key}: undefined (n={self.units_used})"
        return (
            f"{self.criterion_key}: alpha={self.alpha:.3f} "
            f"(n={self.units_used}/{self.units_total} units, "
            f"{self.raters} raters)"
        )


@dataclass(frozen=True)
class AgreementReport:
    """Per criterion, never aggregated.

    Section 16.2: "Report it per criterion, not overall. The aggregate hides
    exactly the variation that should drive product decisions." A single
    framework-wide alpha averages a criterion humans agree about with one they
    do not, and the average is true of neither.
    """

    by_criterion: Mapping[str, CriterionAgreement]
    metric: Metric = Metric.ORDINAL

    def __getitem__(self, key: str) -> CriterionAgreement:
        return self.by_criterion[key]

    @property
    def weakest(self) -> CriterionAgreement | None:
        defined = [a for a in self.by_criterion.values() if a.defined]
        return min(defined, key=lambda a: a.alpha) if defined else None

    def unreliable(self) -> tuple[CriterionAgreement, ...]:
        """Criteria whose definitions need work before their models do."""
        return tuple(
            a
            for a in self.by_criterion.values()
            if a.defined and not a.reliable_enough_to_report
        )

    def table(self) -> str:
        rows = ["criterion             alpha    n   raters"]
        for key in sorted(self.by_criterion):
            a = self.by_criterion[key]
            alpha = "  n/a" if not a.defined else f"{a.alpha:.3f}"
            rows.append(f"{key:<20}  {alpha}  {a.units_used:>3}  {a.raters:>6}")
        return "\n".join(rows)


def krippendorff_alpha(
    ratings: Mapping[str, Mapping[str, int]],
    *,
    metric: Metric = Metric.ORDINAL,
) -> float:
    """Krippendorff's alpha over `ratings[item_id][rater_id] -> level`.

    Missing ratings are simply absent from the inner mapping, which is what
    makes this usable on real labelling data where raters skip units.

    Returns NaN when alpha is undefined: no unit was rated more than once, or
    every rating in the whole set is the same value. The second case is worth
    dwelling on -- if all three raters give every call a 0, they agree
    perfectly and alpha is undefined rather than 1.0, because there is no
    variation for chance agreement to be measured against. A report that
    printed 1.0 there would be claiming excellent reliability on a set of
    labels that carry no information.
    """
    # Only units with two or more ratings can contribute: agreement is a
    # statement about pairs.
    units = [
        list(per_rater.values())
        for per_rater in ratings.values()
        if len(per_rater) >= 2
    ]
    if not units:
        return UNDEFINED

    # Coincidence matrix. Each unit contributes every ordered pair of its
    # ratings, weighted by 1/(m_u - 1) so that a unit rated by ten raters does
    # not outweigh forty units rated by two.
    coincidence: dict[tuple[int, int], float] = defaultdict(float)
    for values in units:
        weight = 1.0 / (len(values) - 1)
        for i, c in enumerate(values):
            for j, k in enumerate(values):
                if i != j:
                    coincidence[(c, k)] += weight

    marginals: Counter[int] = Counter()
    for (c, _k), value in coincidence.items():
        marginals[c] += value
    total = sum(marginals.values())
    if total <= 1:
        return UNDEFINED

    levels = sorted(marginals)
    if len(levels) == 1:
        # No variation anywhere. See the docstring.
        return UNDEFINED

    delta = _difference(metric, marginals, levels)

    observed = 0.0
    for (c, k), value in coincidence.items():
        observed += value * delta(c, k)

    expected = 0.0
    for c in levels:
        for k in levels:
            pairs = marginals[c] * (marginals[k] - (1 if c == k else 0))
            expected += pairs * delta(c, k)
    expected /= total - 1

    if expected == 0:
        return UNDEFINED
    return 1.0 - observed / expected


def _difference(
    metric: Metric, marginals: Mapping[int, float], levels: Sequence[int]
) -> Callable[[int, int], float]:
    """The squared difference function for one metric."""
    if metric is Metric.NOMINAL:
        return lambda c, k: 0.0 if c == k else 1.0

    if metric is Metric.INTERVAL:
        return lambda c, k: float((c - k) ** 2)

    # Ordinal. The distance between two levels depends on how many ratings
    # fall between them, which is what makes it an ordinal rather than an
    # interval statistic: the levels are ordered, but the gaps between them
    # are not assumed equal.
    order = {level: i for i, level in enumerate(levels)}

    def ordinal(c: int, k: int) -> float:
        if c == k:
            return 0.0
        lo, hi = sorted((order[c], order[k]))
        between = sum(marginals[levels[g]] for g in range(lo, hi + 1))
        correction = (marginals[c] + marginals[k]) / 2.0
        return float((between - correction) ** 2)

    return ordinal


def rater_agreement(
    labels: Mapping[str, Mapping[str, Mapping[str, int]]],
    *,
    metric: Metric = Metric.ORDINAL,
) -> AgreementReport:
    """Per-criterion agreement.

    `labels[call_id][rater_id][criterion_key] -> anchor level`, which is the
    shape a labelling tool naturally produces: one rater scores one call
    against every criterion in a sitting.

    Section 16.2's signature takes `labels[call_id][rater_id] -> level`, for
    one criterion at a time. This takes all criteria at once and transposes,
    because the per-criterion split is not optional -- making the caller loop
    invites an aggregate.
    """
    per_criterion: dict[str, dict[str, dict[str, int]]] = defaultdict(
        lambda: defaultdict(dict)
    )
    raters: set[str] = set()

    for call_id, by_rater in labels.items():
        for rater_id, by_criterion in by_rater.items():
            raters.add(rater_id)
            for criterion_key, level in by_criterion.items():
                per_criterion[criterion_key][call_id][rater_id] = level

    report: dict[str, CriterionAgreement] = {}
    for criterion_key, ratings in per_criterion.items():
        used = sum(1 for per_rater in ratings.values() if len(per_rater) >= 2)
        report[criterion_key] = CriterionAgreement(
            criterion_key=criterion_key,
            alpha=krippendorff_alpha(ratings, metric=metric),
            units_used=used,
            units_total=len(ratings),
            raters=len({r for per_rater in ratings.values() for r in per_rater}),
            ratings=sum(len(per_rater) for per_rater in ratings.values()),
            metric=metric,
        )

    return AgreementReport(by_criterion=report, metric=metric)


# ---------------------------------------------------------------- consensus


class NoConsensus(ValueError):
    """The raters did not converge and nobody adjudicated.

    Section 8.1: "Establish a consensus label through adjudicated discussion,
    not majority vote." So this is raised rather than resolved -- a majority
    vote here would manufacture a label that no rater would defend, and the
    evaluation set would then measure the model against a number nobody
    believes.
    """


def consensus(
    per_rater: Mapping[str, int], *, adjudicated: int | None = None
) -> int:
    """The agreed level for one call and one criterion.

    Unanimity is consensus. Anything else needs an adjudicated value supplied
    by whoever ran the discussion. There is no majority-vote path.
    """
    if adjudicated is not None:
        return adjudicated
    values = set(per_rater.values())
    if len(values) == 1:
        return next(iter(values))
    raise NoConsensus(
        f"raters gave {sorted(per_rater.values())}; consensus requires "
        "adjudicated discussion, not a majority vote (section 8.1). Supply "
        "`adjudicated=` with the level the raters settled on."
    )


# ----------------------------------------------- model measured against it


@dataclass(frozen=True)
class CriterionResult:
    """One row of section 8.2's table."""

    criterion_key: str
    human_alpha: float
    model_alpha: float
    n: int

    @property
    def fraction_of_ceiling(self) -> float:
        """Model agreement as a fraction of human agreement.

        "The Champion row is the point. A raw 0.36 looks bad in isolation.
        Against a human ceiling of 0.38, the model is performing as well as a
        person."
        """
        if math.isnan(self.human_alpha) or self.human_alpha <= 0:
            return UNDEFINED
        return self.model_alpha / self.human_alpha

    @property
    def verdict(self) -> str:
        """What this row should make someone do.

        The distinction the table exists to draw: a model at the ceiling on an
        unreliable criterion needs a better *definition*, and no amount of
        model work will help.
        """
        if math.isnan(self.human_alpha):
            return "human agreement undefined -- label more calls"
        if math.isnan(self.model_alpha):
            # Usually the model agreed with the consensus on every call, so
            # there is no variation to measure. Calling that "the model
            # trails the ceiling" is the same mistake `krippendorff_alpha`
            # returns NaN to avoid.
            return "model agreement undefined -- no variation to measure"
        if self.human_alpha <= 0:
            # There is no ceiling to measure against. Raters disagreed at or
            # beyond chance, which means the criterion as written is not
            # measuring anything, and a ratio computed against it would be a
            # percentage of nothing.
            return "human agreement at or below chance -- the criterion is broken"
        if self.human_alpha < 0.667:
            if not math.isnan(self.fraction_of_ceiling) and self.fraction_of_ceiling >= 0.9:
                return "at the human ceiling; the criterion is what needs work"
            return "criterion is unreliable AND the model trails it"
        if not math.isnan(self.fraction_of_ceiling) and self.fraction_of_ceiling >= 0.9:
            return "at the human ceiling"
        return "below the ceiling; model has room"


@dataclass(frozen=True)
class ModelReport:
    rows: tuple[CriterionResult, ...]

    def table(self) -> str:
        """Section 8.2's table. Both columns, always.

        "Publishing both columns is the honest framing."
        """
        out = [
            f"{'criterion':<20} {'human a':>8} {'model a':>8} {'vs ceiling':>11}"
            "  verdict",
        ]
        for row in self.rows:
            ceiling = (
                "   n/a"
                if math.isnan(row.fraction_of_ceiling)
                else f"{row.fraction_of_ceiling * 100:.0f}%"
            )
            human = "   n/a" if math.isnan(row.human_alpha) else f"{row.human_alpha:.2f}"
            model = "   n/a" if math.isnan(row.model_alpha) else f"{row.model_alpha:.2f}"
            out.append(
                f"{row.criterion_key:<20} {human:>8} {model:>8} {ceiling:>11}"
                f"  {row.verdict}"
            )
        return "\n".join(out)

    def __getitem__(self, key: str) -> CriterionResult:
        for row in self.rows:
            if row.criterion_key == key:
                return row
        raise KeyError(key)


def measure_against_ceiling(
    human: AgreementReport,
    consensus_labels: Mapping[str, Mapping[str, int]],
    model_labels: Mapping[str, Mapping[str, int]],
    *,
    metric: Metric = Metric.ORDINAL,
) -> ModelReport:
    """Build section 8.2's table.

    `consensus_labels[call_id][criterion_key]` and `model_labels` likewise.
    The model's agreement with the consensus is computed with the same
    statistic as the humans' agreement with each other -- comparing an alpha
    against an accuracy would make the ratio meaningless, and the ratio is the
    entire point of the table.
    """
    criteria = sorted(
        {k for per_call in consensus_labels.values() for k in per_call}
    )
    rows: list[CriterionResult] = []

    for criterion_key in criteria:
        paired: dict[str, dict[str, int]] = {}
        for call_id, per_criterion in consensus_labels.items():
            if criterion_key not in per_criterion:
                continue
            model = model_labels.get(call_id, {}).get(criterion_key)
            if model is None:
                continue
            paired[call_id] = {
                "consensus": per_criterion[criterion_key],
                "model": model,
            }

        human_alpha = (
            human[criterion_key].alpha
            if criterion_key in human.by_criterion
            else UNDEFINED
        )
        rows.append(
            CriterionResult(
                criterion_key=criterion_key,
                human_alpha=human_alpha,
                model_alpha=krippendorff_alpha(paired, metric=metric),
                n=len(paired),
            )
        )

    return ModelReport(rows=tuple(rows))


def stratification_gaps(
    strata: Mapping[str, Iterable[str]], labelled: Iterable[str]
) -> Mapping[str, int]:
    """How many labelled calls each stratum has.

    Section 8.1 step 1: "Sample 100-200 calls, stratified across segment, deal
    stage, rep, and outcome." A set that is unstratified in practice -- forty
    calls from one rep -- produces a ceiling for that rep, and the failure is
    invisible unless something counts.
    """
    have = set(labelled)
    return {name: len(set(members) & have) for name, members in strata.items()}
