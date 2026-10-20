#!/usr/bin/env python3
"""Benchmark semantic decisions with configurable HTTP models and labelled cases."""

from __future__ import annotations

import argparse
import hashlib
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from decision_benchmark.config import DEFAULT_CASES, load_cases, load_models
from decision_benchmark.reporting import summary_lines
from decision_benchmark.runner import run_benchmark
from decision_benchmark.tasks import TASKS


def _positive(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


def _nonnegative(value: str) -> int:
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("must be at least 0")
    return number


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", type=Path, help="JSON model endpoints and authentication environment names"
    )
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--models", nargs="+", help="Only these model IDs from config")
    parser.add_argument("--tasks", nargs="+", choices=TASKS)
    parser.add_argument("--languages", nargs="+", help="Only these case languages, e.g. en pt")
    parser.add_argument("--repetitions", type=_positive, default=3)
    parser.add_argument(
        "--warmups",
        type=_nonnegative,
        default=1,
        help="Unscored requests per model per task (default 1)",
    )
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument(
        "--output-dir", type=Path, help="New directory; existing directories are never overwritten"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and show request counts; no network or credentials needed",
    )
    parser.add_argument(
        "--list-cases",
        action="store_true",
        help="List case IDs and labels without network requests",
    )
    parser.add_argument(
        "--fail-under",
        type=float,
        help="Exit 1 if any model's overall exact accuracy is below this percentage",
    )
    args = parser.parse_args(argv)
    if args.fail_under is not None and not 0 <= args.fail_under <= 100:
        parser.error("--fail-under must be between 0 and 100")
    try:
        cases = load_cases(args.cases)
        if args.tasks:
            cases = [case for case in cases if case.task in args.tasks]
        if args.languages:
            available = {case.language for case in cases}
            if set(args.languages) - available:
                raise ValueError("Unknown language selection")
            cases = [case for case in cases if case.language in args.languages]
        if not cases:
            raise ValueError("No cases selected")
        if args.list_cases:
            for case in cases:
                print(f"{case.id:<36} {case.task:<25} {case.language} {case.expected}")
            return 0
        if not args.config:
            parser.error("--config is required unless using --list-cases")
        models = load_models(args.config)
        if args.models:
            if set(args.models) - {model.id for model in models}:
                raise ValueError("Unknown model selection")
            models = [model for model in models if model.id in args.models]
        print(f"Cases: {len(cases)} {dict(Counter(case.task for case in cases))}")
        print(f"Models: {', '.join(model.id for model in models)}")
        total = len(cases) * len(models) * args.repetitions
        warms = len({case.task for case in cases}) * len(models) * args.warmups
        print(f"Requests: {total} measured + {warms} warm-up; sequential, no retries/fallbacks")
        if args.dry_run:
            return 0
        output = args.output_dir or Path(__file__).resolve().parents[
            1
        ] / "storage" / "decision-benchmarks" / datetime.now(timezone.utc).strftime(
            "%Y%m%dT%H%M%S%fZ"
        )

        def progress(row, index, count):
            status = row["error"] or ("PASS" if row["passed"] else "FAIL")
            print(
                f"[{index}/{count}] {row['phase']} {row['model_id']} {row['case_id']}: {status} {row['duration_ms']:.1f}ms",
                flush=True,
            )

        report = run_benchmark(
            cases=cases,
            models=models,
            output=output,
            repetitions=args.repetitions,
            warmups=args.warmups,
            seed=args.seed,
            suite_hash=hashlib.sha256(args.cases.read_bytes()).hexdigest(),
            config_hash=hashlib.sha256(args.config.read_bytes()).hexdigest(),
            progress=progress,
        )
        print("\n" + "\n".join(summary_lines(report["summaries"])))
        print(f"\nReports: {output.resolve()}")
        if report["interrupted"]:
            return 130
        summaries = [group["overall"] for group in report["summaries"].values()]
        if any(stats["errors"] for stats in summaries):
            return 1
        if args.fail_under is not None and any(
            100 * stats["accuracy"] < args.fail_under for stats in summaries
        ):
            return 1
        return 0
    except (ValueError, OSError) as exc:
        # Configuration exceptions are controlled messages; HTTP errors never reach this path.
        if isinstance(exc, OSError):
            print(
                "Cannot create/read benchmark files; choose a new writable output directory.",
                file=sys.stderr,
            )
        else:
            print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
