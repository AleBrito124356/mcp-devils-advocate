"""Item factories with realistic, mutually distinct texts.

The quality gate rejects junk padding, near-duplicates, restatements of the
claim and answers that parrot their target, so test items must look like what
a real client would send. Each factory draws the next text from its pool, so
consecutive calls in one test never collide; ``reset()`` rewinds every pool.
"""

from __future__ import annotations

import itertools

CLAIM = "We should rewrite our backend in Rust"

COUNTER_TEXTS = [
    "Full rewrites of working systems overrun their schedule far more often than incremental migrations do.",
    "The team has no production Rust experience, so velocity collapses during a long ramp-up period.",
    "Profiling shows the database layer is the bottleneck, so a faster language barely moves latency.",
    "Engineers pushing the rewrite want Rust on their resumes, which skews the business case.",
    "Feature delivery freezes for quarters while competitors keep shipping to our customers.",
    "Hiring senior Rust developers in our region is slow and expensive compared with Go or Java.",
    "Porting thousands of edge cases from the legacy code will reintroduce bugs we fixed years ago.",
    "Library support for our payment provider and ORM is immature in the Rust ecosystem today.",
]

JUSTIFICATIONS = [
    "Our pilot migrated one stateless service in three weeks with zero incidents.",
    "Two engineers already maintain an internal command-line tool in Rust and can mentor others.",
    "Benchmarks after query tuning show CPU is now forty percent of request time.",
    "Budget approval came from the CFO based on hosting savings, not engineer preference.",
    "A small squad keeps shipping features on the old stack during the migration.",
    "A contractor agency offered three experienced developers starting next month.",
]

CAUSE_TEXTS = [
    "Key engineers quit halfway through the migration and take the domain knowledge with them.",
    "The new service mishandles currency rounding and corrupts invoices for a week.",
    "Customers churn because no new features ship for two consecutive quarters.",
    "Compile times balloon and the deploy pipeline slows from minutes to an hour.",
    "The ORM replacement cannot express our reporting queries, forcing raw SQL everywhere.",
    "A security audit flags unsafe blocks copied from forum answers without review.",
]

MITIGATION_ACTIONS = [
    "Run a two-week spike with a rollback plan before committing more engineers.",
    "Pair every legacy module owner with a newcomer and record walkthrough videos.",
    "Shadow-run the new invoice path against production traffic and diff every output.",
    "Reserve thirty percent of sprint capacity for customer-facing roadmap items.",
    "Add incremental compilation caching and split the workspace into smaller crates.",
]

ASSUMPTION_TEXTS = [
    "The team can learn the new stack within one quarter.",
    "Customers will tolerate a feature freeze during the migration.",
    "CPU time is the dominant cost in our hosting bill.",
    "Critical libraries have mature, maintained equivalents available.",
    "Management keeps funding the project even if the first milestone slips.",
    "The legacy test suite covers enough behaviour to catch regressions.",
]

TEST_TEXTS = [
    "Prototype the riskiest module in one afternoon and time it.",
    "Ask five key accounts whether a two-month freeze would hurt renewals.",
    "Pull last month's cloud invoice and split costs by compute, storage and network.",
    "List our top ten dependencies and check crates.io for maintained equivalents.",
    "Ask the sponsor in writing what happens if milestone one slips a month.",
]

POINT_TEXTS = [
    "Incremental improvement of the existing stack delivers most of the gains at a fraction of the risk.",
    "Our current language has a far larger hiring pool, which keeps salaries and onboarding time down.",
    "The measured latency problems come from database queries that any language would suffer from.",
    "Rewrites discard years of battle-tested bug fixes that nobody documented anywhere.",
    "Customers pay for features and reliability, not for the implementation language behind them.",
]

RESPONSE_TEXTS = [
    "Query tuning is already done and CPU is now the measured ceiling.",
    "We accept this and will budget onboarding time explicitly in the plan.",
    "Salary data from our last hiring round shows only a small premium.",
    "The pilot proved we can port fixes by replaying production traffic.",
    "Agreed, so the migration will ship behind feature parity checkpoints.",
]

_POOLS = {
    "counter": COUNTER_TEXTS,
    "justification": JUSTIFICATIONS,
    "cause": CAUSE_TEXTS,
    "action": MITIGATION_ACTIONS,
    "assumption": ASSUMPTION_TEXTS,
    "test": TEST_TEXTS,
    "point": POINT_TEXTS,
    "response": RESPONSE_TEXTS,
}
_cycles: dict[str, itertools.cycle] = {}


def reset() -> None:
    for name, pool in _POOLS.items():
        _cycles[name] = itertools.cycle(pool)


def _next(name: str) -> str:
    return next(_cycles[name])


reset()


def counter(text=None, category="evidence", severity=2):
    return {"text": text or _next("counter"), "category": category, "severity": severity}


def rebuttal(index, verdict="refuted", justification=None):
    return {"index": index, "verdict": verdict, "justification": justification or _next("justification")}


def cause(likelihood=2, impact=2, text=None):
    return {"text": text or _next("cause"), "likelihood": likelihood, "impact": impact}


def mitigation(index, residual_risk="low", action=None):
    return {"index": index, "action": action or _next("action"), "residual_risk": residual_risk}


def assumption(load_bearing=True, evidence="none", text=None):
    return {"text": text or _next("assumption"), "load_bearing": load_bearing, "evidence": evidence}


def assumption_test(index, test=None):
    return {"index": index, "test": test or _next("test")}


def point(text=None):
    return {"text": text or _next("point")}


def response(index, stance="counter", text=None):
    return {"index": index, "stance": stance, "text": text or _next("response")}
