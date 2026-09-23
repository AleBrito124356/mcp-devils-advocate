"""Tests for mcp_devils_advocate.core — run with `python -m pytest`. Does NOT import mcp.

Item texts come from tests/factories.py: realistic and distinct, because the
quality gate (tested in test_quality.py) rejects junk and duplicates.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp_devils_advocate.core import (  # noqa: E402
    ASSESS_REFUTED,
    ASSESS_REVISE,
    ASSESS_SURVIVES,
    MODES,
    ReviewStore,
)

from factories import (  # noqa: E402
    CLAIM,
    assumption,
    assumption_test,
    cause,
    counter,
    mitigation,
    point,
    rebuttal,
    response,
)


@pytest.fixture()
def store(tmp_path):
    return ReviewStore(tmp_path)


def start(store, mode="devils_advocate"):
    result = store.start_review(CLAIM, mode)
    return result["review_id"], result


# ---------------------------------------------------------------------------
# start_review
# ---------------------------------------------------------------------------


class TestStartReview:
    def test_invalid_mode_raises_and_lists_modes(self, store):
        with pytest.raises(ValueError) as exc:
            store.start_review(CLAIM, "socratic")
        for mode in MODES:
            assert mode in str(exc.value)

    def test_short_claim_raises(self, store):
        with pytest.raises(ValueError, match="at least 10"):
            store.start_review("nope", "devils_advocate")

    def test_id_format_and_first_phase(self, store):
        rid, result = start(store)
        assert rid.startswith("rev-")
        assert len(rid) == 8
        assert result["status"] == "active"
        assert result["instructions"]["phase"] == "counterarguments"
        assert "item_format" in result["instructions"]

    def test_premortem_starts_with_setup(self, store):
        _, result = start(store, "premortem")
        assert result["instructions"]["phase"] == "setup"

    def test_data_dir_created(self, tmp_path):
        target = tmp_path / "nested" / "state"
        ReviewStore(target)
        assert target.is_dir()


# ---------------------------------------------------------------------------
# devils_advocate
# ---------------------------------------------------------------------------


class TestDevilsAdvocate:
    def test_items_must_be_nonempty_list(self, store):
        rid, _ = start(store)
        with pytest.raises(ValueError, match="non-empty list"):
            store.submit(rid, [])
        with pytest.raises(ValueError, match="non-empty list"):
            store.submit(rid, "not a list")

    def test_invalid_category_raises(self, store):
        rid, _ = start(store)
        with pytest.raises(ValueError) as exc:
            store.submit(rid, [counter(category="vibes")])
        assert "category" in str(exc.value)
        assert "base_rates" in str(exc.value)  # lists valid choices

    def test_severity_out_of_range_raises(self, store):
        rid, _ = start(store)
        for bad in (0, 6, "3", None, 2.5):
            with pytest.raises(ValueError, match="severity"):
                store.submit(rid, [counter(severity=bad)])

    def test_text_too_short_raises(self, store):
        rid, _ = start(store)
        with pytest.raises(ValueError, match="30"):
            store.submit(rid, [counter(text="too short")])

    def test_invalid_batch_is_atomic(self, store):
        rid, _ = start(store)
        with pytest.raises(ValueError):
            store.submit(rid, [counter(), counter(severity=99)])
        result = store.submit(rid, [counter()])  # valid item — phase starts empty
        assert result["items_in_phase"] == 1

    def test_too_few_counterarguments_reports_missing(self, store):
        rid, _ = start(store)
        result = store.submit(rid, [counter(), counter(category="scope")])
        assert result["status"] == "in_progress"
        assert any("3" in m for m in result["missing"])

    def test_single_category_reports_missing_diversity(self, store):
        rid, _ = start(store)
        result = store.submit(rid, [counter(), counter(), counter()])
        assert result["status"] == "in_progress"
        assert any("categories" in m for m in result["missing"])

    def test_accumulation_across_calls(self, store):
        rid, _ = start(store)
        store.submit(rid, [counter(severity=1), counter(category="scope", severity=1)])
        result = store.submit(rid, [counter(category="base_rates", severity=1)])
        # all severities < 3 -> rebuttals skipped -> review complete
        assert result["status"] == "complete"
        assert "rebuttals" in result["skipped_phases"]

    def test_advance_to_rebuttals_with_targets(self, store):
        rid, _ = start(store)
        result = store.submit(
            rid,
            [counter(severity=4), counter(category="scope", severity=3), counter(severity=1)],
        )
        assert result["status"] == "phase_complete"
        instructions = result["next_phase"]
        assert instructions["phase"] == "rebuttals"
        targets = {t["index"] for t in instructions["targets"]}
        assert targets == {0, 1}  # only severity >= 3

    def _to_rebuttals(self, store, severities=(4, 3, 1)):
        rid, _ = start(store)
        cats = ["evidence", "scope", "base_rates", "incentives", "alternatives"]
        items = [counter(category=cats[i % len(cats)], severity=s) for i, s in enumerate(severities)]
        store.submit(rid, items)
        return rid

    def test_rebuttal_invalid_index_raises(self, store):
        rid = self._to_rebuttals(store)
        with pytest.raises(ValueError, match="pending"):
            store.submit(rid, [rebuttal(2)])  # severity 1 — not a target

    def test_rebuttal_invalid_verdict_raises(self, store):
        rid = self._to_rebuttals(store)
        with pytest.raises(ValueError, match="verdict"):
            store.submit(rid, [rebuttal(0, verdict="maybe")])

    def test_duplicate_rebuttal_in_batch_raises(self, store):
        rid = self._to_rebuttals(store)
        with pytest.raises(ValueError, match="duplicate"):
            store.submit(rid, [rebuttal(0), rebuttal(0)])

    def test_partial_rebuttals_report_missing(self, store):
        rid = self._to_rebuttals(store)
        result = store.submit(rid, [rebuttal(0)])
        assert result["status"] == "in_progress"
        assert len(result["missing"]) == 1
        assert "1" in result["missing"][0]

    def test_full_flow_and_submit_after_complete_raises(self, store):
        rid = self._to_rebuttals(store)
        result = store.submit(rid, [rebuttal(0), rebuttal(1)])
        assert result["status"] == "complete"
        assert "get_verdict" in result["message"]
        with pytest.raises(ValueError, match="already complete"):
            store.submit(rid, [rebuttal(0)])

    def test_verdict_all_refuted_survives(self, store):
        rid = self._to_rebuttals(store)
        store.submit(rid, [rebuttal(0), rebuttal(1)])
        verdict = store.get_verdict(rid)
        assert verdict["assessment"] == ASSESS_SURVIVES
        assert verdict["risk_score"]["value"] == 0
        assert verdict["claim"] == CLAIM
        # rebuttals are attached to their counterarguments
        assert verdict["phases"]["counterarguments"][0]["rebuttal"]["verdict"] == "refuted"
        assert verdict["phases"]["counterarguments"][2]["rebuttal"] is None

    def test_verdict_one_holds_needs_revision(self, store):
        rid = self._to_rebuttals(store, severities=(4, 3, 1))
        store.submit(rid, [rebuttal(0, verdict="holds"), rebuttal(1)])
        verdict = store.get_verdict(rid)
        assert verdict["assessment"] == ASSESS_REVISE
        assert verdict["risk_score"]["value"] == 1

    def test_verdict_two_hold_refuted(self, store):
        rid = self._to_rebuttals(store, severities=(4, 3, 1))
        store.submit(rid, [rebuttal(0, verdict="holds"), rebuttal(1, verdict="holds")])
        assert store.get_verdict(rid)["assessment"] == ASSESS_REFUTED

    def test_verdict_severity5_holds_refuted(self, store):
        rid = self._to_rebuttals(store, severities=(5, 3, 1))
        store.submit(rid, [rebuttal(0, verdict="holds"), rebuttal(1)])
        assert store.get_verdict(rid)["assessment"] == ASSESS_REFUTED

    def test_verdict_two_partial_needs_revision(self, store):
        rid = self._to_rebuttals(store, severities=(4, 3, 1))
        store.submit(
            rid,
            [rebuttal(0, verdict="partially_holds"), rebuttal(1, verdict="partially_holds")],
        )
        assert store.get_verdict(rid)["assessment"] == ASSESS_REVISE


# ---------------------------------------------------------------------------
# premortem
# ---------------------------------------------------------------------------


class TestPremortem:
    def _setup(self, store):
        rid, _ = start(store, "premortem")
        result = store.submit(rid, [{"horizon": "6 months"}])
        assert result["next_phase"]["phase"] == "failure_causes"
        return rid

    def test_setup_requires_horizon_field(self, store):
        rid, _ = start(store, "premortem")
        with pytest.raises(ValueError, match="horizon"):
            store.submit(rid, [{"timeframe": "6 months"}])

    def test_setup_takes_exactly_one_item(self, store):
        rid, _ = start(store, "premortem")
        with pytest.raises(ValueError, match="ONE item"):
            store.submit(rid, [{"horizon": "6 months"}, {"horizon": "1 year"}])

    def test_horizon_shows_in_failure_causes_goal(self, store):
        rid = self._setup(store)
        # re-load instructions via an invalid-count submit response
        result = store.submit(rid, [cause()])
        assert result["status"] == "in_progress"

    def test_likelihood_impact_validated(self, store):
        rid = self._setup(store)
        with pytest.raises(ValueError, match="likelihood"):
            store.submit(rid, [cause(likelihood=0)])
        with pytest.raises(ValueError, match="impact"):
            store.submit(rid, [cause(impact=7)])

    def test_min_four_causes(self, store):
        rid = self._setup(store)
        result = store.submit(rid, [cause(), cause(), cause()])
        assert result["status"] == "in_progress"
        assert any("4" in m for m in result["missing"])

    def test_mitigations_skipped_when_all_low_risk(self, store):
        rid = self._setup(store)
        result = store.submit(rid, [cause(), cause(), cause(), cause()])  # scores 4
        assert result["status"] == "complete"
        assert "mitigations" in result["skipped_phases"]
        verdict = store.get_verdict(rid)
        assert verdict["assessment"] == ASSESS_SURVIVES
        assert verdict["risk_score"]["value"] == 4.0
        assert verdict["horizon"] == "6 months"

    def test_mitigation_targets_and_validation(self, store):
        rid = self._setup(store)
        result = store.submit(
            rid, [cause(3, 3), cause(4, 4), cause(1, 1), cause(2, 2)]
        )
        instructions = result["next_phase"]
        assert instructions["phase"] == "mitigations"
        assert {t["index"] for t in instructions["targets"]} == {0, 1}
        with pytest.raises(ValueError, match="residual_risk"):
            store.submit(rid, [mitigation(0, residual_risk="extreme")])
        result = store.submit(rid, [mitigation(0)])
        assert result["status"] == "in_progress"
        result = store.submit(rid, [mitigation(1)])
        assert result["status"] == "complete"

    def test_verdict_average_and_needs_revision(self, store):
        rid = self._setup(store)
        # scores: 9, 9, 4, 4 -> average 6.5 -> needs revision
        store.submit(rid, [cause(3, 3), cause(3, 3), cause(2, 2), cause(2, 2)])
        store.submit(rid, [mitigation(0), mitigation(1)])
        verdict = store.get_verdict(rid)
        assert verdict["risk_score"]["value"] == 6.5
        assert verdict["assessment"] == ASSESS_REVISE

    def test_verdict_refuted_above_twelve(self, store):
        rid = self._setup(store)
        # scores: 16, 16, 16, 9 -> average 14.25 -> refuted
        store.submit(rid, [cause(4, 4), cause(4, 4), cause(4, 4), cause(3, 3)])
        store.submit(rid, [mitigation(i) for i in range(4)])
        assert store.get_verdict(rid)["assessment"] == ASSESS_REFUTED

    def test_high_residual_risk_bumps_to_revision(self, store):
        rid = self._setup(store)
        # scores: 9, 1, 1, 1 -> average 3 -> would survive, but residual high
        store.submit(rid, [cause(3, 3), cause(1, 1), cause(1, 1), cause(1, 1)])
        store.submit(rid, [mitigation(0, residual_risk="high")])
        assert store.get_verdict(rid)["assessment"] == ASSESS_REVISE


# ---------------------------------------------------------------------------
# assumptions
# ---------------------------------------------------------------------------


class TestAssumptions:
    def test_load_bearing_must_be_bool(self, store):
        rid, _ = start(store, "assumptions")
        with pytest.raises(ValueError, match="load_bearing"):
            store.submit(rid, [assumption(load_bearing="true")])

    def test_evidence_enum_validated(self, store):
        rid, _ = start(store, "assumptions")
        with pytest.raises(ValueError, match="evidence"):
            store.submit(rid, [assumption(evidence="solid")])

    def test_min_four_assumptions(self, store):
        rid, _ = start(store, "assumptions")
        result = store.submit(rid, [assumption(), assumption(), assumption()])
        assert result["status"] == "in_progress"

    def test_tests_skipped_when_nothing_qualifies(self, store):
        rid, _ = start(store, "assumptions")
        result = store.submit(
            rid,
            [
                assumption(evidence="verified"),
                assumption(load_bearing=False),
                assumption(load_bearing=False, evidence="partial"),
                assumption(evidence="verified"),
            ],
        )
        assert result["status"] == "complete"
        assert "tests" in result["skipped_phases"]
        assert store.get_verdict(rid)["assessment"] == ASSESS_SURVIVES

    def test_targets_are_unverified_load_bearing(self, store):
        rid, _ = start(store, "assumptions")
        result = store.submit(
            rid,
            [
                assumption(evidence="none"),          # target
                assumption(evidence="partial"),        # target
                assumption(load_bearing=False),        # not load-bearing
                assumption(evidence="verified"),       # verified
            ],
        )
        instructions = result["next_phase"]
        assert instructions["phase"] == "tests"
        assert {t["index"] for t in instructions["targets"]} == {0, 1}
        result = store.submit(rid, [assumption_test(0), assumption_test(1)])
        assert result["status"] == "complete"

    def test_verdict_two_none_refuted(self, store):
        rid, _ = start(store, "assumptions")
        store.submit(
            rid,
            [
                assumption(evidence="none"),
                assumption(evidence="none"),
                assumption(evidence="verified"),
                assumption(evidence="verified"),
            ],
        )
        store.submit(rid, [assumption_test(0), assumption_test(1)])
        verdict = store.get_verdict(rid)
        assert verdict["assessment"] == ASSESS_REFUTED
        assert verdict["risk_score"]["value"] == 2

    def test_verdict_one_none_needs_revision(self, store):
        rid, _ = start(store, "assumptions")
        store.submit(
            rid,
            [
                assumption(evidence="none"),
                assumption(evidence="verified"),
                assumption(evidence="verified"),
                assumption(evidence="verified"),
            ],
        )
        store.submit(rid, [assumption_test(0)])
        assert store.get_verdict(rid)["assessment"] == ASSESS_REVISE

    def test_verdict_two_partial_needs_revision(self, store):
        rid, _ = start(store, "assumptions")
        store.submit(
            rid,
            [
                assumption(evidence="partial"),
                assumption(evidence="partial"),
                assumption(evidence="verified"),
                assumption(evidence="verified"),
            ],
        )
        store.submit(rid, [assumption_test(0), assumption_test(1)])
        assert store.get_verdict(rid)["assessment"] == ASSESS_REVISE


# ---------------------------------------------------------------------------
# steelman
# ---------------------------------------------------------------------------


class TestSteelman:
    def _to_responses(self, store):
        rid, _ = start(store, "steelman")
        result = store.submit(rid, [point(), point(), point()])
        assert result["next_phase"]["phase"] == "responses"
        return rid

    def test_point_min_length(self, store):
        rid, _ = start(store, "steelman")
        with pytest.raises(ValueError, match="30"):
            store.submit(rid, [point(text="weak point")])

    def test_min_three_points(self, store):
        rid, _ = start(store, "steelman")
        result = store.submit(rid, [point(), point()])
        assert result["status"] == "in_progress"

    def test_stance_enum_validated(self, store):
        rid = self._to_responses(store)
        with pytest.raises(ValueError, match="stance"):
            store.submit(rid, [response(0, stance="dodge")])

    def test_every_point_needs_response(self, store):
        rid = self._to_responses(store)
        result = store.submit(rid, [response(0), response(1)])
        assert result["status"] == "in_progress"
        assert len(result["missing"]) == 1

    def test_verdict_all_conceded_refuted(self, store):
        rid = self._to_responses(store)
        store.submit(rid, [response(i, stance="concede") for i in range(3)])
        verdict = store.get_verdict(rid)
        assert verdict["assessment"] == ASSESS_REFUTED
        assert verdict["risk_score"]["value"] == 3

    def test_verdict_majority_conceded_needs_revision(self, store):
        rid = self._to_responses(store)
        store.submit(
            rid,
            [response(0, stance="concede"), response(1, stance="concede"), response(2)],
        )
        assert store.get_verdict(rid)["assessment"] == ASSESS_REVISE

    def test_verdict_counters_win_survives(self, store):
        rid = self._to_responses(store)
        store.submit(rid, [response(0, stance="concede"), response(1), response(2)])
        verdict = store.get_verdict(rid)
        assert verdict["assessment"] == ASSESS_SURVIVES
        assert verdict["phases"]["strongest_case"][0]["response"]["stance"] == "concede"


# ---------------------------------------------------------------------------
# lifecycle: verdict gating, abandon, list, persistence
# ---------------------------------------------------------------------------


class TestLifecycle:
    def test_get_verdict_before_complete_raises(self, store):
        rid, _ = start(store)
        with pytest.raises(ValueError, match="counterarguments"):
            store.get_verdict(rid)

    def test_unknown_review_raises(self, store):
        with pytest.raises(ValueError, match="not found"):
            store.submit("rev-zzzz", [counter()])
        with pytest.raises(ValueError, match="Invalid review id"):
            store.get_verdict("../../etc")

    def test_abandon_requires_reason(self, store):
        rid, _ = start(store)
        with pytest.raises(ValueError, match="reason"):
            store.abandon_review(rid, "   ")

    def test_abandon_blocks_submit_and_verdict(self, store):
        rid, _ = start(store)
        result = store.abandon_review(rid, "Superseded by a better claim")
        assert result["status"] == "abandoned"
        with pytest.raises(ValueError, match="abandoned"):
            store.submit(rid, [counter()])
        with pytest.raises(ValueError, match="abandoned"):
            store.get_verdict(rid)
        with pytest.raises(ValueError, match="already abandoned"):
            store.abandon_review(rid, "again")

    def test_list_reviews(self, store):
        rid1, _ = start(store)
        rid2, _ = start(store, "steelman")
        store.abandon_review(rid2, "changed my mind")
        listing = store.list_reviews()
        assert listing["count"] == 2
        by_id = {r["review_id"]: r for r in listing["reviews"]}
        assert by_id[rid1]["status"] == "active"
        assert by_id[rid1]["phase"] == "counterarguments"
        assert by_id[rid2]["status"] == "abandoned"
        assert by_id[rid1]["mode"] == "devils_advocate"

    def test_persistence_across_store_instances(self, tmp_path):
        store1 = ReviewStore(tmp_path)
        rid, _ = start(store1)
        store1.submit(
            rid,
            [counter(severity=4), counter(category="scope", severity=1), counter(category="base_rates", severity=1)],
        )
        # fresh instance, same dir — state must survive
        store2 = ReviewStore(tmp_path)
        listing = store2.list_reviews()
        assert listing["count"] == 1
        result = store2.submit(rid, [rebuttal(0)])
        assert result["status"] == "complete"
        verdict = store2.get_verdict(rid)
        assert verdict["assessment"] == ASSESS_SURVIVES
        # file on disk is valid JSON with the full state
        data = json.loads((tmp_path / f"{rid}.json").read_text(encoding="utf-8"))
        assert data["status"] == "complete"
        assert data["mode"] == "devils_advocate"

    def test_verdict_is_repeatable(self, store):
        rid, _ = start(store, "steelman")
        store.submit(rid, [point(), point(), point()])
        store.submit(rid, [response(i) for i in range(3)])
        first = store.get_verdict(rid)
        second = store.get_verdict(rid)
        assert first == second


# ---------------------------------------------------------------------------
# 0.2.0 lifecycle fixes: final verdicts, robust listing, resume, export
# ---------------------------------------------------------------------------


def _complete_steelman(store):
    rid, _ = start(store, "steelman")
    store.submit(rid, [point(), point(), point()])
    store.submit(rid, [response(0, stance="concede"), response(1), response(2)])
    return rid


class TestLifecycleV2:
    def test_complete_review_cannot_be_abandoned(self, store):
        rid = _complete_steelman(store)
        with pytest.raises(ValueError, match="already complete — its verdict is final"):
            store.abandon_review(rid, "oops")
        # the verdict is still there
        assert store.get_verdict(rid)["assessment"] == ASSESS_SURVIVES
        assert store.list_reviews()["reviews"][0]["status"] == "complete"

    def test_list_reviews_skips_malformed_files(self, store, tmp_path):
        rid, _ = start(store)
        (tmp_path / "rev-zzzz.json").write_text("{}", encoding="utf-8")
        (tmp_path / "rev-yyyy.json").write_text("{not json", encoding="utf-8")
        (tmp_path / "rev-wwww.json").write_text("[1, 2]", encoding="utf-8")
        listing = store.list_reviews()
        assert listing["count"] == 1
        assert listing["reviews"][0]["review_id"] == rid
        skipped = {s["file"]: s["reason"] for s in listing["skipped"]}
        assert skipped["rev-zzzz.json"].startswith("missing keys: id")
        assert skipped["rev-yyyy.json"] == "unreadable (JSONDecodeError)"
        assert skipped["rev-wwww.json"].startswith("not a JSON object")

    def test_listing_without_problems_has_no_skipped_key(self, store):
        start(store)
        assert "skipped" not in store.list_reviews()

    def test_corrupted_review_gives_a_clean_error(self, store, tmp_path):
        (tmp_path / "rev-zzzz.json").write_text('{"id": "rev-zzzz"}', encoding="utf-8")
        with pytest.raises(ValueError, match="corrupted on disk"):
            store.submit("rev-zzzz", [counter()])
        (tmp_path / "rev-yyyy.json").write_text("garbage", encoding="utf-8")
        with pytest.raises(ValueError, match="unreadable on disk"):
            store.get_verdict("rev-yyyy")

    def test_same_second_reviews_are_listed_in_creation_order(self, tmp_path):
        store = ReviewStore(tmp_path, clock=lambda: "2026-01-01T00:00:00+00:00")
        ids = [store.start_review(CLAIM, "steelman")["review_id"] for _ in range(6)]
        listed = [r["review_id"] for r in store.list_reviews()["reviews"]]
        assert listed == list(reversed(ids))

    def test_context_is_repeated_in_every_phase(self, store):
        result = store.start_review(CLAIM, "devils_advocate", context="Budget is fixed at 2 engineers.")
        assert result["instructions"]["context"] == "Budget is fixed at 2 engineers."
        out = store.submit(
            result["review_id"], [counter(severity=4), counter(category="scope"), counter(category="scope")]
        )
        assert out["next_phase"]["context"] == "Budget is fixed at 2 engineers."
        assert out["next_phase"]["claim"] == CLAIM

    def test_no_context_key_when_context_is_empty(self, store):
        _, result = start(store)
        assert "context" not in result["instructions"]

    def test_get_review_for_each_status(self, store):
        rid, _ = start(store)
        active = store.get_review(rid)
        assert active["status"] == "active"
        assert active["step"] == "1/2"
        assert active["instructions"]["phase"] == "counterarguments"
        assert any("3 counterarguments" in m for m in active["missing"])

        done = store.get_review(_complete_steelman(store))
        assert done["status"] == "complete" and "get_verdict" in done["next"]
        assert "instructions" not in done

        rid2, _ = start(store, "premortem")
        store.abandon_review(rid2, "scope changed")
        gone = store.get_review(rid2)
        assert gone["status"] == "abandoned" and gone["abandon_reason"] == "scope changed"

    def test_export_report_formats(self, store):
        rid = _complete_steelman(store)
        md = store.export_report(rid)
        assert md["format"] == "markdown"
        assert md["content"].startswith(f"# Decision memo: {CLAIM}")
        assert store.export_report(rid, "md")["content"] == md["content"]
        js = store.export_report(rid, "json")
        assert json.loads(js["content"]) == store.get_verdict(rid)
        with pytest.raises(ValueError, match="Unknown format"):
            store.export_report(rid, "pdf")

    def test_export_report_requires_a_complete_review(self, store):
        rid, _ = start(store)
        with pytest.raises(ValueError, match="not complete"):
            store.export_report(rid)

    def test_protocol_prompt(self):
        from mcp_devils_advocate.core import protocol_prompt

        text = protocol_prompt(CLAIM, "gauntlet")
        assert CLAIM in text and "mode='gauntlet'" in text
        assert "counterarguments -> rebuttals -> assumptions" in text
        with pytest.raises(ValueError, match="Unknown mode"):
            protocol_prompt(CLAIM, "socratic")
        with pytest.raises(ValueError, match="claim"):
            protocol_prompt("short", "steelman")

    def test_legacy_review_files_still_load(self, store, tmp_path):
        """A review written by 0.1.0 (no created_ns) keeps working."""
        rid, _ = start(store)
        path = tmp_path / f"{rid}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        del data["created_ns"]
        path.write_text(json.dumps(data), encoding="utf-8")
        assert store.list_reviews()["count"] == 1
        result = store.submit(
            rid,
            [counter(severity=1), counter(category="scope", severity=1), counter(category="base_rates", severity=1)],
        )
        assert result["status"] == "complete"
