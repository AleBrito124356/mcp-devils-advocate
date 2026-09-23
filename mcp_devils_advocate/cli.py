"""Command-line interface: ``mcp-devils-advocate [command]``.

With no command it runs the MCP stdio server, exactly as before, so existing
client configurations (``"command": "mcp-devils-advocate"``) keep working.
The other commands work on the same review files without an LLM client and
without importing the MCP SDK:

* ``serve``                          run the MCP stdio server (the default)
* ``list``                           every review, newest first
* ``show <id>``                      status, progress and current instructions
* ``report <id> [--format md|json] [-o FILE]``   export the decision memo
* ``demo [--mode MODE]``             offline scripted review, prints the memo
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from . import __version__
from .core import MODES, ReviewStore
from .memo import render_status_markdown


def _utf8_stdout() -> None:
    """Emit UTF-8 even when stdout is a pipe on a legacy Windows code page."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):  # pragma: no cover - exotic streams
                pass


def _store(args: argparse.Namespace) -> ReviewStore:
    return ReviewStore(args.data_dir) if args.data_dir else ReviewStore()


def _cmd_serve(args: argparse.Namespace) -> int:
    from .server import main as serve  # imported lazily: only this command needs the SDK

    serve(args.data_dir)
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    listing = _store(args).list_reviews()
    if args.json:
        print(json.dumps(listing, indent=2, ensure_ascii=False))
        return 0
    reviews = listing["reviews"]
    if not reviews:
        print(f"No reviews in {listing['data_dir']}.")
    else:
        print(f"{'ID':<9} {'MODE':<16} {'STATUS':<10} {'PHASE':<17} {'CREATED':<26} CLAIM")
        for r in reviews:
            print(
                f"{r['review_id']:<9} {r['mode']:<16} {r['status']:<10} "
                f"{(r['phase'] or '-'):<17} {r['created_at']:<26} {r['claim']}"
            )
        print(f"\n{listing['count']} review(s) in {listing['data_dir']}")
    for skipped in listing.get("skipped", []):
        print(f"warning: skipped {skipped['file']}: {skipped['reason']}", file=sys.stderr)
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    summary = _store(args).get_review(args.review_id)
    if args.json:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        return 0
    print(render_status_markdown(summary), end="")
    if summary.get("instructions"):
        print("\n## Current phase instructions\n")
        print("```json")
        print(json.dumps(summary["instructions"], indent=2, ensure_ascii=False))
        print("```")
    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    fmt = "json" if args.format == "json" else "markdown"
    exported = _store(args).export_report(args.review_id, fmt)
    content = exported["content"]
    if not content.endswith("\n"):
        content += "\n"
    if args.output:
        path = Path(args.output)
        path.write_text(content, encoding="utf-8")
        print(f"Wrote {exported['format']} report for {exported['review_id']} to {path}")
    else:
        sys.stdout.write(content)
    return 0


def _cmd_demo(args: argparse.Namespace) -> int:
    from .demo import run_demo

    if args.data_dir:
        run_demo(args.mode, args.data_dir, sys.stdout, memo_only=args.memo_only)
        return 0
    with tempfile.TemporaryDirectory(prefix="devils-advocate-demo-") as tmp:
        run_demo(args.mode, tmp, sys.stdout, memo_only=args.memo_only)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mcp-devils-advocate",
        description=(
            "Stress-test reasoning with enforced protocols. With no command, runs the "
            "MCP stdio server."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    data_dir_help = (
        "review directory (default: $DEVILS_ADVOCATE_DIR or ~/.mcp-devils-advocate; "
        "for 'demo', where to keep the demo review instead of a temp dir)"
    )
    parser.add_argument("--data-dir", help=data_dir_help)
    # Accept --data-dir after the command too, without clobbering one given before it.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--data-dir", default=argparse.SUPPRESS, help=data_dir_help)
    sub = parser.add_subparsers(dest="command", metavar="command")

    p = sub.add_parser("serve", parents=[common], help="run the MCP stdio server (default)")
    p.set_defaults(func=_cmd_serve)

    p = sub.add_parser("list", parents=[common], help="list every review, newest first")
    p.add_argument("--json", action="store_true", help="print raw JSON")
    p.set_defaults(func=_cmd_list)

    p = sub.add_parser("show", parents=[common], help="status, progress and current instructions of a review")
    p.add_argument("review_id")
    p.add_argument("--json", action="store_true", help="print raw JSON")
    p.set_defaults(func=_cmd_show)

    p = sub.add_parser("report", parents=[common], help="export a completed review as a decision memo")
    p.add_argument("review_id")
    p.add_argument("--format", choices=("md", "markdown", "json"), default="md")
    p.add_argument("-o", "--output", help="write to this file instead of stdout")
    p.set_defaults(func=_cmd_report)

    p = sub.add_parser("demo", parents=[common], help="run an offline scripted review and print the memo")
    p.add_argument("--mode", choices=MODES, default="devils_advocate")
    p.add_argument("--memo-only", action="store_true", help="print only the final memo")
    p.set_defaults(func=_cmd_demo)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command in (None, "serve"):
        # stdout is the JSON-RPC channel here: leave it exactly as the SDK expects.
        return _cmd_serve(args)
    _utf8_stdout()
    try:
        return args.func(args)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def console_main() -> None:  # pragma: no cover - thin wrapper for the console script
    sys.exit(main())


if __name__ == "__main__":  # pragma: no cover
    console_main()
