"""Command line entry point.

Four commands, each of which shows one of the things the design document
argues for and is hard to see from reading code:

    cis demo        the whole pipeline on the bundled sample call
    cis gate        the consent gate on its own, including what it refuses
    cis framework   a framework as loaded, anchors and all
    cis agreement   the ceiling, from a file of multi-rater labels

Everything runs offline.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from .consent.gate import may_process
from .crm.tasks import ProposedTask
from .samples import (
    DEFAULT_SAMPLE,
    SamplePlatform,
    SampleTranscriber,
    ScriptedJudge,
    ScriptedModel,
    load_sample,
)
from .score.framework import FrameworkError, load_framework
from .workflows.pipeline import Outcome, run

FRAMEWORKS = Path(__file__).resolve().parents[2] / "docs" / "frameworks"
DEFAULT_FRAMEWORK = FRAMEWORKS / "meddicc.yaml"

RULE = "-" * 72


def _heading(text: str) -> None:
    print(f"\n{text}\n{RULE}")


# ------------------------------------------------------------------- demo


def cmd_demo(args: argparse.Namespace) -> int:
    sample = load_sample(args.sample)
    framework = load_framework(args.framework)
    model = ScriptedModel(fabricate=not args.no_fabrication)
    judge = ScriptedJudge()

    result = run(
        sample.call,
        sample.ledger,
        SamplePlatform(sample),
        SampleTranscriber(sample),
        model,
        judge,
        framework,
        now=sample.call.scheduled_at,
        redact_before_model=not args.no_redaction,
        # Small chunks on a short sample, so the demo actually exercises
        # the overlap and the cross-chunk reconciliation rather than
        # sending the whole call in one pass.
        target_tokens=args.target_tokens,
        overlap_turns=2,
    )

    _heading(f"consent gate  ({sample.call.call_id})")
    decision = result.decision
    print(f"  allowed:      {decision.allowed}")
    print(f"  mode:         {decision.mode.value}")
    print(f"  consenting:   {', '.join(decision.consenting_participants) or '(none)'}")
    if decision.reason:
        print(f"  reason:       {decision.reason}")

    if result.outcome is not Outcome.COMPLETED:
        _heading("stopped")
        print(f"  stage:  {result.failed_stage.value if result.failed_stage else '?'}")
        print(f"  reason: {result.reason}")
        return 0 if result.outcome is Outcome.SKIPPED else 1

    _heading("transcript")
    print(f"  version:      {result.transcript.version}")
    print(f"  engine:       {result.transcript.asr_engine} / {result.transcript.asr_model}")
    print(f"  segments:     {len(result.transcript)}")
    print(f"  vocab hash:   {result.transcript.vocabulary_hash}")
    print(f"  content hash: {result.transcript.content_hash}")

    _heading("redaction (before the model saw anything)")
    print(f"  values redacted: {result.redactions}")
    print(
        "  note:            roster names are pseudonymised; a name that is not\n"
        "                   on the roster is not detected, and the demo's\n"
        "                   'Priya Raman' is exactly that case (docs/legal.md)."
    )
    if model.seen:
        first = model.seen[0].splitlines()
        print("  what the model received, first three lines:")
        for line in first[:3]:
            print(f"    {line[:66]}")

    _heading("extraction")
    for claim in result.extraction.claims:
        print(f"  {claim.field.value:<18} {claim.value}")
        print(f"  {'':<18} “{claim.quote[:60]}”  [{claim.earliest_ms // 1000}s]")
    stats = result.stats
    print(
        f"\n  quotes offered {stats.quotes_offered}, located {stats.quotes_located}, "
        f"hallucination rate {stats.hallucination_rate:.1%}"
    )
    for field, quote in stats.dropped_quotes:
        print(f"    discarded {field}: “{quote[:52]}”")

    if result.extraction.contradictions:
        _heading("contradictions (surfaced, not collapsed)")
        for contradiction in result.extraction.contradictions:
            print(f"  {contradiction.describe()}")

    _heading(f"score  ({result.score.framework_id})")
    for score in result.score.scores:
        criterion = framework[score.criterion_key]
        flag = "review" if score.requires_review else "auto"
        print(f"  {criterion.label:<20} {score.level}  [{flag}]  {score.rationale[:34]}")
        for note in score.excluded:
            print(f"  {'':<20}    excluded: {note[:50]}")
    print(f"\n  coverage: {result.score.coverage:.0%} of criteria have evidence")

    _heading("proposed tasks (every one awaiting confirmation)")
    if not result.tasks:
        print("  (none)")
    for task in result.tasks:
        _print_task(task)

    if result.warnings:
        _heading("warnings")
        for warning in result.warnings:
            print(f"  {warning}")

    return 0


def _print_task(task: ProposedTask) -> None:
    print(f"  {task.title}")
    print(f"     owner:   {task.owner_email or '(unresolved -- the rep assigns)'}")
    print(f"     due:     {task.due or '(none)'}{f'  [{task.due_note}]' if task.due_note else ''}")
    print(f"     confirm: {task.requires_confirmation}")
    print(f"     because: “{task.quote[:58]}”")


# ------------------------------------------------------------------- gate


def cmd_gate(args: argparse.Namespace) -> int:
    sample = load_sample(args.sample)
    ledger = sample.ledger

    if args.drop:
        kept = [r for r in ledger.all() if r.participant_id not in set(args.drop)]
        from .consent.model import ConsentLedger

        ledger = ConsentLedger(kept)

    decision = may_process(sample.call, ledger, now=sample.call.scheduled_at)
    _heading(f"gate  ({sample.call.call_id})")
    print(f"  allowed:     {decision.allowed}")
    print(f"  policy:      {decision.policy_version} / {decision.mode.value}")
    print(f"  roster:      {decision.roster_fingerprint}")
    if decision.reason:
        print(f"  reason:      {decision.reason}")
    if decision.remediation:
        print(f"  remediation: {decision.remediation}")
    for block in decision.blocks:
        print(f"    blocked: {block.participant_id} -- {block.blocker.value}")
    return 0 if decision.allowed else 2


# -------------------------------------------------------------- framework


def cmd_framework(args: argparse.Namespace) -> int:
    framework = load_framework(args.framework)
    _heading(f"{framework.framework_id}   ({framework.source_path})")
    for criterion in framework.criteria:
        print(f"\n  {criterion.label}  [{criterion.kind.value}]")
        print(f"    fields:   {', '.join(f.value for f in criterion.fields)}")
        print(f"    requires: {criterion.evidence_requires}")
        if criterion.prospect_stated_only:
            print("    note:     evidence from the rep does not count")
        for level in sorted(criterion.anchors):
            print(f"      {level}: {criterion.anchors[level]}")
        if criterion.warning:
            print(f"    WARNING:  {criterion.warning}")
    print(
        f"\n  auto-writable: {', '.join(c.key for c in framework.factual)}"
        f"\n  always review: {', '.join(c.key for c in framework.judgment)}"
    )
    return 0


# -------------------------------------------------------------- agreement


def cmd_agreement(args: argparse.Namespace) -> int:
    from eval.agreement import Metric, rater_agreement

    labels = json.loads(Path(args.labels).read_text(encoding="utf-8"))
    report = rater_agreement(labels, metric=Metric(args.metric))
    _heading(f"inter-rater agreement  ({args.metric})")
    print(report.table())
    unreliable = report.unreliable()
    if unreliable:
        print(
            "\n  below 0.667 -- fix the definition before the model:\n    "
            + "\n    ".join(a.describe() for a in unreliable)
        )
    return 0


# -------------------------------------------------------------------- cli


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cis", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    demo = sub.add_parser("demo", help="run the sample call end to end")
    demo.add_argument("--sample", default=str(DEFAULT_SAMPLE))
    demo.add_argument("--framework", default=str(DEFAULT_FRAMEWORK))
    demo.add_argument("--target-tokens", type=int, default=90)
    demo.add_argument(
        "--no-redaction",
        action="store_true",
        help="send the transcript to the model unredacted (self-hosted only)",
    )
    demo.add_argument(
        "--no-fabrication",
        action="store_true",
        help="stop the scripted model inventing a quote",
    )
    demo.set_defaults(func=cmd_demo)

    gate = sub.add_parser("gate", help="run the consent gate on its own")
    gate.add_argument("--sample", default=str(DEFAULT_SAMPLE))
    gate.add_argument(
        "--drop",
        nargs="*",
        default=[],
        metavar="PARTICIPANT_ID",
        help="remove a participant's consent record and watch the gate refuse",
    )
    gate.set_defaults(func=cmd_gate)

    framework = sub.add_parser("framework", help="print a framework as loaded")
    framework.add_argument("--framework", default=str(DEFAULT_FRAMEWORK))
    framework.set_defaults(func=cmd_framework)

    agreement = sub.add_parser("agreement", help="inter-rater agreement from a labels file")
    agreement.add_argument("labels", help="JSON: {call_id: {rater: {criterion: level}}}")
    agreement.add_argument(
        "--metric", default="ordinal", choices=["nominal", "ordinal", "interval"]
    )
    agreement.set_defaults(func=cmd_agreement)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except FrameworkError as exc:
        print(f"framework error: {exc}", file=sys.stderr)
        return 1
    except FileNotFoundError as exc:
        print(f"not found: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
