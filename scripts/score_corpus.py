#!/usr/bin/env python3
"""Score the agent against tests/fixtures/command_corpus.yaml.

Each utterance goes through the real router and policy engine (nothing is executed) and is
scored as correct, a false accept (an action it shouldn't have caused — any action for speech
that should be ignored, or the wrong command or duration), or a false reject (nothing
happened when it should have).

Local only by default, with no network: exact phrases must resolve and paraphrases are
expected to miss. `--cloud` sends local misses to Groq as the app would, which measures the
paraphrases properly; it needs GROQ_API_KEY and uses quota, paced to the configured rate.

Usage:
    python scripts/score_corpus.py [--cloud] [--show-misses]

Exits 1 if anything is a false accept.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import yaml  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from voice_agent.core.models import (  # noqa: E402
    NO_ACTION_INTENT,
    Decision,
    IntentProposal,
    IntentSource,
)

CORPUS = ROOT / "tests" / "fixtures" / "command_corpus.yaml"
#: Categories where the right outcome is that nothing happens.
SILENT = ("ambiguous", "out_of_scope", "adversarial", "background")


class Case(BaseModel):
    category: str
    say: str
    expect: str | None = None
    seconds: int | None = None


@dataclass(frozen=True)
class Result:
    case: Case
    verdict: str  # correct | false_accept | false_reject
    got: str


@dataclass(frozen=True)
class Summary:
    total: int
    accuracy: float
    false_accept_rate: float  # of all utterances
    false_reject_rate: float  # of the utterances that should have acted


def load_corpus(path: Path = CORPUS) -> list[Case]:
    """Bare strings are 'nothing should happen'; mappings name the expected command."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [
        Case(category=category, **({"say": entry} if isinstance(entry, str) else entry))
        for category, entries in data.items()
        for entry in entries
    ]


def score(agent: Any, cases: Iterable[Case], pace_s: float = 0.0) -> list[Result]:
    """Resolve and decide each utterance as the agent would; `pace_s` spaces cloud calls."""
    results = []
    for case in cases:
        proposal = agent.router.resolve(case.say) or IntentProposal(
            intent=NO_ACTION_INTENT, raw=case.say
        )
        if pace_s and proposal.source is IntentSource.LLM:
            time.sleep(pace_s)
        verdict = agent.policy.decide(proposal)
        if verdict.decision is Decision.DENY:
            outcome = "correct" if case.expect is None else "false_reject"
            results.append(Result(case, outcome, f"nothing ({verdict.reason})"))
            continue
        got = proposal.intent + ("" if verdict.argument is None else f" {verdict.argument}s")
        right = proposal.intent == case.expect and case.seconds in (None, verdict.argument)
        results.append(Result(case, "correct" if right else "false_accept", got))
    return results


def _summary(results: Sequence[Result]) -> Summary:
    should_act = [r for r in results if r.case.expect is not None]
    return Summary(
        total=len(results),
        accuracy=sum(r.verdict == "correct" for r in results) / len(results),
        false_accept_rate=sum(r.verdict == "false_accept" for r in results) / len(results),
        false_reject_rate=(
            sum(r.verdict == "false_reject" for r in should_act) / len(should_act)
            if should_act
            else 0.0
        ),
    )


def summarize(results: Sequence[Result]) -> dict[str, Summary]:
    """Per category, plus 'all'."""
    categories = dict.fromkeys(r.case.category for r in results)
    summary = {c: _summary([r for r in results if r.case.category == c]) for c in categories}
    summary["all"] = _summary(results)
    return summary


def report(results: Sequence[Result], show_misses: bool = False) -> str:
    lines = [f"{'category':<14}{'n':>5}{'accuracy':>10}{'false acc':>11}{'false rej':>11}"]
    for name, s in summarize(results).items():
        lines.append(
            f"{name:<14}{s.total:>5}{s.accuracy:>10.1%}"
            f"{s.false_accept_rate:>11.1%}{s.false_reject_rate:>11.1%}"
        )
    if show_misses:
        lines += ["", "misses:"]
        lines += [
            f"  {r.verdict:<13}{r.case.category:<14}{r.case.say!r} -> {r.got}"
            f" (wanted {r.case.expect or 'nothing'})"
            for r in results
            if r.verdict != "correct"
        ]
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score the agent against the command corpus.")
    parser.add_argument("--cloud", action="store_true", help="send local misses to Groq")
    parser.add_argument("--show-misses", action="store_true", help="list every miss")
    args = parser.parse_args(argv)

    from voice_agent.bootstrap import build_agent
    from voice_agent.main import Simulator
    from voice_agent.service import cloud_transport
    from voice_agent.settings.loader import load_settings

    settings = load_settings()
    llm = None
    if args.cloud:
        llm = cloud_transport(settings, notify=lambda note: print(note, file=sys.stderr))
        if llm is None:
            return 2
    simulator = Simulator()
    with tempfile.TemporaryDirectory() as tmp:
        agent = build_agent(
            settings, keys=simulator, windows=simulator, llm=llm, audit_path=Path(tmp) / "a.jsonl"
        )
        pace = 60 / settings.llm.max_calls_per_minute if args.cloud else 0.0
        results = score(agent, load_corpus(), pace)
    print(f"{'cloud' if args.cloud else 'local only'}: {len(results)} utterances")
    print(report(results, args.show_misses))
    return 1 if any(r.verdict == "false_accept" for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
