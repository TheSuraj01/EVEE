#!/usr/bin/env python3
"""Latency of the local command path, per stage, over every utterance in the command corpus.

Stages, as the voice loop runs them once a transcript exists:
  resolve   normalise + alias match (the cloud is off: this is the no-network path)
  policy    the policy decision on its own
  dispatch  policy gate, dedupe, executor, audit-log write
  total     resolve + dispatch: transcript to action

Executors are simulated, so this times the agent's own code, not Windows. Dedupe is off so
every repeat really executes. Speech-to-text is timed by benchmark_stt.py.

Usage:
    python scripts/benchmark_intents.py [--repeat 20] [--budget-ms 400]

Exits 1 if total p95 is over budget; CI runs the same check in
tests/integration/test_benchmarks.py.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import tempfile
import time
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

from scripts.score_corpus import load_corpus  # noqa: E402

from voice_agent.core.models import NO_ACTION_INTENT, IntentProposal  # noqa: E402

BUDGET_MS = 400.0


def benchmark_agent(audit_path: Path) -> Any:
    """The shipped agent over simulated backends, with dedupe off and no cloud."""
    from voice_agent.bootstrap import build_agent
    from voice_agent.main import Simulator
    from voice_agent.settings.loader import load_settings

    settings = load_settings(env={"VOICE_AGENT__NLU__DEDUPE_WINDOW_MS": "0"})
    simulator = Simulator()
    return build_agent(settings, keys=simulator, windows=simulator, audit_path=audit_path)


def time_path(agent: Any, transcript: str) -> dict[str, float]:
    """One transcript through the local path, as Agent.handle runs it; ms per stage."""
    t0 = time.perf_counter()
    proposal = agent.router.resolve(transcript) or IntentProposal(
        intent=NO_ACTION_INTENT, raw=transcript
    )
    t1 = time.perf_counter()
    agent.policy.decide(proposal)
    t2 = time.perf_counter()
    agent.dispatcher.dispatch(proposal)
    t3 = time.perf_counter()
    resolve, dispatch = (t1 - t0) * 1000, (t3 - t2) * 1000
    return {
        "resolve": resolve,
        "policy": (t2 - t1) * 1000,
        "dispatch": dispatch,
        "total": resolve + dispatch,
    }


def measure(agent: Any, transcripts: Iterable[str], repeat: int) -> dict[str, list[float]]:
    samples: dict[str, list[float]] = {}
    transcripts = list(transcripts)
    for _ in range(repeat):
        for transcript in transcripts:
            for stage, value in time_path(agent, transcript).items():
                samples.setdefault(stage, []).append(value)
    return samples


def percentiles(samples: Sequence[float]) -> dict[str, float]:
    if len(samples) < 2:  # statistics.quantiles needs two points
        return dict.fromkeys(("p50", "p95", "p99", "max"), samples[0])
    cuts = statistics.quantiles(samples, n=100, method="inclusive")
    return {"p50": cuts[49], "p95": cuts[94], "p99": cuts[98], "max": max(samples)}


def table(samples: Mapping[str, Sequence[float]]) -> str:
    lines = [f"{'stage':<14}{'n':>7}{'p50':>10}{'p95':>10}{'p99':>10}{'max':>10}   ms"]
    for stage, values in samples.items():
        stats = percentiles(values)
        lines.append(
            f"{stage:<14}{len(values):>7}"
            + "".join(f"{stats[key]:>10.2f}" for key in ("p50", "p95", "p99", "max"))
        )
    return "\n".join(lines)


def verdict(samples: Mapping[str, Sequence[float]], stage: str, budget_ms: float) -> int:
    """Print the budget line; 0 within budget, 1 over."""
    p95 = percentiles(samples[stage])["p95"]
    within = p95 <= budget_ms
    status = "within" if within else "OVER"
    print(f"\n{stage} p95 {p95:.2f} ms: {status} the {budget_ms:.0f} ms budget")
    return 0 if within else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Time the local command path per stage.")
    parser.add_argument("--repeat", type=int, default=20, help="passes over the corpus")
    parser.add_argument("--budget-ms", type=float, default=BUDGET_MS, help="p95 budget")
    args = parser.parse_args(argv)

    transcripts = [case.say for case in load_corpus()]
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        samples = measure(benchmark_agent(Path(tmp) / "audit.jsonl"), transcripts, args.repeat)
    print(table(samples))
    return verdict(samples, "total", args.budget_ms)


if __name__ == "__main__":
    sys.exit(main())
