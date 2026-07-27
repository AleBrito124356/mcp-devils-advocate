"""Core logic for mcp-devils-advocate.

A state machine that stress-tests the reasoning of the LLM client through
four enforced, structured protocols: devil's advocate, premortem analysis,
assumption audit, and steelmanning.

The server NEVER generates content. The client LLM does all the thinking;
this module validates every submission against the rules of the current
phase, refuses to advance until the phase is genuinely complete, and
compiles the final verdict report with a documented assessment.

Pure Python stdlib — no external dependencies. Persistence is one JSON
file per review inside the data directory handed to ``ReviewStore``.
"""

from __future__ import annotations

import json
import os
import random
import re
import string
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Protocol constants
# ---------------------------------------------------------------------------

MODES = ("devils_advocate", "premortem", "assumptions", "steelman")

COUNTERARGUMENT_CATEGORIES = (
    "evidence",
    "incentives",
    "base_rates",
    "alternatives",
    "second_order",
    "scope",
)
REBUTTAL_VERDICTS = ("holds", "partially_holds", "refuted")
EVIDENCE_LEVELS = ("verified", "partial", "none")
RESIDUAL_RISKS = ("low", "medium", "high")
STANCES = ("concede", "counter")

MIN_COUNTERARGUMENTS = 3
MIN_DISTINCT_CATEGORIES = 2
MIN_FAILURE_CAUSES = 4
MIN_ASSUMPTIONS = 4
MIN_STEELMAN_POINTS = 3
MIN_CLAIM_LENGTH = 10

LONG_TEXT_MIN = 30   # counterarguments and steelman points (spec-mandated)
SHORT_TEXT_MIN = 20  # every other free-text field

REBUTTAL_SEVERITY_THRESHOLD = 3   # severity >= 3 -> rebuttal required
MITIGATION_SCORE_THRESHOLD = 9    # likelihood * impact >= 9 -> mitigation required

MODE_PHASES: dict[str, tuple[str, ...]] = {
    "devils_advocate": ("counterarguments", "rebuttals"),
    "premortem": ("setup", "failure_causes", "mitigations"),
    "assumptions": ("assumptions", "tests"),
    "steelman": ("strongest_case", "responses"),
}

# Phases whose items reference items produced by an earlier phase.
_REFERENCE_PHASES = ("rebuttals", "mitigations", "tests", "responses")

ASSESS_SURVIVES = "claim survives scrutiny"
ASSESS_REVISE = "claim needs revision"
ASSESS_REFUTED = "claim refuted"

_ID_PATTERN = re.compile(r"^rev-[a-z0-9]{4}$")

CATEGORY_MEANINGS = {
    "evidence": "the supporting evidence is weak, cherry-picked, or contradicted",
    "incentives": "who benefits from believing this — motivated reasoning, misaligned incentives",
    "base_rates": "how often do similar attempts actually succeed",
    "alternatives": "a different option achieves the same goal better or cheaper",
    "second_order": "downstream consequences that undermine the original goal",
    "scope": "the claim overreaches — it only holds under narrower conditions",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _snippet(text: str, limit: int = 70) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


# ---------------------------------------------------------------------------
# Item validation helpers — each appends actionable messages to ``errors``
# ---------------------------------------------------------------------------


def _err(errors: list[str], idx: int, msg: str) -> None:
    errors.append(f"item {idx}: {msg}")


def _get_str(item: dict, field: str, min_len: int, errors: list[str], idx: int) -> str | None:
    value = item.get(field)
    if not isinstance(value, str) or len(value.strip()) < min_len:
        if isinstance(value, str):
            got = f"got {len(value.strip())} characters"
        elif value is None:
            got = "field is missing"
        else:
            got = f"got {type(value).__name__}"
        _err(errors, idx, f"'{field}' must be a string of at least {min_len} characters ({got})")
        return None
    return value.strip()


def _get_choice(item: dict, field: str, choices: tuple[str, ...], errors: list[str], idx: int) -> str | None:
    value = item.get(field)
    if value not in choices:
        _err(errors, idx, f"'{field}' must be one of: {', '.join(choices)} (got {value!r})")
        return None
    return value


def _get_int(item: dict, field: str, lo: int, hi: int, errors: list[str], idx: int) -> int | None:
    value = item.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or not (lo <= value <= hi):
        _err(errors, idx, f"'{field}' must be an integer between {lo} and {hi} (got {value!r})")
        return None
    return value


def _get_bool(item: dict, field: str, errors: list[str], idx: int) -> bool | None:
    value = item.get(field)
    if not isinstance(value, bool):
        _err(errors, idx, f"'{field}' must be a boolean true/false (got {value!r})")
        return None
    return value


# ---------------------------------------------------------------------------
# ReviewStore — the public API used by server.py and the tests
# ---------------------------------------------------------------------------


class ReviewStore:
    """Manages review sessions persisted as JSON files inside ``data_dir``.

    ``data_dir`` is normally ``~/.mcp-devils-advocate`` or the value of the
    ``DEVILS_ADVOCATE_DIR`` environment variable (resolved by the caller,
    typically server.py, and passed in as a parameter).
    """

    def __init__(self, data_dir: str | os.PathLike | None = None) -> None:
        if data_dir is None:
            env = os.environ.get("DEVILS_ADVOCATE_DIR")
            data_dir = Path(env) if env else Path.home() / ".mcp-devils-advocate"
        self.data_dir = Path(data_dir).expanduser()
        self.data_dir.mkdir(parents=True, exist_ok=True)

    # -- persistence --------------------------------------------------------

    def _path(self, review_id: str) -> Path:
        return self.data_dir / f"{review_id}.json"

    def _load(self, review_id: str) -> dict:
        if not isinstance(review_id, str) or not _ID_PATTERN.match(review_id):
            raise ValueError(
                f"Invalid review id {review_id!r} — ids look like 'rev-x9k2'. "
                "Call list_reviews() to see existing reviews."
            )
        path = self._path(review_id)
        if not path.exists():
            raise ValueError(
                f"Review '{review_id}' not found — call list_reviews() to see existing "
                "reviews, or start_review() to create one."
            )
        return json.loads(path.read_text(encoding="utf-8"))

    def _save(self, review: dict) -> None:
        path = self._path(review["id"])
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(review, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

    def _new_id(self) -> str:
        alphabet = string.ascii_lowercase + string.digits
        while True:
            review_id = "rev-" + "".join(random.choices(alphabet, k=4))
            if not self._path(review_id).exists():
                return review_id

    # -- tools --------------------------------------------------------------

    def start_review(self, claim: str, mode: str, context: str = "") -> dict:
        """Create a review session and return the first phase's instructions."""
        if not isinstance(claim, str) or len(claim.strip()) < MIN_CLAIM_LENGTH:
            raise ValueError(
                f"'claim' must be a meaningful statement of at least {MIN_CLAIM_LENGTH} "
                "characters, e.g. 'We should rewrite our backend in Rust'."
            )
        if mode not in MODES:
            raise ValueError(f"Unknown mode {mode!r}. Valid modes: {', '.join(MODES)}.")
        review = {
            "id": self._new_id(),
            "claim": claim.strip(),
            "mode": mode,
            "context": context.strip() if isinstance(context, str) else "",
            "status": "active",
            "phase": MODE_PHASES[mode][0],
            "phase_index": 0,
            "phases": {phase: [] for phase in MODE_PHASES[mode]},
            "skipped_phases": [],
            "horizon": None,
            "abandon_reason": None,
            "created_at": _utc_now(),
            "updated_at": _utc_now(),
        }
        self._save(review)
        return {
            "review_id": review["id"],
            "claim": review["claim"],
            "mode": mode,
            "status": "active",
            "instructions": self._instructions(review),
        }

    def submit(self, review_id: str, items: list[dict]) -> dict:
        """Validate ``items`` against the current phase; advance when complete."""
        review = self._load(review_id)
        if review["status"] == "abandoned":
            raise ValueError(
                f"Review '{review_id}' was abandoned "
                f"(reason: {review['abandon_reason']}) — start a new review."
            )
        if review["status"] == "complete":
            raise ValueError(
                f"Review '{review_id}' is already complete — call "
                f"get_verdict('{review_id}') for the report, or start a new review."
            )

        cleaned = self._validate_batch(review, items)
        current_phase = review["phase"]
        review["phases"][current_phase].extend(cleaned)
        review["updated_at"] = _utc_now()

        missing = self._completion_missing(review)
        if missing:
            self._save(review)
            return {
                "review_id": review["id"],
                "status": "in_progress",
                "phase": current_phase,
                "accepted": len(cleaned),
                "items_in_phase": len(review["phases"][current_phase]),
                "missing": missing,
            }

        self._advance(review)
        self._save(review)
        if review["status"] == "complete":
            return {
                "review_id": review["id"],
                "status": "complete",
                "completed_phase": current_phase,
                "accepted": len(cleaned),
                "skipped_phases": review["skipped_phases"],
                "message": (
                    f"All phases complete. Call get_verdict('{review['id']}') "
                    "to compile the final report."
                ),
            }
        return {
            "review_id": review["id"],
            "status": "phase_complete",
            "completed_phase": current_phase,
            "accepted": len(cleaned),
            "next_phase": self._instructions(review),
        }

    def get_verdict(self, review_id: str) -> dict:
        """Compile the final report — only available once every phase is complete."""
        review = self._load(review_id)
        if review["status"] == "abandoned":
            raise ValueError(
                f"Review '{review_id}' was abandoned "
                f"(reason: {review['abandon_reason']}) — no verdict is available."
            )
        if review["status"] != "complete":
            missing = self._completion_missing(review)
            raise ValueError(
                f"Review '{review_id}' is not complete — phase '{review['phase']}' "
                f"still needs: {'; '.join(missing)}. Use submit() to finish it."
            )

        builders = {
            "devils_advocate": self._verdict_devils_advocate,
            "premortem": self._verdict_premortem,
            "assumptions": self._verdict_assumptions,
            "steelman": self._verdict_steelman,
        }
        phases, risk_score, assessment, reason = builders[review["mode"]](review)
        report = {
            "review_id": review["id"],
            "claim": review["claim"],
            "mode": review["mode"],
            "context": review["context"],
            "created_at": review["created_at"],
            "completed_at": review["updated_at"],
            "skipped_phases": review["skipped_phases"],
            "phases": phases,
            "risk_score": risk_score,
            "assessment": assessment,
            "assessment_reason": reason,
        }
        if review["mode"] == "premortem":
            report["horizon"] = review["horizon"]
        return report

    def list_reviews(self) -> dict:
        """Summaries of every review in the data directory (newest first)."""
        reviews = []
        for path in sorted(self.data_dir.glob("rev-*.json")):
            try:
                review = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            reviews.append(
                {
                    "review_id": review["id"],
                    "claim": _snippet(review["claim"]),
                    "mode": review["mode"],
                    "status": review["status"],
                    "phase": review["phase"],
                    "created_at": review["created_at"],
                    "updated_at": review["updated_at"],
                }
            )
        reviews.sort(key=lambda r: r["created_at"], reverse=True)
        return {"count": len(reviews), "reviews": reviews, "data_dir": str(self.data_dir)}

    def abandon_review(self, review_id: str, reason: str) -> dict:
        """Mark a review as abandoned; it can no longer be submitted to."""
        review = self._load(review_id)
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError(
                "'reason' must be a non-empty string explaining why the review is abandoned."
            )
        if review["status"] == "abandoned":
            raise ValueError(
                f"Review '{review_id}' is already abandoned "
                f"(reason: {review['abandon_reason']})."
            )
        review["status"] = "abandoned"
        review["abandon_reason"] = reason.strip()
        review["phase"] = None
        review["updated_at"] = _utc_now()
        self._save(review)
        return {
            "review_id": review["id"],
            "status": "abandoned",
            "reason": review["abandon_reason"],
            "message": f"Review '{review['id']}' abandoned. Start a new review when ready.",
        }

    # -- phase machinery ----------------------------------------------------

    def _phase_targets(self, review: dict, phase: str) -> list[tuple[int, dict]]:
        """(index, item) pairs from the earlier phase that ``phase`` must cover."""
        if phase == "rebuttals":
            return [
                (i, c)
                for i, c in enumerate(review["phases"]["counterarguments"])
                if c["severity"] >= REBUTTAL_SEVERITY_THRESHOLD
            ]
        if phase == "mitigations":
            return [
                (i, c)
                for i, c in enumerate(review["phases"]["failure_causes"])
                if c["likelihood"] * c["impact"] >= MITIGATION_SCORE_THRESHOLD
            ]
        if phase == "tests":
            return [
                (i, a)
                for i, a in enumerate(review["phases"]["assumptions"])
                if a["load_bearing"] and a["evidence"] != "verified"
            ]
        if phase == "responses":
            return list(enumerate(review["phases"]["strongest_case"]))
        return []

    def _pending_targets(self, review: dict, phase: str) -> list[tuple[int, dict]]:
        covered = {item["index"] for item in review["phases"][phase]}
        return [(i, t) for i, t in self._phase_targets(review, phase) if i not in covered]

    def _completion_missing(self, review: dict) -> list[str]:
        """Human-readable list of what the current phase still needs (empty = done)."""
        phase = review["phase"]
        items = review["phases"][phase]
        missing: list[str] = []
        if phase == "counterarguments":
            if len(items) < MIN_COUNTERARGUMENTS:
                missing.append(
                    f"need at least {MIN_COUNTERARGUMENTS} counterarguments (have {len(items)})"
                )
            categories = {item["category"] for item in items}
            if len(categories) < MIN_DISTINCT_CATEGORIES:
                have = ", ".join(sorted(categories)) if categories else "none"
                missing.append(
                    f"counterarguments must span at least {MIN_DISTINCT_CATEGORIES} "
                    f"distinct categories (have {len(categories)}: {have})"
                )
        elif phase == "setup":
            if not items:
                missing.append(
                    "submit exactly one item with the 'horizon' field, "
                    "e.g. [{\"horizon\": \"6 months\"}]"
                )
        elif phase == "failure_causes":
            if len(items) < MIN_FAILURE_CAUSES:
                missing.append(
                    f"need at least {MIN_FAILURE_CAUSES} failure causes (have {len(items)})"
                )
        elif phase == "assumptions":
            if len(items) < MIN_ASSUMPTIONS:
                missing.append(
                    f"need at least {MIN_ASSUMPTIONS} assumptions (have {len(items)})"
                )
        elif phase == "strongest_case":
            if len(items) < MIN_STEELMAN_POINTS:
                missing.append(
                    f"need at least {MIN_STEELMAN_POINTS} points for the opposing "
                    f"position (have {len(items)})"
                )
        elif phase in _REFERENCE_PHASES:
            nouns = {
                "rebuttals": ("rebuttal", "counterargument"),
                "mitigations": ("mitigation", "failure cause"),
                "tests": ("test", "assumption"),
                "responses": ("response", "point"),
            }
            noun, target_kind = nouns[phase]
            for i, target in self._pending_targets(review, phase):
                missing.append(
                    f"missing {noun} for {target_kind} {i}: \"{_snippet(target['text'])}\""
                )
        return missing

    def _advance(self, review: dict) -> None:
        """Move to the next phase, auto-skipping reference phases with no targets."""
        phases = MODE_PHASES[review["mode"]]
        while True:
            review["phase_index"] += 1
            if review["phase_index"] >= len(phases):
                review["status"] = "complete"
                review["phase"] = None
                return
            next_phase = phases[review["phase_index"]]
            review["phase"] = next_phase
            if next_phase in _REFERENCE_PHASES and not self._phase_targets(review, next_phase):
                review["skipped_phases"].append(next_phase)
                continue
            return

    # -- batch validation ---------------------------------------------------

    def _validate_batch(self, review: dict, items: Any) -> list[dict]:
        """Validate a submission atomically; raise ValueError listing every problem."""
        if not isinstance(items, list) or not items:
            raise ValueError(
                "'items' must be a non-empty list of dicts matching the current "
                "phase's item_format (see the instructions returned by "
                "start_review/submit)."
            )
        phase = review["phase"]
        errors: list[str] = []
        cleaned: list[dict] = []

        pending = {i for i, _ in self._pending_targets(review, phase)} if phase in _REFERENCE_PHASES else set()
        all_targets = {i for i, _ in self._phase_targets(review, phase)} if phase in _REFERENCE_PHASES else set()
        batch_taken: set[int] = set()

        for idx, item in enumerate(items):
            if not isinstance(item, dict):
                _err(errors, idx, f"must be a dict, got {type(item).__name__}")
                continue

            if phase == "counterarguments":
                text = _get_str(item, "text", LONG_TEXT_MIN, errors, idx)
                category = _get_choice(item, "category", COUNTERARGUMENT_CATEGORIES, errors, idx)
                severity = _get_int(item, "severity", 1, 5, errors, idx)
                if None not in (text, category, severity):
                    cleaned.append({"text": text, "category": category, "severity": severity})

            elif phase == "rebuttals":
                index = self._get_index(item, pending, all_targets, batch_taken, errors, idx, "counterargument")
                verdict = _get_choice(item, "verdict", REBUTTAL_VERDICTS, errors, idx)
                justification = _get_str(item, "justification", SHORT_TEXT_MIN, errors, idx)
                if None not in (index, verdict, justification):
                    cleaned.append({"index": index, "verdict": verdict, "justification": justification})

            elif phase == "setup":
                if len(items) != 1 or review["phases"]["setup"]:
                    _err(errors, idx, "the setup phase takes exactly ONE item: [{\"horizon\": \"6 months\"}]")
                    continue
                horizon = _get_str(item, "horizon", 2, errors, idx)
                if horizon is not None:
                    review["horizon"] = horizon
                    cleaned.append({"horizon": horizon})

            elif phase == "failure_causes":
                text = _get_str(item, "text", SHORT_TEXT_MIN, errors, idx)
                likelihood = _get_int(item, "likelihood", 1, 5, errors, idx)
                impact = _get_int(item, "impact", 1, 5, errors, idx)
                if None not in (text, likelihood, impact):
                    cleaned.append({"text": text, "likelihood": likelihood, "impact": impact})

            elif phase == "mitigations":
                index = self._get_index(item, pending, all_targets, batch_taken, errors, idx, "failure cause")
                action = _get_str(item, "action", SHORT_TEXT_MIN, errors, idx)
                residual = _get_choice(item, "residual_risk", RESIDUAL_RISKS, errors, idx)
                if None not in (index, action, residual):
                    cleaned.append({"index": index, "action": action, "residual_risk": residual})

            elif phase == "assumptions":
                text = _get_str(item, "text", SHORT_TEXT_MIN, errors, idx)
                load_bearing = _get_bool(item, "load_bearing", errors, idx)
                evidence = _get_choice(item, "evidence", EVIDENCE_LEVELS, errors, idx)
                if None not in (text, load_bearing, evidence):
                    cleaned.append({"text": text, "load_bearing": load_bearing, "evidence": evidence})

            elif phase == "tests":
                index = self._get_index(item, pending, all_targets, batch_taken, errors, idx, "assumption")
                test = _get_str(item, "test", SHORT_TEXT_MIN, errors, idx)
                if None not in (index, test):
                    cleaned.append({"index": index, "test": test})

            elif phase == "strongest_case":
                text = _get_str(item, "text", LONG_TEXT_MIN, errors, idx)
                if text is not None:
                    cleaned.append({"text": text})

            elif phase == "responses":
                index = self._get_index(item, pending, all_targets, batch_taken, errors, idx, "point")
                stance = _get_choice(item, "stance", STANCES, errors, idx)
                text = _get_str(item, "text", SHORT_TEXT_MIN, errors, idx)
                if None not in (index, stance, text):
                    cleaned.append({"index": index, "stance": stance, "text": text})

        if errors:
            raise ValueError(
                "Invalid submission — nothing was saved. Fix these problems and resubmit:\n- "
                + "\n- ".join(errors)
            )
        return cleaned

    def _get_index(
        self,
        item: dict,
        pending: set[int],
        all_targets: set[int],
        batch_taken: set[int],
        errors: list[str],
        idx: int,
        target_kind: str,
    ) -> int | None:
        value = item.get("index")
        if isinstance(value, bool) or not isinstance(value, int):
            _err(errors, idx, f"'index' must be an integer referencing a target {target_kind} (got {value!r})")
            return None
        if value in batch_taken:
            _err(errors, idx, f"duplicate 'index' {value} in this batch — one item per {target_kind}")
            return None
        if value not in pending:
            if value in all_targets:
                _err(errors, idx, f"{target_kind} {value} already has an item — one per {target_kind}")
            else:
                pending_list = ", ".join(str(i) for i in sorted(pending - batch_taken)) or "none"
                _err(
                    errors,
                    idx,
                    f"'index' {value} is not a pending target {target_kind} "
                    f"(pending indices: {pending_list})",
                )
            return None
        batch_taken.add(value)
        return value

    # -- phase instructions --------------------------------------------------

    def _instructions(self, review: dict) -> dict:
        """Exact instructions for the current phase: what to send, format, minimums."""
        phase = review["phase"]
        base = {
            "phase": phase,
            "how_to_submit": f"submit(review_id='{review['id']}', items=[{{...}}, ...])",
        }

        if phase == "counterarguments":
            base.update(
                goal=(
                    "Attack the claim as a devil's advocate. Generate the strongest, most "
                    "specific counterarguments you can — no strawmen."
                ),
                item_format={
                    "text": f"str, >= {LONG_TEXT_MIN} characters — the counterargument, concrete and falsifiable",
                    "category": f"one of: {', '.join(COUNTERARGUMENT_CATEGORIES)}",
                    "severity": "int 1-5 — how damaging to the claim if true (5 = fatal)",
                },
                category_meanings=CATEGORY_MEANINGS,
                rules=[
                    f"Submit at least {MIN_COUNTERARGUMENTS} counterarguments (send them all in one call).",
                    f"Use at least {MIN_DISTINCT_CATEGORIES} distinct categories.",
                    f"Every counterargument with severity >= {REBUTTAL_SEVERITY_THRESHOLD} must be "
                    "rebutted in the next phase — assign severity honestly.",
                ],
            )
        elif phase == "rebuttals":
            targets = [
                {"index": i, "category": t["category"], "severity": t["severity"], "text": t["text"]}
                for i, t in self._pending_targets(review, phase)
            ]
            base.update(
                goal=(
                    "Rebut each high-severity counterargument honestly. If you cannot refute "
                    "one, admit that it holds."
                ),
                targets=targets,
                item_format={
                    "index": "int — index of the counterargument being rebutted (see 'targets')",
                    "verdict": f"one of: {', '.join(REBUTTAL_VERDICTS)} — does the counterargument survive your rebuttal?",
                    "justification": f"str, >= {SHORT_TEXT_MIN} characters — why that verdict",
                },
                rules=[
                    f"Provide exactly one rebuttal per target ({len(targets)} pending).",
                    "'holds' means the counterargument stands and damages the claim. "
                    "Do not mark 'refuted' without an actual refutation.",
                ],
            )
        elif phase == "setup":
            base.update(
                goal=(
                    "Premortem setup: pick the time horizon at which we imagine the decision "
                    "was taken and FAILED."
                ),
                item_format={"horizon": "str — timeframe of the imagined failure, e.g. '6 months'"},
                rules=["Submit exactly one item: [{\"horizon\": \"6 months\"}]"],
            )
        elif phase == "failure_causes":
            base.update(
                goal=(
                    f"It is {review['horizon']} from now and acting on the claim FAILED. "
                    "Write the history of that failure: the most plausible causes."
                ),
                item_format={
                    "text": f"str, >= {SHORT_TEXT_MIN} characters — one concrete cause of failure",
                    "likelihood": "int 1-5 — how likely this cause is (5 = almost certain)",
                    "impact": "int 1-5 — how much damage it does (5 = fatal)",
                },
                rules=[
                    f"Submit at least {MIN_FAILURE_CAUSES} failure causes.",
                    f"Every cause with likelihood x impact >= {MITIGATION_SCORE_THRESHOLD} must be "
                    "mitigated in the next phase — score honestly.",
                ],
            )
        elif phase == "mitigations":
            targets = [
                {
                    "index": i,
                    "likelihood": t["likelihood"],
                    "impact": t["impact"],
                    "score": t["likelihood"] * t["impact"],
                    "text": t["text"],
                }
                for i, t in self._pending_targets(review, phase)
            ]
            base.update(
                goal="Propose a concrete mitigation for each high-risk failure cause.",
                targets=targets,
                item_format={
                    "index": "int — index of the failure cause being mitigated (see 'targets')",
                    "action": f"str, >= {SHORT_TEXT_MIN} characters — a concrete, actionable mitigation",
                    "residual_risk": f"one of: {', '.join(RESIDUAL_RISKS)} — risk remaining after the mitigation",
                },
                rules=[
                    f"Provide exactly one mitigation per target ({len(targets)} pending).",
                    "The action must be something someone could actually do, not a platitude.",
                ],
            )
        elif phase == "assumptions":
            base.update(
                goal=(
                    "Audit the claim's assumptions: surface everything the claim silently "
                    "relies on being true."
                ),
                item_format={
                    "text": f"str, >= {SHORT_TEXT_MIN} characters — the assumption, stated plainly",
                    "load_bearing": "bool — if this assumption is false, does the claim collapse?",
                    "evidence": f"one of: {', '.join(EVIDENCE_LEVELS)} — how well-verified it is today",
                },
                rules=[
                    f"Submit at least {MIN_ASSUMPTIONS} assumptions.",
                    "Every load-bearing assumption with evidence != 'verified' must get a cheap "
                    "verification test in the next phase — classify honestly.",
                ],
            )
        elif phase == "tests":
            targets = [
                {"index": i, "evidence": t["evidence"], "text": t["text"]}
                for i, t in self._pending_targets(review, phase)
            ]
            base.update(
                goal="Design the cheapest test that would verify each unverified load-bearing assumption.",
                targets=targets,
                item_format={
                    "index": "int — index of the assumption being tested (see 'targets')",
                    "test": f"str, >= {SHORT_TEXT_MIN} characters — the cheapest way to verify it",
                },
                rules=[
                    f"Provide exactly one test per target ({len(targets)} pending).",
                    "Prefer tests that take hours, not months.",
                ],
            )
        elif phase == "strongest_case":
            base.update(
                goal=(
                    "Steelman the OPPOSING position: make the strongest honest case AGAINST "
                    "the claim, as its smartest advocate would."
                ),
                item_format={
                    "text": f"str, >= {LONG_TEXT_MIN} characters — one strong point for the opposing position",
                },
                rules=[
                    f"Submit at least {MIN_STEELMAN_POINTS} points.",
                    "Each point must be one the opposing side would actually endorse — no strawmen.",
                ],
            )
        elif phase == "responses":
            targets = [
                {"index": i, "text": t["text"]} for i, t in self._pending_targets(review, phase)
            ]
            base.update(
                goal=(
                    "Respond honestly to each opposing point: concede it or counter it. "
                    "Conceding when you cannot counter is the honest move."
                ),
                targets=targets,
                item_format={
                    "index": "int — index of the point being answered (see 'targets')",
                    "stance": f"one of: {', '.join(STANCES)}",
                    "text": f"str, >= {SHORT_TEXT_MIN} characters — the concession or the counter",
                },
                rules=[
                    f"Provide exactly one response per target ({len(targets)} pending).",
                    "'counter' requires an actual counterargument, not a restatement of the claim.",
                ],
            )
        return base

    # -- verdict builders ----------------------------------------------------
    #
    # Assessment rules (documented, deterministic):
    #
    # devils_advocate — risk score = number of counterarguments whose rebuttal
    # verdict is 'holds'.
    #   * refuted:  >= 2 counterarguments hold, OR any severity-5 counterargument holds
    #   * revision: exactly 1 holds, OR >= 2 partially hold
    #   * survives: otherwise
    #
    # premortem — risk score = average(likelihood * impact) over all failure
    # causes (scale 1-25).
    #   * refuted:  average > 12
    #   * revision: average > 6, OR any mitigation has residual_risk 'high'
    #   * survives: otherwise
    #
    # assumptions — risk score = number of load-bearing assumptions whose
    # evidence is not 'verified'.
    #   * refuted:  >= 2 load-bearing assumptions with evidence 'none'
    #   * revision: exactly 1 with 'none', OR >= 2 load-bearing with 'partial'
    #   * survives: otherwise
    #
    # steelman — risk score = number of opposing points conceded.
    #   * refuted:  every opposing point conceded
    #   * revision: concessions >= counters
    #   * survives: counters outnumber concessions

    def _verdict_devils_advocate(self, review: dict):
        counters = review["phases"]["counterarguments"]
        rebuttals = {r["index"]: r for r in review["phases"]["rebuttals"]}
        organized = []
        for i, counter in enumerate(counters):
            entry = {"index": i, **counter}
            rebuttal = rebuttals.get(i)
            if rebuttal:
                entry["rebuttal"] = {
                    "verdict": rebuttal["verdict"],
                    "justification": rebuttal["justification"],
                }
            else:
                entry["rebuttal"] = None
                entry["note"] = (
                    f"severity below {REBUTTAL_SEVERITY_THRESHOLD} — no rebuttal required"
                )
            organized.append(entry)

        holds = sum(1 for r in rebuttals.values() if r["verdict"] == "holds")
        partially = sum(1 for r in rebuttals.values() if r["verdict"] == "partially_holds")
        refuted = sum(1 for r in rebuttals.values() if r["verdict"] == "refuted")
        severity5_holds = any(
            counters[i]["severity"] == 5 and r["verdict"] == "holds"
            for i, r in rebuttals.items()
        )

        if holds >= 2 or severity5_holds:
            assessment = ASSESS_REFUTED
        elif holds == 1 or partially >= 2:
            assessment = ASSESS_REVISE
        else:
            assessment = ASSESS_SURVIVES

        reason = (
            f"Of {len(rebuttals)} rebutted counterargument(s): {holds} hold, "
            f"{partially} partially hold, {refuted} refuted"
            + ("; a severity-5 counterargument holds" if severity5_holds else "")
            + ". Rules: refuted if >=2 hold or any severity-5 holds; revision if "
            "exactly 1 holds or >=2 partially hold; survives otherwise."
        )
        risk_score = {
            "value": holds,
            "scale": f"0-{len(rebuttals)} counterarguments that held after rebuttal",
            "explanation": "Number of counterarguments the client could not refute.",
        }
        return {"counterarguments": organized}, risk_score, assessment, reason

    def _verdict_premortem(self, review: dict):
        causes = review["phases"]["failure_causes"]
        mitigations = {m["index"]: m for m in review["phases"]["mitigations"]}
        organized = []
        for i, cause in enumerate(causes):
            score = cause["likelihood"] * cause["impact"]
            entry = {"index": i, **cause, "score": score}
            mitigation = mitigations.get(i)
            if mitigation:
                entry["mitigation"] = {
                    "action": mitigation["action"],
                    "residual_risk": mitigation["residual_risk"],
                }
            else:
                entry["mitigation"] = None
                entry["note"] = (
                    f"score below {MITIGATION_SCORE_THRESHOLD} — no mitigation required"
                )
            organized.append(entry)

        scores = [c["likelihood"] * c["impact"] for c in causes]
        average = round(sum(scores) / len(scores), 2)
        high_residual = sum(
            1 for m in mitigations.values() if m["residual_risk"] == "high"
        )

        if average > 12:
            assessment = ASSESS_REFUTED
        elif average > 6 or high_residual:
            assessment = ASSESS_REVISE
        else:
            assessment = ASSESS_SURVIVES

        reason = (
            f"Average likelihood x impact = {average} (scale 1-25) across "
            f"{len(causes)} failure cause(s); {high_residual} mitigation(s) leave "
            "high residual risk. Rules: refuted if average > 12; revision if "
            "average > 6 or any high residual risk; survives otherwise."
        )
        risk_score = {
            "value": average,
            "scale": "1-25 (average likelihood x impact across failure causes)",
            "explanation": "Mean risk score of the imagined failure causes.",
        }
        return {
            "setup": {"horizon": review["horizon"]},
            "failure_causes": organized,
        }, risk_score, assessment, reason

    def _verdict_assumptions(self, review: dict):
        assumptions = review["phases"]["assumptions"]
        tests = {t["index"]: t for t in review["phases"]["tests"]}
        organized = []
        for i, assumption in enumerate(assumptions):
            entry = {"index": i, **assumption}
            test = tests.get(i)
            if test:
                entry["test"] = test["test"]
            else:
                entry["test"] = None
                entry["note"] = "not load-bearing or already verified — no test required"
            organized.append(entry)

        load_bearing = [a for a in assumptions if a["load_bearing"]]
        none_lb = sum(1 for a in load_bearing if a["evidence"] == "none")
        partial_lb = sum(1 for a in load_bearing if a["evidence"] == "partial")
        unverified_lb = none_lb + partial_lb

        if none_lb >= 2:
            assessment = ASSESS_REFUTED
        elif none_lb == 1 or partial_lb >= 2:
            assessment = ASSESS_REVISE
        else:
            assessment = ASSESS_SURVIVES

        reason = (
            f"{len(load_bearing)} load-bearing assumption(s): {none_lb} with no "
            f"evidence, {partial_lb} with partial evidence. Rules: refuted if >=2 "
            "load-bearing with evidence 'none'; revision if exactly 1 with 'none' "
            "or >=2 with 'partial'; survives otherwise."
        )
        risk_score = {
            "value": unverified_lb,
            "scale": f"0-{len(load_bearing)} unverified load-bearing assumptions",
            "explanation": "Load-bearing assumptions whose evidence is not 'verified'.",
        }
        return {"assumptions": organized}, risk_score, assessment, reason

    def _verdict_steelman(self, review: dict):
        points = review["phases"]["strongest_case"]
        responses = {r["index"]: r for r in review["phases"]["responses"]}
        organized = []
        for i, point in enumerate(points):
            entry = {"index": i, **point}
            response = responses.get(i)
            entry["response"] = (
                {"stance": response["stance"], "text": response["text"]} if response else None
            )
            organized.append(entry)

        conceded = sum(1 for r in responses.values() if r["stance"] == "concede")
        countered = sum(1 for r in responses.values() if r["stance"] == "counter")

        if conceded == len(points):
            assessment = ASSESS_REFUTED
        elif conceded >= countered:
            assessment = ASSESS_REVISE
        else:
            assessment = ASSESS_SURVIVES

        reason = (
            f"Of {len(points)} opposing point(s): {conceded} conceded, "
            f"{countered} countered. Rules: refuted if every point is conceded; "
            "revision if concessions >= counters; survives if counters outnumber "
            "concessions."
        )
        risk_score = {
            "value": conceded,
            "scale": f"0-{len(points)} opposing points conceded",
            "explanation": "Opposing points the client had to concede.",
        }
        return {"strongest_case": organized}, risk_score, assessment, reason
