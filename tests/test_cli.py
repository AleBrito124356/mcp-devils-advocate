"""CLI tests: run ``python -m mcp_devils_advocate`` as a real subprocess.

None of these commands import the MCP SDK and none touch the network or the
user's home directory (DEVILS_ADVOCATE_DIR points at a temp dir).
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from mcp_devils_advocate import __version__
from mcp_devils_advocate.cli import build_parser

REPO = Path(__file__).resolve().parents[1]


def run(*args, data_dir, check=True):
    env = {**os.environ, "DEVILS_ADVOCATE_DIR": str(data_dir), "PYTHONPATH": str(REPO)}
    env.pop("PYTHONIOENCODING", None)  # the CLI must emit UTF-8 on its own
    proc = subprocess.run(
        [sys.executable, "-m", "mcp_devils_advocate", *args],
        capture_output=True,
        env=env,
        cwd=REPO,
        timeout=60,
    )
    out, err = proc.stdout.decode("utf-8"), proc.stderr.decode("utf-8")
    if check:
        assert proc.returncode == 0, err
    return proc.returncode, out, err


@pytest.fixture()
def data_dir(tmp_path):
    return tmp_path / "reviews"


def test_version(data_dir):
    _, out, _ = run("--version", data_dir=data_dir)
    assert out.strip() == f"mcp-devils-advocate {__version__}"


def test_demo_is_offline_deterministic_and_leaves_nothing_behind(data_dir):
    _, first, _ = run("demo", data_dir=data_dir)
    _, second, _ = run("demo", data_dir=data_dir)
    assert first == second
    assert "Offline demo" in first
    assert "== Phase 1/2: counterarguments ==" in first
    assert "== Phase 2/2: rebuttals ==" in first
    assert "REJECTED — Invalid submission — nothing was saved" in first
    assert "is a near-duplicate of item 0 (similarity 1.00)" in first
    assert "get_verdict(rev-ess4) -> claim refuted (risk score 2" in first
    assert "# Decision memo: We should rewrite our backend in Rust" in first
    assert "## Next actions" in first
    # the default demo runs in a temp dir: the data dir was never created
    assert not data_dir.exists()


def test_gauntlet_demo(data_dir):
    _, out, _ = run("demo", "--mode", "gauntlet", "--memo-only", data_dir=data_dir)
    assert out.startswith("# Decision memo:")
    assert "mode `gauntlet`" in out
    assert "**Assessment:** **claim needs revision**" in out
    assert "| Premortem | claim needs revision |" in out


def test_list_show_and_report_on_a_kept_demo_review(data_dir, tmp_path):
    run("demo", "--memo-only", "--data-dir", str(data_dir), data_dir=tmp_path / "unused")
    assert (data_dir / "rev-ess4.json").exists()

    _, listing, _ = run("list", data_dir=data_dir)
    assert "rev-ess4" in listing and "devils_advocate" in listing and "complete" in listing
    assert "1 review(s)" in listing

    _, raw, _ = run("list", "--json", data_dir=data_dir)
    assert json.loads(raw)["reviews"][0]["review_id"] == "rev-ess4"

    _, shown, _ = run("show", "rev-ess4", data_dir=data_dir)
    assert "**Status:** complete" in shown

    out_file = tmp_path / "memo.md"
    _, msg, _ = run("report", "rev-ess4", "--format", "md", "-o", str(out_file), data_dir=data_dir)
    assert f"to {out_file}" in msg
    memo = out_file.read_text(encoding="utf-8")
    assert memo.startswith("# Decision memo: We should rewrite our backend in Rust")
    assert "**Assessment:** **claim refuted**" in memo

    _, js, _ = run("report", "rev-ess4", "--format", "json", data_dir=data_dir)
    report = json.loads(js)
    assert report["assessment"] == "claim refuted"
    assert report["risk_score"]["value"] == 2


def test_show_active_review_prints_current_instructions(data_dir):
    from mcp_devils_advocate.core import ReviewStore

    rid = ReviewStore(data_dir).start_review("We should adopt a four-day work week", "premortem")["review_id"]
    _, out, _ = run("show", rid, data_dir=data_dir)
    assert "# Review in progress: We should adopt a four-day work week" in out
    assert "## Current phase instructions" in out
    assert '"phase": "setup"' in out


def test_errors_exit_1_with_a_message(data_dir):
    code, _, err = run("report", "rev-zzzz", data_dir=data_dir, check=False)
    assert code == 1
    assert "error: Review 'rev-zzzz' not found" in err
    code, _, err = run("show", "not-an-id", data_dir=data_dir, check=False)
    assert code == 1 and "Invalid review id" in err


def test_list_warns_about_malformed_files(data_dir):
    data_dir.mkdir()
    (data_dir / "rev-zzzz.json").write_text("{}", encoding="utf-8")
    _, out, err = run("list", data_dir=data_dir)
    assert "No reviews in" in out
    assert "warning: skipped rev-zzzz.json: missing keys" in err


def test_no_command_means_serve():
    args = build_parser().parse_args([])
    assert args.command is None
    args = build_parser().parse_args(["--data-dir", "A", "list"])
    assert args.data_dir == "A"
    args = build_parser().parse_args(["list", "--data-dir", "B"])
    assert args.data_dir == "B"
