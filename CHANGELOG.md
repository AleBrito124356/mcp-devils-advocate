# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] — Unreleased

### Fixed

- **The server did not start on a fresh install.** `mcp>=1.2.0` resolved to mcp 2.x, which removed `mcp.server.fastmcp`, so both the console script and `python -m mcp_devils_advocate.server` crashed with `ModuleNotFoundError`. The server now runs on both SDK generations: `MCPServer` on mcp 2.x and `FastMCP` on 1.x. The dependency is now `mcp>=1.5.0,<3`.
- **Validation errors now reach the model under mcp 2.x.** 2.x treats a plain exception in a tool as a crash and sends only `Error executing tool submit`. Every tool now raises the SDK's `ToolError`, so the full message ("item 2: … — nothing was saved") gets through. Resources use `ResourceError`, and the prompt raises an invalid-params protocol error.
- Tool registration crashed on mcp 1.9 (`issubclass() arg 1 must be a class`) because of string annotations in `server.py`. 0.1.0 had the same latent bug.
- `abandon_review` accepted a **completed** review, and after that its verdict could never be read again. It now refuses: a verdict is final.
- `list_reviews` raised `KeyError` when the data directory held a `rev-*.json` that was valid JSON but not a review. Such files are now reported under `skipped` and the rest of the listing is returned. Corrupt files opened by id give a clear error instead of a traceback.
- `context` passed to `start_review` was stored but never shown to the model. It is now repeated in every phase's instructions, together with the claim.
- Reviews created in the same second were listed in id order instead of creation order.

### Added

- **Anti-gaming quality gate** (`quality.py`, pure stdlib, deterministic). It rejects low-information padding (`"x" * 30`), near-duplicate items (Jaccard ≥ 0.8, within the batch and against saved items), counterarguments / steelman points / justifications / `counter` responses that only restate the claim, and rebuttals / mitigations / tests / responses that parrot the item they answer. Errors name the item and the reason, and the batch stays atomic. Every phase's instructions list the rules under `quality_checks`. The four exploits from the 0.1.0 audit (all of which ended in "claim survives scrutiny") are regression tests.
- **`gauntlet` mode.** All four lenses run on one claim in nine phases (devil's advocate → assumptions → premortem → steelman). Each lens gets its own sub-verdict, and a documented combined rule decides the result: refuted if ≥2 lenses refute; needs revision if exactly 1 refutes or ≥2 need revision; survives otherwise.
- **Decision memo export.** New `export_report(review_id, format="markdown"|"json")` tool. The Markdown memo contains the claim, context, assessment and its rule, every item with its answer attached, and a **Next actions** checklist: counterarguments that still hold, mitigations to carry out, tests to run, and conceded points.
- `get_review(review_id)` tool: status, step, per-phase counts, what is missing and the current instructions, for resuming a long review.
- MCP resources `reviews://` (listing) and `review://{review_id}` (memo, or a progress page), plus the MCP prompt `stress_test(claim, mode)`. Server `instructions` describe the modes and the workflow.
- **CLI** on the existing console script: `list`, `show`, `report` and `demo`. With no arguments it still runs the stdio server. `python -m mcp_devils_advocate` works too. `demo` runs a scripted review offline and deterministically, and the README quotes its output, checked by a test.
- `create_server(data_dir)` factory, for embedding and tests.
- End-to-end tests start the real server over stdio with the official SDK client. The whole suite (141 tests) passes on mcp 1.5.0, 1.9.0, 1.30.0 and 2.2.0.

### Changed

- The README Quickstart no longer claims the package is on PyPI (it has not been released yet). It installs from GitHub with `uvx --from git+…` or `pip install git+…`, and the PyPI badges are gone.
- The console script entry point moved from `server:main` to `cli:console_main`. Running it with no arguments behaves as before.
- `mcp_devils_advocate.server.mcp` / `.store` are now built when first accessed, not at import time.
- Stored reviews gain a `created_ns` field. Files written by 0.1.0 still load.

## [0.1.0] — 2026-07-26

### Added

- Five MCP tools: `start_review`, `submit`, `get_verdict`, `list_reviews`, `abandon_review`.
- Four enforced review protocols — `devils_advocate`, `premortem`, `assumptions` and `steelman` — each a phase machine with its own minimum counts, categories, severity/likelihood scales and length floors.
- The server never generates content: it validates every submission atomically (nothing is saved if any item is invalid), returns actionable errors, and refuses to advance a phase until its requirements are genuinely met.
- Dependent phases are auto-skipped when they have no targets (e.g. no counterargument reached severity 3, no failure cause scored ≥9).
- Deterministic verdicts: an aggregate risk score per mode plus `claim survives scrutiny` / `claim needs revision` / `claim refuted`, with the applied rules spelled out in `assessment_reason`.
- Persistence as one JSON file per review in `~/.mcp-devils-advocate`, overridable with the `DEVILS_ADVOCATE_DIR` environment variable.
- 56 tests covering phase transitions, validation errors, the full flow of all four modes up to the verdict rules, and persistence — running on pure stdlib, without `mcp` installed.

[0.2.0]: https://github.com/AleBrito124356/mcp-devils-advocate/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/AleBrito124356/mcp-devils-advocate/releases/tag/v0.1.0
