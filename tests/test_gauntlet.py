"""Tests for 'gauntlet' mode: all four lenses on one claim, combined verdict."""

import json

import pytest

from factories import CLAIM, assumption, assumption_test, cause, counter, mitigation, point, rebuttal, response
from mcp_devils_advocate.core import (
    ASSESS_REFUTED,
    ASSESS_REVISE,
    ASSESS_SURVIVES,
    GAUNTLET_LENSES,
    MODE_PHASES,
    ReviewStore,
)
from mcp_devils_advocate.demo import script_for

ALL_PHASES = (
    "counterarguments",
    "rebuttals",
    "assumptions",
    "tests",
    "setup",
    "failure_causes",
    "mitigations",
    "strongest_case",
    "responses",
)


@pytest.fixture()
def store(tmp_path):
    return ReviewStore(tmp_path)


def test_phase_list():
    assert MODE_PHASES["gauntlet"] == ALL_PHASES
    assert GAUNTLET_LENSES == ("devils_advocate", "assumptions", "premortem", "steelman")


def test_full_flow_through_all_nine_phases(store):
    started = store.start_review(CLAIM, "gauntlet", context="Monolith, 14 engineers.")
    rid = started["review_id"]
    assert started["phases"] == list(ALL_PHASES)
    instructions = started["instructions"]
    seen = []
    for batch in script_for("gauntlet"):
        seen.append((instructions["phase"], instructions["lens"], instructions["step"]))
        result = store.submit(rid, batch)
        instructions = result.get("next_phase")
    assert result["status"] == "complete"
    assert result["skipped_phases"] == []
    assert [phase for phase, _, _ in seen] == list(ALL_PHASES)
    assert seen[0] == ("counterarguments", "devils_advocate", "1/9")
    assert seen[4] == ("setup", "premortem", "5/9")
    assert seen[8] == ("responses", "steelman", "9/9")

    verdict = store.get_verdict(rid)
    assert verdict["mode"] == "gauntlet"
    assert verdict["horizon"] == "9 months"
    assert set(verdict["lenses"]) == set(GAUNTLET_LENSES)
    assert verdict["lenses"]["devils_advocate"]["assessment"] == ASSESS_REFUTED
    for lens in ("assumptions", "premortem", "steelman"):
        assert verdict["lenses"][lens]["assessment"] == ASSESS_REVISE
    assert verdict["lenses"]["premortem"]["phases"] == ["setup", "failure_causes", "mitigations"]
    # exactly one refuting lens -> needs revision; risk = 2*1 + 3
    assert verdict["assessment"] == ASSESS_REVISE
    assert verdict["risk_score"]["value"] == 5
    assert "1 of 4 lenses refute" in verdict["assessment_reason"]
    # every lens's organised items are in the merged phases
    assert set(verdict["phases"]) == {"counterarguments", "assumptions", "setup", "failure_causes", "strongest_case"}
    assert verdict["phases"]["counterarguments"][2]["rebuttal"]["verdict"] == "holds"
    assert verdict["phases"]["failure_causes"][0]["mitigation"]["residual_risk"] == "medium"


def test_dependent_phases_are_auto_skipped(store):
    rid = store.start_review(CLAIM, "gauntlet")["review_id"]
    order = []

    def send(items):
        result = store.submit(rid, items)
        if result["status"] == "phase_complete":
            order.append(result["next_phase"]["phase"])
        return result

    send([counter(severity=2), counter(category="scope", severity=1), counter(category="base_rates", severity=2)])
    send([assumption(evidence="verified"), assumption(load_bearing=False), assumption(evidence="verified"),
          assumption(load_bearing=False, evidence="partial")])
    send([{"horizon": "1 year"}])
    send([cause(), cause(), cause(), cause()])
    send([point(), point(), point()])
    result = send([response(0), response(1), response(2, stance="concede")])

    assert order == ["assumptions", "setup", "failure_causes", "strongest_case", "responses"]
    assert result["status"] == "complete"
    assert result["skipped_phases"] == ["rebuttals", "tests", "mitigations"]
    verdict = store.get_verdict(rid)
    assert verdict["assessment"] == ASSESS_SURVIVES
    assert verdict["risk_score"]["value"] == 0


# ---------------------------------------------------------------------------
# Combined-rule branches: drive each lens to a chosen outcome
# ---------------------------------------------------------------------------


def _devils_advocate(outcome):
    counters = [counter(severity=4), counter(category="scope", severity=3), counter(category="base_rates", severity=1)]
    verdicts = {"survives": ("refuted", "refuted"), "revise": ("holds", "refuted"), "refuted": ("holds", "holds")}[outcome]
    return [counters, [rebuttal(0, verdict=verdicts[0]), rebuttal(1, verdict=verdicts[1])]]


def _assumptions(outcome):
    evidence = {
        "survives": ("verified", "verified", "verified", "verified"),
        "revise": ("none", "verified", "verified", "verified"),
        "refuted": ("none", "none", "verified", "verified"),
    }[outcome]
    batches = [[assumption(evidence=e) for e in evidence]]
    targets = [i for i, e in enumerate(evidence) if e != "verified"]
    if targets:
        batches.append([assumption_test(i) for i in targets])
    return batches


def _premortem(outcome):
    scores = {
        "survives": ((2, 2), (2, 2), (2, 2), (2, 2)),
        "revise": ((3, 3), (3, 3), (2, 2), (2, 2)),
        "refuted": ((4, 4), (4, 4), (4, 4), (3, 3)),
    }[outcome]
    batches = [[{"horizon": "6 months"}], [cause(l, i) for l, i in scores]]
    targets = [n for n, (l, i) in enumerate(scores) if l * i >= 9]
    if targets:
        batches.append([mitigation(n) for n in targets])
    return batches


def _steelman(outcome):
    stances = {
        "survives": ("concede", "counter", "counter"),
        "revise": ("concede", "concede", "counter"),
        "refuted": ("concede", "concede", "concede"),
    }[outcome]
    return [[point(), point(), point()], [response(i, stance=s) for i, s in enumerate(stances)]]


def run_gauntlet(store, da, asm, pm, st):
    rid = store.start_review(CLAIM, "gauntlet")["review_id"]
    for batch in _devils_advocate(da) + _assumptions(asm) + _premortem(pm) + _steelman(st):
        result = store.submit(rid, batch)
    assert result["status"] == "complete"
    return store.get_verdict(rid)


@pytest.mark.parametrize(
    "outcomes, expected, risk",
    [
        # 0 refuting, 0 revising -> survives
        (("survives", "survives", "survives", "survives"), ASSESS_SURVIVES, 0),
        # 0 refuting, 1 revising -> still survives
        (("survives", "revise", "survives", "survives"), ASSESS_SURVIVES, 1),
        # exactly 1 refuting -> needs revision
        (("survives", "survives", "refuted", "survives"), ASSESS_REVISE, 2),
        # 2 revising, 0 refuting -> needs revision
        (("revise", "survives", "revise", "survives"), ASSESS_REVISE, 2),
        # 2 refuting -> refuted
        (("refuted", "survives", "survives", "refuted"), ASSESS_REFUTED, 4),
        # everything refutes -> refuted, maximum risk
        (("refuted", "refuted", "refuted", "refuted"), ASSESS_REFUTED, 8),
    ],
)
def test_combined_rule(store, outcomes, expected, risk):
    verdict = run_gauntlet(store, *outcomes)
    names = {"survives": ASSESS_SURVIVES, "revise": ASSESS_REVISE, "refuted": ASSESS_REFUTED}
    for lens, outcome in zip(GAUNTLET_LENSES, outcomes):
        assert verdict["lenses"][lens]["assessment"] == names[outcome], lens
    assert verdict["assessment"] == expected
    assert verdict["risk_score"]["value"] == risk


def test_wrong_phase_submission_is_rejected_atomically(store):
    rid = store.start_review(CLAIM, "gauntlet")["review_id"]
    store.submit(rid, [counter(severity=1), counter(category="scope", severity=1), counter(category="base_rates", severity=1)])
    # now in 'assumptions' (rebuttals skipped); counterargument-shaped items do not fit
    with pytest.raises(ValueError) as exc:
        store.submit(rid, [counter(severity=4)])
    assert "load_bearing" in str(exc.value) and "evidence" in str(exc.value)
    data = json.loads((store.data_dir / f"{rid}.json").read_text(encoding="utf-8"))
    assert data["phase"] == "assumptions"
    assert data["phases"]["assumptions"] == []
    assert len(data["phases"]["counterarguments"]) == 3


def test_get_review_mid_gauntlet(store):
    rid = store.start_review(CLAIM, "gauntlet")["review_id"]
    batches = script_for("gauntlet")
    for batch in batches[:4]:
        store.submit(rid, batch)
    summary = store.get_review(rid)
    assert summary["phase"] == "setup"
    assert summary["step"] == "5/9"
    assert summary["instructions"]["lens"] == "premortem"
    assert summary["items_per_phase"]["counterarguments"] == 4
    assert summary["items_per_phase"]["tests"] == 2
    assert summary["missing"]
