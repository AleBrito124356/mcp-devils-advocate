"""Tests for the anti-gaming quality gate (mcp_devils_advocate.quality).

Covers every check on both sides of its threshold, the four exploits from the
0.1.0 audit (each of which used to end in 'claim survives scrutiny'), and the
README example, which must keep passing unchanged.
"""

import json

import pytest

from factories import CLAIM, point, response
from mcp_devils_advocate import quality
from mcp_devils_advocate.core import ASSESS_REFUTED, ReviewStore
from mcp_devils_advocate.demo import LENS_SCRIPTS


@pytest.fixture()
def store(tmp_path):
    return ReviewStore(tmp_path)


def saved_items(store, rid, phase):
    data = json.loads((store.data_dir / f"{rid}.json").read_text(encoding="utf-8"))
    return data["phases"][phase]


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------


class TestNormalisation:
    def test_normalize_strips_accents_case_and_punctuation(self):
        assert quality.normalize("¡Migración RÁPIDA, sin riesgo!") == "migracion rapida sin riesgo"

    def test_stemmer_merges_simple_inflections(self):
        assert quality.word_set("rewriting services") == quality.word_set("rewrite service")
        assert quality.word_set("costs") == quality.word_set("cost")

    def test_stopwords_negations_and_filler_are_ignored(self):
        assert quality.content_words("We should not do this, it is a really bad idea") == []

    def test_keyboard_mash_is_not_a_word(self):
        assert quality.content_words("xxxxxxxxxx yyyyyy latency") == ["latency"]

    def test_numbers_are_kept(self):
        assert "12" in quality.content_words("in 12 months")

    def test_jaccard_and_coverage(self):
        assert quality.jaccard({"a", "b"}, {"a", "b"}) == 1.0
        assert quality.jaccard({"a", "b"}, {"c"}) == 0.0
        assert quality.jaccard(set(), set()) == 0.0
        assert quality.coverage({"a", "b"}, {"a"}) == 0.5


# ---------------------------------------------------------------------------
# Individual checks — both sides of each threshold
# ---------------------------------------------------------------------------


class TestLowInformation:
    def test_below_minimum_distinct_words(self):
        # latency, database, bottleneck = 3 content words < 4
        problem = quality.low_information("The latency is in the database bottleneck.", 4)
        assert problem and "3 distinct content word" in problem

    def test_at_minimum_passes(self):
        assert quality.low_information("The latency lives in the database bottleneck.", 4) is None

    def test_one_word_dominating_is_repetitive(self):
        text = "Rust Rust Rust Rust hiring costs Rust Rust"
        problem = quality.low_information(text, 3)
        assert problem and "repetitive" in problem and "'rust'" in problem

    def test_repetition_at_half_is_allowed(self):
        # 3 of 6 words = 50%, not more than MAX_WORD_DOMINANCE
        assert quality.low_information("Rust hiring Rust costs Rust delays", 3) is None


class TestNearDuplicate:
    def test_identical_after_normalisation(self):
        problem = quality.near_duplicate(
            "Our team lacks any Rust experience.", [("item 0", "The team lacks Rust experience!")], "x"
        )
        assert problem and "item 0" in problem and "1.00" in problem

    def test_below_threshold_passes(self):
        # {team, lack, rust, experience} vs {team, lack, go, experience}: 3/5 = 0.6
        assert (
            quality.near_duplicate(
                "The team lacks Rust experience.", [("item 0", "The team lacks Go experience.")], "x"
            )
            is None
        )

    def test_reports_the_closest_match(self):
        others = [("item 0", "Hiring takes months in our region"), ("item 1", "Postgres queries dominate p99 latency")]
        problem = quality.near_duplicate("Postgres queries dominate our p99 latency", others, "x")
        assert "item 1" in problem


class TestCopies:
    def test_restating_the_claim(self):
        assert quality.restates_claim("We should NOT rewrite our backend in Rust!", CLAIM)
        assert quality.restates_claim("Rewriting the backend in Rust is a bad idea.", CLAIM)

    def test_one_new_word_is_still_a_restatement(self):
        # adds only 'now' — fewer than MIN_NEW_WORDS new content words
        assert quality.restates_claim("Rewrite the backend in Rust immediately.", CLAIM)

    def test_two_new_words_and_low_coverage_pass(self):
        assert quality.restates_claim("Rust hiring takes months in our region.", CLAIM) is None

    def test_parroting_the_target(self):
        target = "The team can learn Rust within two months."
        problem = quality.parrots_target("Verify that the team can learn Rust within two months.", target, "assumption")
        assert problem and "parrots the assumption" in problem

    def test_real_answer_is_not_parroting(self):
        target = "The team can learn Rust within two months."
        answer = "Give two engineers a one-week spike on a real ticket and measure throughput."
        assert quality.parrots_target(answer, target, "assumption") is None


def test_rules_for_every_gated_phase_and_none_for_setup():
    for phase in quality.PHASE_RULES:
        assert quality.rules_for(phase)
    assert quality.rules_for("setup") == []
    assert any("parrots" in r for r in quality.rules_for("tests"))
    assert any("'counter'" in r for r in quality.rules_for("responses"))


# ---------------------------------------------------------------------------
# The four exploits from the 0.1.0 audit — all must now be rejected atomically
# ---------------------------------------------------------------------------


class TestAuditExploits:
    def test_junk_padding_is_rejected(self, store):
        rid = store.start_review(CLAIM, "devils_advocate")["review_id"]
        junk = [{"text": "x" * 30, "category": c, "severity": 5} for c in ("evidence", "scope", "base_rates")]
        with pytest.raises(ValueError) as exc:
            store.submit(rid, junk)
        message = str(exc.value)
        assert "nothing was saved" in message
        for idx in range(3):
            assert f"item {idx}: 'text' has too little information" in message
        assert saved_items(store, rid, "counterarguments") == []

    def test_junk_rebuttal_is_rejected(self, store):
        rid = store.start_review(CLAIM, "devils_advocate")["review_id"]
        store.submit(rid, LENS_SCRIPTS["devils_advocate"][0])
        with pytest.raises(ValueError, match="item 0: 'justification' has too little information"):
            store.submit(rid, [{"index": 0, "verdict": "refuted", "justification": "y" * 20}])

    def test_steelman_counters_that_repeat_the_claim(self, store):
        rid = store.start_review(CLAIM, "steelman")["review_id"]
        store.submit(rid, [point(), point(), point()])
        with pytest.raises(ValueError) as exc:
            store.submit(rid, [{"index": i, "stance": "counter", "text": CLAIM} for i in range(3)])
        assert str(exc.value).count("is a 'counter' that restates the claim") == 3
        assert saved_items(store, rid, "responses") == []

    def test_same_counterargument_under_three_categories(self, store):
        rid = store.start_review(CLAIM, "devils_advocate")["review_id"]
        text = "The team has zero production Rust experience and hiring will take many months."
        with pytest.raises(ValueError) as exc:
            store.submit(rid, [{"text": text, "category": c, "severity": 4} for c in ("evidence", "base_rates", "scope")])
        message = str(exc.value)
        assert "item 1: 'text' is a near-duplicate of item 0 (similarity 1.00)" in message
        assert "item 2: 'text' is a near-duplicate of item 0" in message
        assert "- item 0:" not in message  # the first copy itself is fine

    def test_justifications_copied_from_the_counterargument(self, store):
        rid = store.start_review(CLAIM, "devils_advocate")["review_id"]
        counters = LENS_SCRIPTS["devils_advocate"][0]
        store.submit(rid, counters)
        copied = [{"index": i, "verdict": "refuted", "justification": counters[i]["text"]} for i in range(3)]
        with pytest.raises(ValueError) as exc:
            store.submit(rid, copied)
        assert str(exc.value).count("parrots the counterargument it answers") == 3
        assert saved_items(store, rid, "rebuttals") == []


# ---------------------------------------------------------------------------
# Wiring into _validate_batch
# ---------------------------------------------------------------------------


class TestWiring:
    def test_duplicate_of_an_already_saved_item(self, store):
        rid = store.start_review(CLAIM, "assumptions")["review_id"]
        store.submit(rid, [{"text": "CPU time dominates our hosting bill.", "load_bearing": True, "evidence": "none"}])
        with pytest.raises(ValueError, match="near-duplicate of already-saved assumption 0"):
            store.submit(rid, [{"text": "Our hosting bill is dominated by CPU time.", "load_bearing": False, "evidence": "partial"}])

    def test_field_and_quality_errors_are_reported_together_in_item_order(self, store):
        rid = store.start_review(CLAIM, "devils_advocate")["review_id"]
        items = [
            {"text": "x" * 40, "category": "evidence", "severity": 3},
            {"text": "Hiring senior Rust developers here is slow and expensive.", "category": "vibes", "severity": 3},
        ]
        with pytest.raises(ValueError) as exc:
            store.submit(rid, items)
        lines = str(exc.value).splitlines()[1:]
        assert lines[0].startswith("- item 0: 'text' has too little information")
        assert lines[1].startswith("- item 1: 'category' must be one of")

    def test_boilerplate_rebuttals_are_near_duplicates(self, store):
        rid = store.start_review(CLAIM, "devils_advocate")["review_id"]
        store.submit(rid, LENS_SCRIPTS["devils_advocate"][0])
        same = "We disagree because our internal benchmarks clearly say otherwise."
        with pytest.raises(ValueError, match="item 1: 'justification' is a near-duplicate of item 0"):
            store.submit(rid, [{"index": i, "verdict": "refuted", "justification": same} for i in range(2)])

    def test_mitigation_parroting_its_failure_cause(self, store):
        rid = store.start_review(CLAIM, "premortem")["review_id"]
        store.submit(rid, [{"horizon": "6 months"}])
        causes = LENS_SCRIPTS["premortem"][1]
        store.submit(rid, causes)
        with pytest.raises(ValueError, match="parrots the failure cause"):
            store.submit(rid, [{"index": 0, "action": "Avoid that " + causes[0]["text"], "residual_risk": "low"}])

    def test_concession_may_not_parrot_the_point_either(self, store):
        rid = store.start_review(CLAIM, "steelman")["review_id"]
        pts = [point(), point(), point()]
        store.submit(rid, pts)
        with pytest.raises(ValueError, match="parrots the opposing point"):
            store.submit(rid, [response(0, stance="concede", text="True: " + pts[0]["text"])])

    def test_setup_phase_is_not_quality_gated(self, store):
        rid = store.start_review(CLAIM, "premortem")["review_id"]
        assert store.submit(rid, [{"horizon": "6 months"}])["status"] == "phase_complete"

    def test_quality_checks_listed_in_every_phase_instructions(self, store):
        for mode, script in LENS_SCRIPTS.items():
            result = store.start_review(CLAIM, mode, context="Small team, tight budget.")
            rid, instructions = result["review_id"], result["instructions"]
            for batch in script:
                assert instructions["claim"] == CLAIM
                assert instructions["context"] == "Small team, tight budget."
                assert isinstance(instructions["quality_checks"], list)
                if instructions["phase"] != "setup":
                    assert instructions["quality_checks"]
                out = store.submit(rid, batch)
                instructions = out.get("next_phase")
            assert out["status"] == "complete"


def test_readme_example_still_passes_and_is_refuted(store):
    """Regression: the README's Rust review is accepted unchanged and still ends 'claim refuted', risk 2."""
    rid = store.start_review(CLAIM, "devils_advocate")["review_id"]
    counters, rebuttals = LENS_SCRIPTS["devils_advocate"]
    result = store.submit(rid, counters)
    assert result["status"] == "phase_complete"
    assert [t["index"] for t in result["next_phase"]["targets"]] == [0, 1, 2]
    assert store.submit(rid, rebuttals)["status"] == "complete"
    verdict = store.get_verdict(rid)
    assert verdict["assessment"] == ASSESS_REFUTED
    assert verdict["risk_score"]["value"] == 2


def test_spanish_submissions_work(store):
    rid = store.start_review("Deberíamos migrar el backend a Rust", "devils_advocate")["review_id"]
    items = [
        {"text": "El equipo no tiene experiencia con Rust en producción y la contratación es lenta.", "category": "evidence", "severity": 4},
        {"text": "Las reescrituras completas suelen retrasarse muchísimo frente a migraciones graduales.", "category": "base_rates", "severity": 3},
        {"text": "El cuello de botella real está en las consultas a Postgres, no en la CPU.", "category": "alternatives", "severity": 5},
    ]
    assert store.submit(rid, items)["status"] == "phase_complete"
    with pytest.raises(ValueError, match="restates the claim"):
        store.submit(rid, [{"index": 0, "verdict": "refuted", "justification": "Deberíamos migrar el backend a Rust ya."}])
