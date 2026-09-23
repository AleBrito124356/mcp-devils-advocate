"""Render a finished review as a shareable Markdown decision memo.

``render_markdown`` takes the dict returned by ``ReviewStore.get_verdict`` and
produces a memo a team can paste into a PR, an issue or a design doc: the
claim and its context, the assessment and the rule that produced it, every
item of every phase with its rebuttal / mitigation / test / response attached
under it, and a **Next actions** checklist of the work the review leaves open
(counterarguments that still hold, mitigations to carry out, verification
tests to run, opposing points that were conceded).

``render_status_markdown`` covers reviews that have no verdict yet (in
progress or abandoned), so a ``review://`` resource always has something
useful to show.

Pure standard library; output is deterministic for a given report.
"""

from __future__ import annotations

from typing import Any

LENS_TITLES = {
    "devils_advocate": "Devil's advocate",
    "assumptions": "Assumption audit",
    "premortem": "Premortem",
    "steelman": "Steelman of the opposing side",
}

MODE_TITLES = {**LENS_TITLES, "gauntlet": "Gauntlet: all four lenses"}


def _clean(text: Any) -> str:
    """Collapse whitespace so user text never breaks the Markdown structure."""
    return " ".join(str(text).split())


def _risk(risk: dict) -> str:
    return f"{risk['value']} — {risk['scale']}"


# ---------------------------------------------------------------------------
# Per-lens sections
# ---------------------------------------------------------------------------


def _section_devils_advocate(phases: dict) -> list[str]:
    lines = ["## Counterarguments", ""]
    for c in phases["counterarguments"]:
        lines.append(f"- **#{c['index']} · {c['category']} · severity {c['severity']}** — {_clean(c['text'])}")
        rebuttal = c.get("rebuttal")
        if rebuttal:
            verdict = rebuttal["verdict"].replace("_", " ")
            lines.append(f"  - Rebuttal (*{verdict}*): {_clean(rebuttal['justification'])}")
        else:
            lines.append(f"  - _{c.get('note', 'no rebuttal required')}_")
    lines.append("")
    return lines


def _section_premortem(phases: dict) -> list[str]:
    horizon = phases.get("setup", {}).get("horizon")
    lines = ["## Premortem", ""]
    if horizon:
        lines += [f"Imagined failure horizon: **{_clean(horizon)}**.", ""]
    for c in phases["failure_causes"]:
        lines.append(
            f"- **#{c['index']} · likelihood {c['likelihood']} × impact {c['impact']} = {c['score']}** — "
            f"{_clean(c['text'])}"
        )
        mitigation = c.get("mitigation")
        if mitigation:
            lines.append(
                f"  - Mitigation (residual risk *{mitigation['residual_risk']}*): {_clean(mitigation['action'])}"
            )
        else:
            lines.append(f"  - _{c.get('note', 'no mitigation required')}_")
    lines.append("")
    return lines


def _section_assumptions(phases: dict) -> list[str]:
    lines = ["## Assumptions", ""]
    for a in phases["assumptions"]:
        bearing = "load-bearing" if a["load_bearing"] else "not load-bearing"
        lines.append(f"- **#{a['index']} · {bearing} · evidence {a['evidence']}** — {_clean(a['text'])}")
        if a.get("test"):
            lines.append(f"  - Verification test: {_clean(a['test'])}")
        else:
            lines.append(f"  - _{a.get('note', 'no test required')}_")
    lines.append("")
    return lines


def _section_steelman(phases: dict) -> list[str]:
    lines = ["## Strongest case for the opposing side", ""]
    for p in phases["strongest_case"]:
        lines.append(f"- **#{p['index']}** — {_clean(p['text'])}")
        response = p.get("response")
        if response:
            stance = "Conceded" if response["stance"] == "concede" else "Countered"
            lines.append(f"  - {stance}: {_clean(response['text'])}")
    lines.append("")
    return lines


_SECTIONS = {
    "devils_advocate": _section_devils_advocate,
    "assumptions": _section_assumptions,
    "premortem": _section_premortem,
    "steelman": _section_steelman,
}

# Lens order inside a gauntlet review (matches core.GAUNTLET_LENSES).
_LENS_ORDER = ("devils_advocate", "assumptions", "premortem", "steelman")


def _lenses_in(report: dict) -> list[str]:
    if report["mode"] == "gauntlet":
        return list(_LENS_ORDER)
    return [report["mode"]]


# ---------------------------------------------------------------------------
# Next actions
# ---------------------------------------------------------------------------


def next_actions(report: dict) -> list[str]:
    """Open work left by the review, in phase order (plain text, no checkbox)."""
    phases = report["phases"]
    actions: list[str] = []
    lenses = _lenses_in(report)
    if "devils_advocate" in lenses:
        for c in phases["counterarguments"]:
            rebuttal = c.get("rebuttal")
            if rebuttal and rebuttal["verdict"] in ("holds", "partially_holds"):
                verdict = rebuttal["verdict"].replace("_", " ")
                actions.append(
                    f"Resolve counterargument #{c['index']} ({verdict}, severity {c['severity']}): "
                    f"{_clean(c['text'])}"
                )
    if "assumptions" in lenses:
        for a in phases["assumptions"]:
            if a.get("test"):
                actions.append(
                    f"Run the test for assumption #{a['index']} (evidence {a['evidence']}): {_clean(a['test'])}"
                )
    if "premortem" in lenses:
        for c in phases["failure_causes"]:
            mitigation = c.get("mitigation")
            if mitigation:
                actions.append(
                    f"Carry out the mitigation for failure cause #{c['index']} "
                    f"(score {c['score']}, residual risk {mitigation['residual_risk']}): "
                    f"{_clean(mitigation['action'])}"
                )
    if "steelman" in lenses:
        for p in phases["strongest_case"]:
            response = p.get("response")
            if response and response["stance"] == "concede":
                actions.append(f"Account for conceded opposing point #{p['index']}: {_clean(p['text'])}")
    return actions


# ---------------------------------------------------------------------------
# Public renderers
# ---------------------------------------------------------------------------


def render_markdown(report: dict) -> str:
    """Markdown decision memo for a completed review (the output of get_verdict)."""
    mode = report["mode"]
    lines = [
        f"# Decision memo: {_clean(report['claim'])}",
        "",
        f"- **Review:** `{report['review_id']}` · mode `{mode}` ({MODE_TITLES.get(mode, mode)})",
        f"- **Assessment:** **{report['assessment']}**",
        f"- **Risk score:** {_risk(report['risk_score'])}",
        f"- **Started:** {report['created_at']} · **Completed:** {report['completed_at']}",
    ]
    if report.get("horizon"):
        lines.append(f"- **Premortem horizon:** {_clean(report['horizon'])}")
    if report.get("skipped_phases"):
        lines.append(
            "- **Skipped phases:** "
            + ", ".join(f"`{p}`" for p in report["skipped_phases"])
            + " (nothing reached the threshold that requires them)"
        )
    if report.get("context"):
        lines += ["", f"**Context:** {_clean(report['context'])}"]
    lines += ["", "## Verdict", "", f"**{report['assessment']}** — {_clean(report['assessment_reason'])}", ""]

    if mode == "gauntlet":
        lines += ["| Lens | Assessment | Risk score |", "|---|---|---|"]
        for lens in _LENS_ORDER:
            sub = report["lenses"][lens]
            lines.append(f"| {LENS_TITLES[lens]} | {sub['assessment']} | {_risk(sub['risk_score'])} |")
        lines.append("")

    for lens in _lenses_in(report):
        lines += _SECTIONS[lens](report["phases"])

    lines += ["## Next actions", ""]
    actions = next_actions(report)
    if actions:
        lines += [f"- [ ] {action}" for action in actions]
    else:
        lines.append("- [x] Nothing left open: no counterargument held, nothing needs mitigating, "
                     "testing or conceding.")
    lines += ["", f"<sub>Generated by mcp-devils-advocate from review `{report['review_id']}`.</sub>", ""]
    return "\n".join(lines)


def render_status_markdown(summary: dict) -> str:
    """Markdown for a review with no verdict yet (the output of ReviewStore.get_review)."""
    lines = [
        f"# Review in progress: {_clean(summary['claim'])}" if summary["status"] == "active"
        else f"# Review {summary['status']}: {_clean(summary['claim'])}",
        "",
        f"- **Review:** `{summary['review_id']}` · mode `{summary['mode']}`",
        f"- **Status:** {summary['status']}",
    ]
    if summary["status"] == "active":
        lines.append(f"- **Current phase:** `{summary['phase']}` (step {summary['step']})")
    if summary.get("abandon_reason"):
        lines.append(f"- **Abandoned because:** {_clean(summary['abandon_reason'])}")
    lines.append(f"- **Started:** {summary['created_at']} · **Last update:** {summary['updated_at']}")
    if summary.get("context"):
        lines += ["", f"**Context:** {_clean(summary['context'])}"]
    lines += ["", "## Items so far", ""]
    for phase, count in summary["items_per_phase"].items():
        lines.append(f"- `{phase}`: {count}")
    if summary.get("missing"):
        lines += ["", "## Still missing in this phase", ""]
        lines += [f"- {_clean(m)}" for m in summary["missing"]]
    if summary["status"] == "complete":
        lines += ["", f"All phases are complete — call `get_verdict('{summary['review_id']}')`."]
    lines.append("")
    return "\n".join(lines)
