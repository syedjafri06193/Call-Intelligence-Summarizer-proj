"""Rerun variance (design.md section 7.4).

    "Measure your own self-consistency. Run the same 50 calls ten times and
    report the variance. If the model gives different scores on reruns, that
    number belongs in your evaluation report -- and it caps how much any
    accuracy improvement can mean."

The second clause is the one that gets skipped. Self-consistency is not
another quality metric to put in a table; it is a second ceiling, sitting
underneath the human-agreement ceiling. If the judge gives a different level
on 12% of reruns of identical input, then a change that moves accuracy by 3
points has not been shown to have done anything at all.

Temperature is fixed at 0 (score/anchors.py), which reduces this but does not
eliminate it: batching, hardware, and model updates all reintroduce variation,
so it is measured rather than assumed away.
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Mapping, Sequence


@dataclass(frozen=True)
class CriterionConsistency:
    criterion_key: str
    #: Fraction of calls where every rerun produced the same level.
    unanimous_fraction: float
    #: Mean fraction of reruns agreeing with the modal level, across calls.
    modal_agreement: float
    #: Mean standard deviation of the level across reruns, in anchor steps.
    mean_stdev: float
    #: The largest spread seen on any single call, in anchor steps. A criterion
    #: can look stable on average and still swing by two levels on one call,
    #: and that call is the one a rep will notice.
    max_spread: int
    calls: int
    runs: int

    @property
    def flip_rate(self) -> float:
        """Fraction of calls that did not produce the same level every time.

        The number section 7.4 says belongs in the report.
        """
        return 1.0 - self.unanimous_fraction

    def describe(self) -> str:
        return (
            f"{self.criterion_key}: {self.flip_rate:.1%} of calls changed level "
            f"across {self.runs} reruns "
            f"(modal agreement {self.modal_agreement:.2f}, "
            f"max spread {self.max_spread} level(s))"
        )


@dataclass(frozen=True)
class ConsistencyReport:
    by_criterion: Mapping[str, CriterionConsistency]
    runs: int

    def __getitem__(self, key: str) -> CriterionConsistency:
        return self.by_criterion[key]

    @property
    def worst(self) -> CriterionConsistency | None:
        if not self.by_criterion:
            return None
        return max(self.by_criterion.values(), key=lambda c: c.flip_rate)

    def noise_floor(self) -> float:
        """The largest flip rate across criteria.

        An accuracy improvement smaller than this has not been demonstrated.
        Stated as one number so it can be quoted next to a result rather than
        looked up afterwards.
        """
        if not self.by_criterion:
            return 0.0
        return max(c.flip_rate for c in self.by_criterion.values())

    def table(self) -> str:
        rows = [f"{'criterion':<20} {'flip rate':>10} {'modal':>7} {'max spread':>11}"]
        for key in sorted(self.by_criterion):
            c = self.by_criterion[key]
            rows.append(
                f"{key:<20} {c.flip_rate:>9.1%} {c.modal_agreement:>7.2f} "
                f"{c.max_spread:>11}"
            )
        rows.append("")
        rows.append(
            f"noise floor: {self.noise_floor():.1%}. An accuracy change smaller "
            "than this is not a result."
        )
        return "\n".join(rows)


def rerun_consistency(
    runs: Sequence[Mapping[str, Mapping[str, int]]],
) -> ConsistencyReport:
    """Compare repeated scoring runs over the same calls.

    `runs[i][call_id][criterion_key] -> level`, one mapping per rerun of an
    identical input. Calls missing from a run are skipped for that criterion
    rather than treated as a change, because a pipeline failure is a different
    defect and counting it here would hide it inside a consistency number.
    """
    if len(runs) < 2:
        raise ValueError(
            "consistency needs at least two runs of the same input; "
            f"got {len(runs)}"
        )

    levels: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    for run in runs:
        for call_id, per_criterion in run.items():
            for criterion_key, level in per_criterion.items():
                levels[criterion_key][call_id].append(level)

    report: dict[str, CriterionConsistency] = {}
    for criterion_key, per_call in levels.items():
        comparable = {
            call_id: values for call_id, values in per_call.items() if len(values) >= 2
        }
        if not comparable:
            continue

        unanimous = sum(1 for v in comparable.values() if len(set(v)) == 1)
        modal = [Counter(v).most_common(1)[0][1] / len(v) for v in comparable.values()]
        stdevs = [statistics.pstdev(v) for v in comparable.values()]
        spreads = [max(v) - min(v) for v in comparable.values()]

        report[criterion_key] = CriterionConsistency(
            criterion_key=criterion_key,
            unanimous_fraction=unanimous / len(comparable),
            modal_agreement=statistics.fmean(modal),
            mean_stdev=statistics.fmean(stdevs),
            max_spread=max(spreads),
            calls=len(comparable),
            runs=len(runs),
        )

    return ConsistencyReport(by_criterion=report, runs=len(runs))


def improvement_is_meaningful(
    delta: float, report: ConsistencyReport, *, criterion_key: str | None = None
) -> bool:
    """Is a measured accuracy change bigger than the rerun noise?

    Blunt on purpose. A 2-point gain on a criterion that flips on 12% of
    reruns is not a gain, and the honest thing is for that judgement to be a
    function rather than a paragraph in a report nobody reads.
    """
    if criterion_key is not None and criterion_key in report.by_criterion:
        floor = report[criterion_key].flip_rate
    else:
        floor = report.noise_floor()
    return abs(delta) > floor
