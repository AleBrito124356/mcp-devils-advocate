"""Offline, deterministic demo: a scripted client drives a real review.

No LLM, no network, no API keys. The scripted "client" below plays the part
the model normally plays — it submits the README's Rust-rewrite review phase
by phase through the real :class:`~mcp_devils_advocate.core.ReviewStore`,
first trying a lazy shortcut that the quality gate rejects, and finally
exports the Markdown decision memo.

Review ids and timestamps come from a seeded RNG and a fake clock, so every
run prints exactly the same output (the README quotes it verbatim).
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, TextIO

from .core import MODE_PHASES, GAUNTLET_LENSES, ReviewStore

CLAIM = "We should rewrite our backend in Rust"
CONTEXT = (
    "9-year-old Python monolith, 14 engineers, enterprise customers complaining "
    "about p99 latency."
)

# One list of batches per lens; a gauntlet review runs them back to back.
LENS_SCRIPTS: dict[str, list[list[dict]]] = {
    "devils_advocate": [
        [
            {
                "text": "Full rewrites of working systems fail or massively overrun far more often "
                "than they succeed; incremental strangler migrations have much better base rates.",
                "category": "base_rates",
                "severity": 4,
            },
            {
                "text": "The team has zero production Rust experience; hiring and ramp-up costs will "
                "land exactly while feature delivery is frozen.",
                "category": "evidence",
                "severity": 4,
            },
            {
                "text": "Profiling shows the bottleneck is the database layer, not CPU — a rewrite "
                "optimizes the part that isn't slow.",
                "category": "alternatives",
                "severity": 5,
            },
            {
                "text": "Engineers pushing the rewrite are the ones who want Rust on their CV — "
                "incentives are not aligned with the business case.",
                "category": "incentives",
                "severity": 2,
            },
        ],
        [
            {
                "index": 0,
                "verdict": "partially_holds",
                "justification": "True in general, but we can scope the rewrite to the two stateless "
                "services first, which is effectively a strangler migration.",
            },
            {
                "index": 1,
                "verdict": "holds",
                "justification": "No honest rebuttal: nobody on the team has shipped Rust, and the "
                "hiring market for it is thin in our region.",
            },
            {
                "index": 2,
                "verdict": "holds",
                "justification": "The profiling data is real — the p99 latency lives in Postgres "
                "queries. A Rust rewrite does not touch that.",
            },
        ],
    ],
    "assumptions": [
        [
            {
                "text": "Rust will cut our p99 latency because CPU work dominates request time.",
                "load_bearing": True,
                "evidence": "none",
            },
            {
                "text": "The team can reach production-level Rust fluency within one quarter.",
                "load_bearing": True,
                "evidence": "partial",
            },
            {
                "text": "Our payments SDK, ORM and queue client all have maintained Rust equivalents.",
                "load_bearing": True,
                "evidence": "verified",
            },
            {
                "text": "Customers will accept a slower feature roadmap while the migration runs.",
                "load_bearing": False,
                "evidence": "none",
            },
        ],
        [
            {
                "index": 0,
                "test": "Profile one hour of production traffic with a sampling profiler and "
                "measure the CPU share of request time.",
            },
            {
                "index": 1,
                "test": "Give two engineers a one-week spike on a real ticket and compare their "
                "throughput with the current stack.",
            },
        ],
    ],
    "premortem": [
        [{"horizon": "9 months"}],
        [
            {
                "text": "Feature work froze for two quarters and two enterprise customers churned "
                "to a competitor.",
                "likelihood": 3,
                "impact": 4,
            },
            {
                "text": "The two engineers who knew the billing edge cases left mid-migration.",
                "likelihood": 2,
                "impact": 5,
            },
            {
                "text": "Compile times and an immature ORM slowed the team far below its old velocity.",
                "likelihood": 3,
                "impact": 3,
            },
            {
                "text": "The new services shipped but latency barely moved because Postgres was the "
                "real bottleneck.",
                "likelihood": 4,
                "impact": 2,
            },
            {
                "text": "Hiring experienced Rust developers took five months longer than planned.",
                "likelihood": 2,
                "impact": 2,
            },
        ],
        [
            {
                "index": 0,
                "action": "Keep a four-person squad shipping roadmap features on the old stack and "
                "publish the roadmap to key accounts.",
                "residual_risk": "medium",
            },
            {
                "index": 1,
                "action": "Record billing walkthroughs now and pair each billing owner with a second "
                "engineer before any porting starts.",
                "residual_risk": "low",
            },
            {
                "index": 2,
                "action": "Start with a spike that measures compile times and ORM fit; abort if "
                "velocity drops more than 30 percent.",
                "residual_risk": "medium",
            },
        ],
    ],
    "steelman": [
        [
            {
                "text": "Fixing the slow Postgres queries delivers the latency win in weeks, with no "
                "rewrite risk at all."
            },
            {
                "text": "A rewrite throws away a decade of undocumented bug fixes that the current "
                "code silently encodes."
            },
            {
                "text": "Rust expertise is scarce locally, so every departure leaves a hole we cannot "
                "quickly refill."
            },
        ],
        [
            {
                "index": 0,
                "stance": "concede",
                "text": "We accept this: query tuning comes first and any Rust work waits for fresh "
                "profiling data.",
            },
            {
                "index": 1,
                "stance": "counter",
                "text": "Shadow traffic replay lets us diff old and new outputs, so hidden fixes "
                "surface before cut-over.",
            },
            {
                "index": 2,
                "stance": "concede",
                "text": "True for our market; we would depend on two people for a critical service "
                "for at least a year.",
            },
        ],
    ],
}

# What a lazy client might try first: one counterargument pasted under three
# categories. The quality gate rejects it and nothing is saved.
LAZY_ATTEMPT = [
    {"text": "Rust is hard to hire for and the rewrite will take a very long time.", "category": c, "severity": 4}
    for c in ("evidence", "base_rates", "scope")
]


def script_for(mode: str) -> list[list[dict]]:
    """The scripted batches for ``mode`` (a gauntlet runs every lens in order)."""
    if mode == "gauntlet":
        return [batch for lens in GAUNTLET_LENSES for batch in LENS_SCRIPTS[lens]]
    return LENS_SCRIPTS[mode]


def fake_clock(start: str = "2026-01-15T09:00:00+00:00") -> Callable[[], str]:
    """A clock that advances one minute per call — deterministic timestamps."""
    moment = [datetime.fromisoformat(start)]

    def tick() -> str:
        value = moment[0]
        moment[0] = value + timedelta(minutes=1)
        return value.astimezone(timezone.utc).isoformat(timespec="seconds")

    return tick


def demo_store(data_dir: str | Path) -> ReviewStore:
    return ReviewStore(data_dir, rng=random.Random(2026), clock=fake_clock())


def _short(text: str, limit: int = 96) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _describe_item(item: dict) -> str:
    if "horizon" in item:
        return f"horizon: {item['horizon']}"
    if "category" in item:
        return f"[{item['category']}, severity {item['severity']}] {_short(item['text'])}"
    if "likelihood" in item:
        return f"[likelihood {item['likelihood']} x impact {item['impact']}] {_short(item['text'])}"
    if "load_bearing" in item:
        lb = "load-bearing" if item["load_bearing"] else "not load-bearing"
        return f"[{lb}, evidence {item['evidence']}] {_short(item['text'])}"
    if "verdict" in item:
        return f"#{item['index']} {item['verdict']}: {_short(item['justification'])}"
    if "residual_risk" in item:
        return f"#{item['index']} residual {item['residual_risk']}: {_short(item['action'])}"
    if "test" in item:
        return f"#{item['index']} test: {_short(item['test'])}"
    if "stance" in item:
        return f"#{item['index']} {item['stance']}: {_short(item['text'])}"
    return _short(item.get("text", str(item)))


def _print_instructions(out: TextIO, ins: dict) -> None:
    lens = f" [{ins['lens']}]" if "lens" in ins else ""
    out.write(f"\n== Phase {ins['step']}: {ins['phase']}{lens} ==\n")
    out.write(f"Goal: {ins['goal']}\n")
    if ins.get("targets"):
        out.write(f"Targets: {len(ins['targets'])} ({', '.join('#' + str(t['index']) for t in ins['targets'])})\n")
    out.write("Item format:\n")
    for field, spec in ins["item_format"].items():
        out.write(f"  {field}: {spec}\n")
    out.write("Rules:\n")
    for rule in ins["rules"]:
        out.write(f"  - {rule}\n")
    if ins.get("quality_checks"):
        out.write("Quality checks:\n")
        for rule in ins["quality_checks"]:
            out.write(f"  - {rule}\n")


def run_demo(mode: str, data_dir: str | Path, out: TextIO, *, memo_only: bool = False) -> str:
    """Run the scripted review for ``mode`` in ``data_dir``; returns the review id."""
    store = demo_store(data_dir)
    say = (lambda _text: None) if memo_only else out.write

    say(
        "Offline demo: no LLM, no network. A scripted client plays the model and runs a\n"
        f"'{mode}' review through the real ReviewStore.\n\n"
    )
    started = store.start_review(CLAIM, mode, context=CONTEXT)
    review_id = started["review_id"]
    say(f"start_review(claim={CLAIM!r}, mode={mode!r}, context=...)\n")
    say(f"  -> {review_id}, phases: {' -> '.join(MODE_PHASES[mode])}\n")

    instructions: dict | None = started["instructions"]
    for number, batch in enumerate(script_for(mode)):
        if instructions is None:  # pragma: no cover - the scripts match the phase machine
            raise RuntimeError("demo script is longer than the review")
        if not memo_only:
            _print_instructions(out, instructions)
        if number == 0 and mode in ("devils_advocate", "gauntlet"):
            say("\nA lazy client pastes one counterargument under three categories:\n")
            try:
                store.submit(review_id, LAZY_ATTEMPT)
            except ValueError as exc:
                say("  REJECTED — " + str(exc).replace("\n", "\n  ") + "\n")
            say("The real client does the thinking instead.\n")
        say(f"\nsubmit({review_id}, {len(batch)} item(s)):\n")
        for item in batch:
            say(f"  {_describe_item(item)}\n")
        result = store.submit(review_id, batch)
        status = result["status"]
        if status == "phase_complete":
            instructions = result["next_phase"]
            skipped = result.get("skipped_phases") or []
            note = f" (skipped so far: {', '.join(skipped)})" if skipped else ""
            say(f"  -> phase_complete; next phase: {instructions['phase']}{note}\n")
        elif status == "complete":
            instructions = None
            say(f"  -> complete: {result['message']}\n")
        else:  # pragma: no cover - the scripts always complete their phase
            raise RuntimeError(f"demo batch left the phase incomplete: {result['missing']}")

    verdict = store.get_verdict(review_id)
    say(
        f"\nget_verdict({review_id}) -> {verdict['assessment']} "
        f"(risk score {verdict['risk_score']['value']}: {verdict['risk_score']['scale']})\n"
    )
    say(f"\nexport_report({review_id}, format='markdown'):\n\n")
    out.write(store.export_report(review_id)["content"])
    return review_id
