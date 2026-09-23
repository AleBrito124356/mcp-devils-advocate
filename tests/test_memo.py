"""Tests for the Markdown decision memo (mcp_devils_advocate.memo)."""

import pytest

from factories import CLAIM
from mcp_devils_advocate.core import MODES, ReviewStore
from mcp_devils_advocate.demo import CONTEXT, script_for
from mcp_devils_advocate.memo import next_actions, render_markdown, render_status_markdown

SECTIONS = {
    "devils_advocate": ["## Counterarguments"],
    "assumptions": ["## Assumptions"],
    "premortem": ["## Premortem"],
    "steelman": ["## Strongest case for the opposing side"],
    "gauntlet": [
        "## Counterarguments",
        "## Assumptions",
        "## Premortem",
        "## Strongest case for the opposing side",
        "| Lens | Assessment | Risk score |",
    ],
}


def completed(tmp_path, mode):
    store = ReviewStore(tmp_path)
    rid = store.start_review(CLAIM, mode, context=CONTEXT)["review_id"]
    for batch in script_for(mode):
        store.submit(rid, batch)
    return store, rid, store.get_verdict(rid)


@pytest.mark.parametrize("mode", MODES)
def test_memo_has_every_section(tmp_path, mode):
    _, rid, report = completed(tmp_path, mode)
    memo = render_markdown(report)
    assert memo.startswith(f"# Decision memo: {CLAIM}\n")
    assert f"`{rid}`" in memo
    assert f"**Assessment:** **{report['assessment']}**" in memo
    assert f"**Context:** {CONTEXT}" in memo
    assert "## Verdict" in memo and report["assessment_reason"] in memo
    assert "## Next actions" in memo
    for heading in SECTIONS[mode]:
        assert heading in memo, heading
    if mode in ("premortem", "gauntlet"):
        assert "**Premortem horizon:** 9 months" in memo


def test_next_actions_match_the_open_work(tmp_path):
    _, _, report = completed(tmp_path, "gauntlet")
    actions = next_actions(report)
    # devil's advocate: #0 partially holds, #1 and #2 hold; #3 needed no rebuttal
    assert [a.split(":")[0] for a in actions[:3]] == [
        "Resolve counterargument #0 (partially holds, severity 4)",
        "Resolve counterargument #1 (holds, severity 4)",
        "Resolve counterargument #2 (holds, severity 5)",
    ]
    tests = [a for a in actions if a.startswith("Run the test")]
    mitigations = [a for a in actions if a.startswith("Carry out the mitigation")]
    conceded = [a for a in actions if a.startswith("Account for conceded")]
    phases = report["phases"]
    assert len(tests) == sum(1 for a in phases["assumptions"] if a["test"])
    assert len(mitigations) == sum(1 for c in phases["failure_causes"] if c["mitigation"])
    assert len(conceded) == sum(1 for p in phases["strongest_case"] if p["response"]["stance"] == "concede")
    assert (len(tests), len(mitigations), len(conceded)) == (2, 3, 2)
    assert len(actions) == 3 + len(tests) + len(mitigations) + len(conceded)
    memo = render_markdown(report)
    assert memo.count("- [ ] ") == len(actions)


def test_nothing_open_is_said_explicitly(tmp_path):
    store = ReviewStore(tmp_path)
    rid = store.start_review(CLAIM, "assumptions")["review_id"]
    store.submit(
        rid,
        [
            {"text": "The team can learn the new stack within one quarter.", "load_bearing": True, "evidence": "verified"},
            {"text": "Customers tolerate a feature freeze during migration.", "load_bearing": False, "evidence": "none"},
            {"text": "CPU time is the dominant cost in our hosting bill.", "load_bearing": True, "evidence": "verified"},
            {"text": "Critical libraries have mature maintained equivalents.", "load_bearing": False, "evidence": "partial"},
        ],
    )
    report = store.get_verdict(rid)
    assert next_actions(report) == []
    memo = render_markdown(report)
    assert "- [x] Nothing left open" in memo
    assert "**Skipped phases:** `tests`" in memo


def test_user_text_cannot_break_the_layout(tmp_path):
    _, _, report = completed(tmp_path, "steelman")
    report["phases"]["strongest_case"][0]["text"] = "Line one\n\n## Injected heading\nline two"
    memo = render_markdown(report)
    assert "\n## Injected heading" not in memo
    assert "Line one ## Injected heading line two" in memo


def test_status_markdown(tmp_path):
    store = ReviewStore(tmp_path)
    rid = store.start_review(CLAIM, "premortem", context="ctx here")["review_id"]
    text = render_status_markdown(store.get_review(rid))
    assert text.startswith(f"# Review in progress: {CLAIM}")
    assert "**Current phase:** `setup` (step 1/3)" in text
    assert "## Still missing in this phase" in text
    store.abandon_review(rid, "no longer relevant")
    text = render_status_markdown(store.get_review(rid))
    assert text.startswith(f"# Review abandoned: {CLAIM}")
    assert "**Abandoned because:** no longer relevant" in text
