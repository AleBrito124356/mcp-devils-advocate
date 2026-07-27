# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] — 2026-07-26

### Added

- Five MCP tools: `start_review`, `submit`, `get_verdict`, `list_reviews`, `abandon_review`.
- Four enforced review protocols — `devils_advocate`, `premortem`, `assumptions` and `steelman` — each a phase machine with its own minimum counts, categories, severity/likelihood scales and length floors.
- The server never generates content: it validates every submission atomically (nothing is saved if any item is invalid), returns actionable errors, and refuses to advance a phase until its requirements are genuinely met.
- Dependent phases are auto-skipped when they have no targets (e.g. no counterargument reached severity 3, no failure cause scored ≥9).
- Deterministic verdicts: an aggregate risk score per mode plus `claim survives scrutiny` / `claim needs revision` / `claim refuted`, with the applied rules spelled out in `assessment_reason`.
- Persistence as one JSON file per review in `~/.mcp-devils-advocate`, overridable with the `DEVILS_ADVOCATE_DIR` environment variable.
- 56 tests covering phase transitions, validation errors, the full flow of all four modes up to the verdict rules, and persistence — running on pure stdlib, without `mcp` installed.

[0.1.0]: https://github.com/AleBrito124356/mcp-devils-advocate/releases/tag/v0.1.0
