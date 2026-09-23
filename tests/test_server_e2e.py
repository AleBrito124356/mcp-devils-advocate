"""End-to-end MCP protocol tests.

Each test spawns the real server as a subprocess (``python -m
mcp_devils_advocate.server`` or the ``mcp-devils-advocate`` console script)
and talks to it over stdio with the official SDK client, exactly like Claude
Desktop or Claude Code would. The same file passes under mcp 1.x (FastMCP)
and mcp 2.x (MCPServer). Fully offline: the server makes no network calls
and DEVILS_ADVOCATE_DIR points at a temp dir.
"""

import json
import os
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

import pytest

mcp = pytest.importorskip("mcp")
anyio = pytest.importorskip("anyio")

from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

from mcp_devils_advocate.demo import CLAIM, script_for  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
TOOLS = {"start_review", "submit", "get_verdict", "export_report", "get_review", "list_reviews", "abandon_review"}


def is_error(result) -> bool:
    # mcp 2.x: is_error; mcp 1.x: isError
    return bool(getattr(result, "is_error", getattr(result, "isError", False)))


def text_of(result) -> str:
    return "\n".join(block.text for block in result.content if getattr(block, "type", "") == "text")


def payload(result) -> dict:
    assert not is_error(result), text_of(result)
    return json.loads(text_of(result))


def server_params(data_dir, command=None):
    env = {
        "DEVILS_ADVOCATE_DIR": str(data_dir),
        "PYTHONPATH": str(REPO),
        # keep the child quiet and portable
        "PYTHONUNBUFFERED": "1",
    }
    if command is None:
        return StdioServerParameters(command=sys.executable, args=["-m", "mcp_devils_advocate.server"], env=env)
    return StdioServerParameters(command=command, args=[], env=env)


SESSION_TIMEOUT = 90  # seconds: a hung transport fails the test instead of the whole run


def run_session(data_dir, scenario, command=None):
    async def main():
        with anyio.fail_after(SESSION_TIMEOUT):
            async with stdio_client(server_params(data_dir, command)) as (read, write):
                async with ClientSession(read, write) as session:
                    init = await session.initialize()
                    return await scenario(session, init)

    return anyio.run(main)


@pytest.fixture()
def data_dir(tmp_path):
    return tmp_path / "reviews"


def test_tools_resources_and_prompt_are_listed(data_dir):
    async def scenario(session, init):
        tools = {t.name for t in (await session.list_tools()).tools}
        prompts = [p.name for p in (await session.list_prompts()).prompts]
        resources = [str(r.uri) for r in (await session.list_resources()).resources]
        templates_result = await session.list_resource_templates()
        templates = [
            getattr(t, "uri_template", None) or getattr(t, "uriTemplate", None)
            for t in getattr(templates_result, "resource_templates", None)
            or getattr(templates_result, "resourceTemplates")
        ]
        server_name = getattr(init, "server_info", None) or getattr(init, "serverInfo")
        return tools, prompts, resources, templates, server_name.name, init.instructions

    tools, prompts, resources, templates, name, instructions = run_session(data_dir, scenario)
    assert tools == TOOLS
    assert prompts == ["stress_test"]
    assert resources == ["reviews://"]
    assert templates == ["review://{review_id}"]
    assert name == "mcp-devils-advocate"
    assert "gauntlet" in instructions and "start_review" in instructions


def test_full_devils_advocate_flow_over_stdio(data_dir):
    async def scenario(session, _init):
        started = payload(await session.call_tool("start_review", {"claim": CLAIM, "mode": "devils_advocate"}))
        rid = started["review_id"]
        steps = [started["instructions"]["phase"]]
        for batch in script_for("devils_advocate"):
            out = payload(await session.call_tool("submit", {"review_id": rid, "items": batch}))
            steps.append(out["status"])
        verdict = payload(await session.call_tool("get_verdict", {"review_id": rid}))
        memo = payload(await session.call_tool("export_report", {"review_id": rid}))
        as_json = payload(await session.call_tool("export_report", {"review_id": rid, "format": "json"}))
        resource = await session.read_resource(f"review://{rid}")
        listing = await session.read_resource("reviews://")
        status = payload(await session.call_tool("get_review", {"review_id": rid}))
        return rid, steps, verdict, memo, as_json, resource, listing, status

    rid, steps, verdict, memo, as_json, resource, listing, status = run_session(data_dir, scenario)
    assert steps == ["counterarguments", "phase_complete", "complete"]
    assert verdict["assessment"] == "claim refuted"
    assert verdict["risk_score"]["value"] == 2
    assert memo["format"] == "markdown"
    assert memo["content"].startswith(f"# Decision memo: {CLAIM}")
    assert json.loads(as_json["content"])["assessment"] == "claim refuted"
    assert resource.contents[0].text == memo["content"]
    listed = json.loads(listing.contents[0].text)
    assert listed["reviews"][0]["review_id"] == rid
    assert status["status"] == "complete"
    # state was persisted where DEVILS_ADVOCATE_DIR pointed
    assert (data_dir / f"{rid}.json").exists()


def test_validation_errors_reach_the_model_verbatim(data_dir):
    """Under mcp 2.x a bare ValueError would arrive as just 'Error executing tool submit'."""

    async def scenario(session, _init):
        missing = await session.call_tool("submit", {"review_id": "rev-zzzz", "items": [{"text": "x"}]})
        rid = payload(await session.call_tool("start_review", {"claim": CLAIM, "mode": "devils_advocate"}))["review_id"]
        junk = [{"text": "x" * 30, "category": c, "severity": 5} for c in ("evidence", "scope", "base_rates")]
        gamed = await session.call_tool("submit", {"review_id": rid, "items": junk})
        bad_mode = await session.call_tool("start_review", {"claim": CLAIM, "mode": "socratic"})
        early = await session.call_tool("get_verdict", {"review_id": rid})
        after = payload(await session.call_tool("get_review", {"review_id": rid}))
        return missing, gamed, bad_mode, early, after

    missing, gamed, bad_mode, early, after = run_session(data_dir, scenario)
    assert is_error(missing)
    assert "Review 'rev-zzzz' not found" in text_of(missing)
    assert "list_reviews()" in text_of(missing)

    assert is_error(gamed)
    text = text_of(gamed)
    assert "nothing was saved" in text
    assert "item 2: 'text' has too little information" in text

    assert is_error(bad_mode) and "Valid modes: devils_advocate" in text_of(bad_mode)
    assert is_error(early) and "is not complete" in text_of(early)
    assert after["items_per_phase"]["counterarguments"] == 0


def test_prompt_and_active_review_resource(data_dir):
    async def scenario(session, _init):
        prompt = await session.get_prompt("stress_test", {"claim": CLAIM, "mode": "gauntlet"})
        rid = payload(
            await session.call_tool("start_review", {"claim": CLAIM, "mode": "gauntlet", "context": "Tight budget."})
        )["review_id"]
        progress = await session.read_resource(f"review://{rid}")
        abandoned = payload(await session.call_tool("abandon_review", {"review_id": rid, "reason": "superseded"}))
        errors = []
        for call in (
            lambda: session.get_prompt("stress_test", {"claim": CLAIM, "mode": "socratic"}),
            lambda: session.read_resource("review://rev-zzzz"),
        ):
            try:
                await call()
                errors.append("no error raised")
            except Exception as exc:  # McpError (1.x) / MCPError (2.x)
                errors.append(str(exc))
        return prompt, progress, abandoned, errors

    prompt, progress, abandoned, errors = run_session(data_dir, scenario)
    assert "Unknown mode 'socratic'" in errors[0]
    assert "Review 'rev-zzzz' not found" in errors[1]
    message = prompt.messages[0]
    assert message.role == "user"
    assert "mode='gauntlet'" in message.content.text
    assert CLAIM in message.content.text
    assert progress.contents[0].text.startswith(f"# Review in progress: {CLAIM}")
    assert "(step 1/9)" in progress.contents[0].text
    assert abandoned["status"] == "abandoned"


def test_gauntlet_is_exposed_through_mcp(data_dir):
    async def scenario(session, _init):
        started = payload(await session.call_tool("start_review", {"claim": CLAIM, "mode": "gauntlet"}))
        rid = started["review_id"]
        for batch in script_for("gauntlet"):
            out = payload(await session.call_tool("submit", {"review_id": rid, "items": batch}))
        verdict = payload(await session.call_tool("get_verdict", {"review_id": rid}))
        return started, out, verdict

    started, last, verdict = run_session(data_dir, scenario)
    assert len(started["phases"]) == 9
    assert started["instructions"]["lens"] == "devils_advocate"
    assert last["status"] == "complete"
    assert set(verdict["lenses"]) == {"devils_advocate", "assumptions", "premortem", "steelman"}
    assert verdict["assessment"] == "claim needs revision"


def _console_script():
    scripts = Path(sysconfig.get_path("scripts"))
    for name in ("mcp-devils-advocate.exe", "mcp-devils-advocate"):
        candidate = scripts / name
        if candidate.exists():
            return str(candidate)
    return shutil.which("mcp-devils-advocate")


def test_console_script_serves_with_no_arguments(data_dir):
    script = _console_script()
    if script is None:
        pytest.skip("package not installed: no mcp-devils-advocate console script")

    async def scenario(session, _init):
        return {t.name for t in (await session.list_tools()).tools}

    assert run_session(data_dir, scenario, command=script) == TOOLS


def test_server_module_imports_and_matches_the_installed_sdk():
    from importlib.metadata import version

    from mcp_devils_advocate import server

    assert server.SDK_MAJOR == int(version("mcp").split(".")[0])


def test_import_is_side_effect_free_and_legacy_attributes_still_work(data_dir):
    """0.1.0 exposed module-level ``server.mcp``/``server.store``; now built lazily."""
    code = (
        "import os, sys\n"
        "from mcp_devils_advocate import server\n"
        "d = os.environ['DEVILS_ADVOCATE_DIR']\n"
        "print(os.path.exists(d))\n"
        "print(type(server.mcp).__name__, server.store.data_dir == server.mcp.store.data_dir)\n"
        "print(os.path.exists(d))\n"
    )
    env = {**os.environ, "DEVILS_ADVOCATE_DIR": str(data_dir), "PYTHONPATH": str(REPO)}
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=60)
    assert proc.returncode == 0, proc.stderr
    before, built, after = proc.stdout.split("\n")[:3]
    assert before == "False"
    assert built in ("MCPServer True", "FastMCP True")
    assert after == "True"
