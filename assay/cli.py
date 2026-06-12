"""Command-line entrypoint.

    python -m assay --founder "Sundaram Acharya" --company "Acme Bio" \
        --institution "CSIR-IGIB" --email you@fund.com --synthesize
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

from .engine import run_check
from .models import Severity
from .synthesize import synthesize
from .verticals import REGISTRY

_COLOR = {Severity.RISK: "\033[91m", Severity.WATCH: "\033[93m", Severity.INFO: "\033[90m"}
_RESET = "\033[0m"


def _print_human(report, no_color: bool) -> None:
    print(f"\n  PROVENANCE: {report.founder}  ·  {report.company}")
    if report.institution:
        print(f"  institution: {report.institution}")
    if report.vertical:
        print(f"  vertical: {report.vertical}")
    print("  " + "-" * 60)
    for f in report.flags:
        tag = f.severity.value
        if not no_color:
            tag = f"{_COLOR[f.severity]}{tag}{_RESET}"
        print(f"  [{tag}] {f.message}")
    if report.source_errors:
        print("\n  source errors (sources that did not return):")
        for e in report.source_errors:
            print(f"    - {e}")
    if report.verdict:
        print("\n  VERDICT")
        print("  " + "-" * 60)
        for line in report.verdict.splitlines():
            print(f"  {line}")
    print()


def main(argv: list[str] | None = None) -> int:
    # The report uses a few unicode glyphs (·, —). Windows consoles default to
    # cp1252 and render them as mojibake; force UTF-8 where the stream allows it.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass

    ap = argparse.ArgumentParser(prog="assay", description="Deeptech founder/company provenance check.")
    ap.add_argument("--founder", required=True)
    ap.add_argument("--company", required=True)
    ap.add_argument("--institution", default=None)
    ap.add_argument("--email", default=None, help="mailto for the OpenAlex polite pool")
    ap.add_argument("--patents-api-key", default=None, help="USPTO/PatentsView API key")
    ap.add_argument("--vertical", default=None, choices=sorted(REGISTRY),
                    help="attach a vertical module for extra sources")
    ap.add_argument("--synthesize", action="store_true", help="add an LLM verdict (needs ANTHROPIC_API_KEY)")
    ap.add_argument("--format", choices=["human", "json", "l3"], default="human",
                    help="human report (default), raw json, or L3 §3 research-lineage + §8 IP-portfolio markdown")
    ap.add_argument("--json", action="store_true", help="alias for --format json")
    ap.add_argument("--no-color", action="store_true")
    args = ap.parse_args(argv)

    report = asyncio.run(run_check(
        founder=args.founder, company=args.company, institution=args.institution,
        mailto=args.email, patents_api_key=args.patents_api_key, vertical=args.vertical,
    ))
    if args.synthesize:
        report.verdict = synthesize(report)

    fmt = "json" if args.json else args.format
    if fmt == "json":
        print(json.dumps(report.to_dict(), indent=2, default=str))
    elif fmt == "l3":
        from .render import render_provenance_l3
        print(render_provenance_l3(report))
    else:
        _print_human(report, no_color=args.no_color)
    return 0


if __name__ == "__main__":
    sys.exit(main())
