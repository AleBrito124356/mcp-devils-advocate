"""mcp-devils-advocate — MCP server entry point.

Pure wiring: every tool, resource and prompt delegates to ``core``. The
state directory is ``~/.mcp-devils-advocate`` or the ``DEVILS_ADVOCATE_DIR``
environment variable if set.

Works with both major versions of the official MCP Python SDK:

* mcp 2.x — ``mcp.server.mcpserver.MCPServer`` (FastMCP was renamed);
* mcp 1.x — ``mcp.server.fastmcp.FastMCP``.

In 2.x only a ``ToolError`` raised by a tool reaches the model; any other
exception is treated as a crash and the client sees just "Error executing
tool <name>". The whole design of this server depends on the model reading
actionable validation errors ("item 2: near-duplicate of item 0 …"), so
every tool converts the ``ValueError`` raised by ``core`` into the SDK's
``ToolError``. Resources do the same with ``ResourceError``, and the prompt
reports bad arguments as an invalid-params protocol error.
"""

# No ``from __future__ import annotations`` here on purpose: some mcp 1.x
# releases (e.g. 1.9) inspect tool signatures with ``issubclass`` and crash on
# string annotations. Python >= 3.10 evaluates every annotation below natively.
import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from mcp.types import INVALID_PARAMS, ErrorData

try:  # mcp >= 2.0
    from mcp.server.mcpserver import MCPServer as _Server
    from mcp.server.mcpserver.exceptions import ResourceError, ToolError
    from mcp.shared.exceptions import MCPError

    SDK_MAJOR = 2

    def _invalid_params(message: str) -> Exception:
        return MCPError.from_error_data(ErrorData(code=INVALID_PARAMS, message=message))

except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server
    from mcp.server.fastmcp.exceptions import ResourceError, ToolError
    from mcp.shared.exceptions import McpError

    SDK_MAJOR = 1

    def _invalid_params(message: str) -> Exception:
        return McpError(ErrorData(code=INVALID_PARAMS, message=message))

from . import __version__
from .core import MODES, MODE_SUMMARIES, ReviewStore, protocol_prompt
from .memo import render_status_markdown

SERVER_NAME = "mcp-devils-advocate"

INSTRUCTIONS = (
    "Stress-test a claim or decision with enforced adversarial protocols. You do all the "
    "thinking; this server validates every submission, rejects junk, duplicates, "
    "restatements of the claim and copy-pasted answers, refuses to advance until each phase "
    "is complete, and compiles a deterministic verdict.\n"
    "Modes:\n"
    + "\n".join(f"- {mode}: {MODE_SUMMARIES[mode]}" for mode in MODES)
    + "\nWorkflow: start_review -> submit (once per phase, all items in one call) -> "
    "get_verdict -> export_report. Errors list every problem and nothing is saved, so fix "
    "and resubmit. get_review shows the current phase's instructions if you lose track. "
    "Resources: reviews:// (listing) and review://{review_id} (decision memo). "
    "Prompt: stress_test(claim, mode)."
)


def default_data_dir() -> Path:
    env_dir = os.environ.get("DEVILS_ADVOCATE_DIR")
    return Path(env_dir) if env_dir else Path.home() / ".mcp-devils-advocate"


@contextmanager
def _tool_errors() -> Iterator[None]:
    """Turn core's ValueError into ToolError so the model sees the full message."""
    try:
        yield
    except ValueError as exc:
        raise ToolError(str(exc)) from exc


@contextmanager
def _resource_errors() -> Iterator[None]:
    try:
        yield
    except ValueError as exc:
        raise ResourceError(str(exc)) from exc


def create_server(data_dir: str | os.PathLike | None = None) -> Any:
    """Build the MCP server over a ReviewStore rooted at ``data_dir``.

    ``data_dir`` defaults to ``$DEVILS_ADVOCATE_DIR`` or ``~/.mcp-devils-advocate``.
    """
    store = ReviewStore(data_dir if data_dir is not None else default_data_dir())
    options: dict[str, Any] = {"instructions": INSTRUCTIONS}
    if SDK_MAJOR >= 2:
        options["version"] = __version__
    server = _Server(SERVER_NAME, **options)
    server.store = store  # handy for embedding and tests

    @server.tool()
    def start_review(claim: str, mode: str, context: str = "") -> dict:
        """Start a structured stress-test of a claim or decision.

        You (the client) do all the thinking; this server enforces the protocol,
        validates every submission, and refuses to advance until each phase is
        genuinely complete. Pick the mode that fits the question:

        - devils_advocate: generate counterarguments (categorized, severity-rated),
          then rebut the severe ones honestly.
        - premortem: imagine the decision failed at a chosen horizon, list failure
          causes (likelihood x impact), then mitigate the high-risk ones.
        - assumptions: audit what the claim silently relies on (load-bearing?
          evidence level?), then design cheap tests for the unverified ones.
        - steelman: build the strongest honest case for the OPPOSING position,
          then concede or counter each point.
        - gauntlet: all four lenses on the same claim, one after another
          (devil's advocate -> assumptions -> premortem -> steelman), with a
          combined verdict. Use it for decisions that really matter.

        Submissions are quality-gated: junk padding, near-duplicate items,
        restating the claim and copying the item you answer are rejected.

        Args:
            claim: The claim or decision under review, stated plainly
                (e.g. "We should rewrite our backend in Rust").
            mode: One of: devils_advocate, premortem, assumptions, steelman, gauntlet.
            context: Optional background that matters for the review
                (constraints, stakes, prior discussion). It is repeated in every
                phase's instructions.

        Returns:
            dict with the new review_id, the phase list, and the exact
            instructions for the first phase: what to send, the item format,
            the minimums and the quality checks enforced.
        """
        with _tool_errors():
            return store.start_review(claim=claim, mode=mode, context=context)

    @server.tool()
    def submit(review_id: str, items: list[dict]) -> dict:
        """Submit items for the current phase of a review.

        Each item must match the item_format from the latest instructions.
        Validation is atomic: if any item is invalid (wrong fields, or it fails
        a quality check), nothing is saved and the error lists every problem to
        fix. When the phase's requirements are met the review advances and the
        next phase's instructions are returned; otherwise the response lists
        exactly what is still missing.

        Args:
            review_id: The review session id (e.g. "rev-x9k2").
            items: List of dicts matching the current phase's item_format.

        Returns:
            dict with status "in_progress" (plus a 'missing' list),
            "phase_complete" (plus 'next_phase' instructions), or "complete"
            (call get_verdict next).
        """
        with _tool_errors():
            return store.submit(review_id=review_id, items=items)

    @server.tool()
    def get_verdict(review_id: str) -> dict:
        """Compile the final report for a completed review.

        Only available once every phase is complete — otherwise it raises an
        error saying what is still missing. The report contains the claim, all
        items organized by phase (with rebuttals/mitigations/tests/responses
        attached to their targets), an aggregate risk score, and a deterministic
        assessment: "claim survives scrutiny", "claim needs revision", or
        "claim refuted" (rules documented in assessment_reason). Gauntlet
        reviews also include one sub-verdict per lens under 'lenses'.

        Args:
            review_id: The review session id (e.g. "rev-x9k2").

        Returns:
            dict — the compiled verdict report.
        """
        with _tool_errors():
            return store.get_verdict(review_id=review_id)

    @server.tool()
    def export_report(review_id: str, format: str = "markdown") -> dict:
        """Export a completed review as a shareable decision memo.

        'markdown' (default) gives a memo with the claim, context, assessment and
        its rule, every item with its rebuttal/mitigation/test/response, and a
        'Next actions' checklist (counterarguments that still hold, mitigations
        to carry out, tests to run, conceded points). 'json' gives the raw
        verdict report.

        Args:
            review_id: The review session id (e.g. "rev-x9k2").
            format: "markdown" (alias "md") or "json".

        Returns:
            dict with review_id, format, assessment and the rendered 'content'.
        """
        with _tool_errors():
            return store.export_report(review_id=review_id, format=format)

    @server.tool()
    def get_review(review_id: str) -> dict:
        """Where a review stands — use it to resume after losing track.

        Args:
            review_id: The review session id (e.g. "rev-x9k2").

        Returns:
            dict with status, current phase and step, items per phase, what the
            phase is still missing, and (if active) the current phase's full
            instructions.
        """
        with _tool_errors():
            return store.get_review(review_id=review_id)

    @server.tool()
    def list_reviews() -> dict:
        """List every review session (active, complete, and abandoned).

        Returns:
            dict with 'count', 'reviews' (id, claim snippet, mode, status,
            current phase, timestamps; newest first), the data directory, and
            'skipped' for any file in it that is not a valid review.
        """
        with _tool_errors():
            return store.list_reviews()

    @server.tool()
    def abandon_review(review_id: str, reason: str) -> dict:
        """Abandon an unfinished review that is no longer worth finishing.

        Abandoned reviews stay listed for the record but accept no further
        submissions and produce no verdict. Completed reviews cannot be
        abandoned — their verdict is final.

        Args:
            review_id: The review session id (e.g. "rev-x9k2").
            reason: Why the review is being abandoned (required, non-empty).

        Returns:
            dict confirming the abandonment.
        """
        with _tool_errors():
            return store.abandon_review(review_id=review_id, reason=reason)

    @server.resource(
        "reviews://",
        name="reviews",
        description="Every review (id, claim, mode, status, phase), newest first, as JSON.",
        mime_type="application/json",
    )
    def reviews_resource() -> str:
        with _resource_errors():
            return json.dumps(store.list_reviews(), indent=2, ensure_ascii=False)

    @server.resource(
        "review://{review_id}",
        name="review",
        description=(
            "Decision memo (Markdown) of a completed review, or its progress if it is "
            "still active or was abandoned."
        ),
        mime_type="text/markdown",
    )
    def review_resource(review_id: str) -> str:
        with _resource_errors():
            summary = store.get_review(review_id)
            if summary["status"] == "complete":
                return store.export_report(review_id)["content"]
            return render_status_markdown(summary)

    @server.prompt(
        name="stress_test",
        description=(
            "Stress-test a claim with one of the protocols: devils_advocate, premortem, "
            "assumptions, steelman or gauntlet."
        ),
    )
    def stress_test(claim: str, mode: str = "devils_advocate") -> str:
        try:
            return protocol_prompt(claim, mode)
        except ValueError as exc:
            # Only a protocol error carries its message out of a prompt in mcp 2.x.
            raise _invalid_params(str(exc)) from exc

    return server


def __getattr__(name: str) -> Any:
    # Backward compatibility: 0.1.0 exposed a module-level ``mcp`` server and
    # ``store``. They are now built on first access instead of at import time.
    if name in ("mcp", "store"):
        server = globals().get("_default_server")
        if server is None:
            server = globals()["_default_server"] = create_server()
        return server if name == "mcp" else server.store
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def main(data_dir: str | os.PathLike | None = None) -> None:
    """Run the stdio server (entry point of ``python -m mcp_devils_advocate.server``)."""
    create_server(data_dir).run()


if __name__ == "__main__":
    main()
