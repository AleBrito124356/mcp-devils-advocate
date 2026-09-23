"""The README must describe what the code does: the pasted demo output is
regenerated and compared, and every mode / tool / resource is documented."""

import io
import re
from pathlib import Path

from mcp_devils_advocate import quality
from mcp_devils_advocate.core import MODES
from mcp_devils_advocate.demo import run_demo

README = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")


def test_pasted_demo_output_is_current(tmp_path):
    match = re.search(r"<!-- demo-output:start -->\n```text\n(.*?)```\n<!-- demo-output:end -->", README, re.S)
    assert match, "demo output block not found in README"
    buf = io.StringIO()
    run_demo("devils_advocate", tmp_path, buf)
    assert match.group(1) == buf.getvalue(), "README demo output is stale: re-run `mcp-devils-advocate demo`"


def test_every_mode_tool_and_resource_is_documented():
    for mode in MODES:
        assert f"| `{mode}` |" in README, mode
    for tool in ("start_review", "submit", "get_verdict", "export_report", "get_review", "list_reviews", "abandon_review"):
        assert f"| `{tool}` |" in README, tool
    for name in ("`reviews://`", "`review://{review_id}`", "`stress_test(claim, mode=\"devils_advocate\")`"):
        assert name in README, name


def test_documented_thresholds_match_the_code():
    assert f"Jaccard word overlap ≥ {quality.DUPLICATE_SIMILARITY}" in README
    assert f"fewer than {quality.MIN_CONTENT_WORDS_LONG} distinct content words" in README
    assert f"or {quality.MIN_CONTENT_WORDS_SHORT} (everything else)" in README
    assert f"≥ {quality.COPY_COVERAGE:.0%} of the content words" in README
    assert f"fewer than {quality.MIN_NEW_WORDS} new ones" in README
    assert f"With {quality.DOMINANCE_MIN_WORDS}+ content words" in README


def test_no_claims_about_an_unpublished_pypi_package():
    assert "uvx mcp-devils-advocate`" not in README.replace("`uvx mcp-devils-advocate` do not work", "")
    assert "img.shields.io/pypi" not in README
