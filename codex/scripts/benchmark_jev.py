#!/usr/bin/env python3
"""Bounded, reproducible Jev batch benchmark using synthetic flight events.

Run from codex:
  uv run python scripts/benchmark_jev.py --env-file /absolute/path/credentials.env

Makes at most 15 small requests (3 sizes × 5 repeats). No messages are read from
Gmail/GBrain and no notifications are sent. Credentials are never included in
results. --dry-run previews the experiment without accessing the provider.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from memento.providers import JevEvaluator


STATE = {
    "today": "2026-09-27T14:40:00-07:00",
    "timezone": "America/Los_Angeles",
    "event": {
        "source": "email",
        "subject": "Synthetic fixture: schedule change UA123",
        "body": (
            "Your United flight UA123 on September 28, 2026 now departs "
            "SFO at 10:40 instead of 08:05. Booking DEMO123."
        ),
    },
}


def questions_for(size: int) -> dict[str, dict]:
    questions = {
        "target": {
            "kind": "noul",
            "question": (
                "Does the incoming event report a changed departure time "
                "for flight UA123, booking DEMO123?"
            ),
            "options": {
                "true": (
                    "Explicit new departure time instead of the old time. "
                    "The booking can remain confirmed while departure changes."
                ),
                "false": "Departure unchanged, unrelated flight, or no change stated.",
            },
        }
    }
    questions.update({
        f"distractor_{i}": {
            "kind": "noul",
            "question": (
                f"Does the incoming event report a changed departure time "
                f"for flight BA{1000 + i} on October 14, 2026?"
            ),
            "options": {
                "true": "The message explicitly reports a new departure for this flight.",
                "false": "The update is about another flight or no departure change is stated.",
            },
        }
        for i in range(size - 1)
    })
    return questions


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env-file", type=Path,
        help="Explicit credential file. Otherwise use existing environment only.",
    )
    parser.add_argument(
        "--sizes", nargs="+", type=int, choices=(1, 10, 200), default=[1, 10, 200],
    )
    parser.add_argument("--repeats", type=int, choices=range(1, 6), default=2)
    parser.add_argument("--model", default="jev-1.13.0")
    parser.add_argument("--json-out", type=Path, help="Also save the JSON report here.")
    parser.add_argument("--dry-run", action="store_true", help="No provider calls.")
    args = parser.parse_args(argv)
    args.sizes = list(dict.fromkeys(args.sizes))
    if args.env_file and not args.env_file.is_file():
        parser.error("--env-file must name an existing regular file")
    return args


async def benchmark(args: argparse.Namespace, api_key: str) -> dict:
    evaluator = JevEvaluator(api_key=api_key, model_name=args.model)
    runs = []
    for size in args.sizes:
        questions = questions_for(size)
        for repeat in range(args.repeats):
            batch = await evaluator.decide(STATE, questions)
            distractors = [
                answer.probability for key, answer in batch.answers.items()
                if key != "target"
            ]
            target = batch.answers["target"].probability
            runs.append({
                "questions": size, "repeat": repeat + 1,
                "model": batch.model, "elapsed_ms": batch.elapsed_ms,
                "input_tokens": batch.input_tokens,
                "output_tokens": batch.output_tokens,
                "target_probability": target,
                "largest_distractor_probability": max(distractors, default=None),
                "separates_at_0_8": target >= 0.8 and all(p < 0.8 for p in distractors),
            })
    return {
        "experiment": "memento-synthetic-flight-departure-batch-v2",
        "measured_at": datetime.now(timezone.utc).isoformat(),
        "requested_model": args.model,
        "request_count": len(runs),
        "runs": runs,
        "note": "Synthetic fixture; small sample, not an accuracy or p95 claim.",
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.dry_run:
        report = {
            "dry_run": True, "model": args.model, "sizes": args.sizes,
            "repeats": args.repeats,
            "request_count": len(args.sizes) * args.repeats,
            "synthetic_state": STATE,
        }
    else:
        if args.env_file:
            load_dotenv(args.env_file, override=False)
        api_key = os.getenv("JEV_API_KEY") or os.getenv("TYPESAFE_API_KEY")
        if not api_key:
            print("Set JEV_API_KEY or TYPESAFE_API_KEY, or pass --env-file.", file=sys.stderr)
            return 2
        try:
            report = asyncio.run(benchmark(args, api_key))
        except Exception as exc:
            # Provider exception text can include headers or request payloads.
            print(f"Benchmark failed ({type(exc).__name__}); no credentials logged.", file=sys.stderr)
            return 1
    encoded = json.dumps(report, indent=2) + "\n"
    if args.json_out:
        args.json_out.write_text(encoded)
    print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
