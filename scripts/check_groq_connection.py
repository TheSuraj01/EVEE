#!/usr/bin/env python3
"""Verify the Groq connection and the configured model ID.

This exists because a wrong model ID fails *quietly* in this architecture: the router
degrades to local-only and the user just notices that paraphrases stopped working. Run this
before trusting the cloud path, and whenever Groq announces a deprecation.

Usage:
    python scripts/check_groq_connection.py --list
    python scripts/check_groq_connection.py            # verify configured model + round trip
    python scripts/check_groq_connection.py --intent "go to the tab on the right" --repeat 20
        # the exact production request (full prompt + strict schema), with p50/p95
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def _safe(exc: BaseException) -> str:
    """Exception text with anything key-shaped masked: an SDK error must not print a key."""
    from voice_agent.observability.logging import redact

    return redact(str(exc))


def _configured_model() -> str:
    env = os.environ.get("GROQ_MODEL")
    if env:
        return env
    import yaml

    data = yaml.safe_load((ROOT / "config" / "default.yaml").read_text(encoding="utf-8"))
    return str(data["llm"]["model"])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--list", action="store_true", help="list every available model")
    parser.add_argument(
        "--intent", metavar="PHRASE", help="send PHRASE through the real intent provider"
    )
    parser.add_argument("--repeat", type=int, default=1, help="calls for latency stats")
    args = parser.parse_args()

    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
    except ImportError:
        pass

    if not os.environ.get("GROQ_API_KEY"):
        print("FAIL: GROQ_API_KEY is not set. Copy .env.example to .env and fill it in.")
        return 2

    try:
        from groq import Groq
    except ImportError:
        print("FAIL: the 'groq' package is missing. Run: pip install -e \".[cloud]\"")
        return 2

    client = Groq()

    try:
        catalogue = sorted(m.id for m in client.models.list().data)
    except Exception as exc:
        print(f"FAIL: could not reach the Groq API: {type(exc).__name__}: {_safe(exc)}")
        return 2

    if args.list:
        print(f"{len(catalogue)} models available:\n")
        for model_id in catalogue:
            print(f"  {model_id}")
        return 0

    configured = _configured_model()
    print(f"Configured model: {configured}")

    if configured not in catalogue:
        print("\nFAIL: that model ID is NOT in the catalogue.")
        candidates = [m for m in catalogue if "qwen" in m.lower()]
        if candidates:
            print("Qwen models that ARE available:")
            for c in candidates:
                print(f"  {c}")
        print("\nUpdate llm.model in config/default.yaml, then record the outcome in")
        print("docs/adr/0002-groq-model-id.md (task T-028).")
        return 1

    if args.intent:
        return _intent_round_trip(args.intent, max(1, args.repeat), configured)

    print("Model ID found in the catalogue. Testing a strict-JSON round trip...")
    schema = {
        "type": "json_schema",
        "json_schema": {
            "name": "intent_proposal",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {"intent": {"type": "string", "enum": ["browser.next_tab"]}},
                "required": ["intent"],
                "additionalProperties": False,
            },
        },
    }
    started = time.monotonic()
    try:
        response = client.chat.completions.create(
            model=configured,
            max_tokens=64,
            temperature=0.0,
            messages=[
                {"role": "system", "content": "Return JSON only."},
                {"role": "user", "content": "go to the next tab"},
            ],
            response_format=schema,
        )
    except Exception as exc:
        print(f"\nFAIL: strict structured output rejected: {type(exc).__name__}: {_safe(exc)}")
        print("Fall back to response_format={'type': 'json_object'} — see ADR 0002.")
        return 1

    elapsed_ms = (time.monotonic() - started) * 1000
    print(f"OK: {response.choices[0].message.content!r} in {elapsed_ms:.0f} ms")
    if elapsed_ms > 1500:
        print("WARNING: that round trip is slow for an interactive command path.")
    print("\nRecord this result in docs/adr/0002-groq-model-id.md and tick T-028.")
    return 0


def _intent_round_trip(phrase: str, repeat: int, model: str) -> int:
    """Production path: registry-derived enum, prompt V2, 4-field strict schema, our retries
    off so each sample is one HTTP call."""
    import statistics

    from voice_agent.core.exceptions import LLMUnavailableError
    from voice_agent.engine.catalog import load_command_specs
    from voice_agent.engine.command_registry import CommandRegistry
    from voice_agent.llm.groq_qwen_provider import GroqQwenIntentProvider, GroqTransport
    from voice_agent.llm.prompts import PROMPT_VERSION

    registry = CommandRegistry()
    registry.register_all(load_command_specs())
    provider = GroqQwenIntentProvider(
        registry, GroqTransport(timeout_s=10.0), model=model, max_retries=0
    )
    print(f"Prompt {PROMPT_VERSION}, {len(registry)} intents, {repeat} call(s): {phrase!r}")

    latencies: list[float] = []
    for _ in range(repeat):
        started = time.monotonic()
        try:
            proposal = provider.propose(phrase, {"active_app": "chrome.exe"})
        except LLMUnavailableError as exc:
            cause = exc.__cause__
            detail = f" <- {type(cause).__name__}: {_safe(cause)}" if cause else ""
            print(f"\nFAIL: {_safe(exc)}{detail}")
            print("If the strict schema was refused, see the json_object fallback in ADR 0002.")
            return 1
        latencies.append((time.monotonic() - started) * 1000)

    if proposal is not None:
        print(
            f"OK: intent={proposal.intent} argument_seconds={proposal.argument_seconds} "
            f"confidence={proposal.confidence:.2f} confirm={proposal.requires_confirmation}"
        )
    p95 = statistics.quantiles(latencies, n=20)[18] if len(latencies) > 1 else latencies[0]
    print(
        f"latency ms: p50={statistics.median(latencies):.0f} p95={p95:.0f} max={max(latencies):.0f}"
    )
    print("\nRecord the p95 in docs/adr/0002-groq-model-id.md.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
