"""The utterance corpus, scored against the real local pipeline (no network)."""

from __future__ import annotations

from typing import Any

from scripts.score_corpus import SILENT, Case, load_corpus, score, summarize

from voice_agent.core.models import NO_ACTION_INTENT

CATEGORIES = {"exact", "paraphrase", "ambiguous", "out_of_scope", "adversarial", "background"}


def test_the_corpus_is_large_and_covers_every_command(shipped: Any) -> None:
    cases = load_corpus()
    registry = shipped.agent.registry
    commands = set(registry.intents()) - {NO_ACTION_INTENT}
    # A command with no phrases (web.open: an address) is only ever resolved by the cloud.
    spoken = {spec.intent for spec in registry if spec.aliases}
    cloud_only = commands - spoken

    assert len(cases) >= 150
    assert {case.category for case in cases} == CATEGORIES
    assert {case.expect for case in cases if case.category in SILENT} == {None}
    assert {case.expect for case in cases if case.expect} <= commands
    assert spoken - {NO_ACTION_INTENT} <= {
        case.expect for case in cases if case.category == "exact"
    }
    assert cloud_only <= {case.expect for case in cases if case.category == "paraphrase"}


def test_local_pipeline_gets_every_exact_phrase_and_never_acts_wrongly(shipped: Any) -> None:
    results = score(shipped.agent, load_corpus())
    wrong = [f"{r.case.say!r} -> {r.got}" for r in results if r.verdict == "false_accept"]
    missed = [
        f"{r.case.say!r} -> {r.got}"
        for r in results
        if r.case.category == "exact" and r.verdict != "correct"
    ]

    assert wrong == []
    assert missed == []
    summary = summarize(results)
    assert summary["all"].false_accept_rate == 0.0
    assert summary["exact"].accuracy == 1.0
    for category in SILENT:
        assert summary[category].accuracy == 1.0


def test_an_unwanted_or_wrong_action_is_a_false_accept_and_inaction_a_false_reject(
    shipped: Any,
) -> None:
    cases = [
        Case(category="background", say="next tab"),
        Case(category="exact", say="next tab", expect="browser.previous_tab"),
        Case(
            category="exact", say="skip forward 30 seconds", expect="media.seek_forward", seconds=20
        ),
        Case(category="paraphrase", say="hop one tab over", expect="browser.next_tab"),
        Case(category="exact", say="next tab", expect="browser.next_tab"),
    ]

    results = score(shipped.agent, cases)
    summary = summarize(results)["all"]

    assert [r.verdict for r in results] == [
        "false_accept",
        "false_accept",
        "false_accept",
        "false_reject",
        "correct",
    ]
    assert summary.accuracy == 1 / 5
    assert summary.false_accept_rate == 3 / 5
    assert summary.false_reject_rate == 1 / 4  # of the four that should have acted
