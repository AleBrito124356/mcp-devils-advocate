"""mcp-devils-advocate — MCP server entry point.

Pure wiring: every tool delegates to core.ReviewStore. The state directory
is ``~/.mcp-devils-advocate`` or the ``DEVILS_ADVOCATE_DIR`` environment
variable if set.
"""

from __future__ import annotations

import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .core import ReviewStore

_env_dir = os.environ.get("DEVILS_ADVOCATE_DIR")
DATA_DIR = Path(_env_dir) if _env_dir else Path.home() / ".mcp-devils-advocate"

store = ReviewStore(DATA_DIR)
mcp = FastMCP("mcp-devils-advocate")


@mcp.tool()
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

    Args:
        claim: The claim or decision under review, stated plainly
            (e.g. "We should rewrite our backend in Rust").
        mode: One of: devils_advocate, premortem, assumptions, steelman.
        context: Optional background that matters for the review
            (constraints, stakes, prior discussion).

    Returns:
        dict with the new review_id and the exact instructions for the first
        phase: what to send, the item format, and the minimums enforced.
    """
    return store.start_review(claim=claim, mode=mode, context=context)


@mcp.tool()
def submit(review_id: str, items: list[dict]) -> dict:
    """Submit items for the current phase of a review.

    Each item must match the item_format from the latest instructions.
    Validation is atomic: if any item is invalid, nothing is saved and the
    error lists every problem to fix. When the phase's requirements are met
    the review advances and the next phase's instructions are returned;
    otherwise the response lists exactly what is still missing.

    Args:
        review_id: The review session id (e.g. "rev-x9k2").
        items: List of dicts matching the current phase's item_format.

    Returns:
        dict with status "in_progress" (plus a 'missing' list),
        "phase_complete" (plus 'next_phase' instructions), or "complete"
        (call get_verdict next).
    """
    return store.submit(review_id=review_id, items=items)


@mcp.tool()
def get_verdict(review_id: str) -> dict:
    """Compile the final report for a completed review.

    Only available once every phase is complete — otherwise it raises an
    error saying what is still missing. The report contains the claim, all
    items organized by phase (with rebuttals/mitigations/tests/responses
    attached to their targets), an aggregate risk score, and a deterministic
    assessment: "claim survives scrutiny", "claim needs revision", or
    "claim refuted" (rules documented in assessment_reason).

    Args:
        review_id: The review session id (e.g. "rev-x9k2").

    Returns:
        dict — the compiled verdict report.
    """
    return store.get_verdict(review_id=review_id)


@mcp.tool()
def list_reviews() -> dict:
    """List every review session (active, complete, and abandoned).

    Returns:
        dict with 'count', 'reviews' (id, claim snippet, mode, status,
        current phase, timestamps; newest first) and the data directory.
    """
    return store.list_reviews()


@mcp.tool()
def abandon_review(review_id: str, reason: str) -> dict:
    """Abandon a review that is no longer worth finishing.

    Abandoned reviews stay listed for the record but accept no further
    submissions and produce no verdict.

    Args:
        review_id: The review session id (e.g. "rev-x9k2").
        reason: Why the review is being abandoned (required, non-empty).

    Returns:
        dict confirming the abandonment.
    """
    return store.abandon_review(review_id=review_id, reason=reason)


def main() -> None:
    """Entry point for the console script."""
    mcp.run()


if __name__ == "__main__":
    main()
