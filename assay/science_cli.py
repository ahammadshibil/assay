"""Science-mode CLI — confirm a scientific claim against the literature.

    python -m assay.science_cli \
        --claim "CSP-TTK21 activates p300/CBP acetyltransferase and crosses the blood-brain barrier" \
        --founders "Tapas Kumar Kundu, Snehajyoti Chatterjee"

The headline is the evidence GRADE; --founders enables the independent-replication
check (the strongest signal). Add --synthesize for an LLM read of actual support.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

from .models import EvidenceGrade, Severity
from .render import render_claim_verification, render_key_publications
from .science import verify_claim
from .synthesize import synthesize_science

_GRADE_COLOR = {
    EvidenceGrade.REPLICATED: "\033[92m",     # green
    EvidenceGrade.PEER_REVIEWED: "\033[92m",
    EvidenceGrade.SINGLE_GROUP: "\033[93m",   # yellow
    EvidenceGrade.PREPRINT_ONLY: "\033[93m",
    EvidenceGrade.UNSUPPORTED: "\033[90m",    # grey
    EvidenceGrade.CONTRADICTED: "\033[91m",   # red
    EvidenceGrade.TOO_GENERIC: "\033[93m",    # yellow — matched the field, not the company
}
_SEV_COLOR = {Severity.RISK: "\033[91m", Severity.WATCH: "\033[93m", Severity.INFO: "\033[90m"}
_RESET = "\033[0m"


def _print_human(report, no_color: bool) -> None:
    grade = report.grade.value
    if not no_color:
        grade = f"{_GRADE_COLOR.get(report.grade, '')}{grade}{_RESET}"
    print(f"\n  CLAIM: {report.claim}")
    if report.founders:
        print(f"  founders (for independence): {', '.join(report.founders)}")
    print("  " + "-" * 64)
    print(f"  GRADE: {grade}")
    print("  " + "-" * 64)
    for f in report.flags:
        tag = f.severity.value
        if not no_color:
            tag = f"{_SEV_COLOR[f.severity]}{tag}{_RESET}"
        print(f"  [{tag}] {f.message}")
    if report.independent_works:
        print("\n  independent corroboration:")
        for w in sorted(report.independent_works, key=lambda w: w.cited_by_count, reverse=True)[:5]:
            print(f"    · [{w.year}] {w.cited_by_count:>4} cites  {(w.title or '')[:72]}")
            print(f"               {w.venue}")
    if report.source_errors:
        print("\n  source errors:")
        for e in report.source_errors:
            print(f"    - {e}")
    if report.adjudications:
        print("\n  per-paper adjudication (does it actually support the claim?):")
        for a in report.adjudications:
            who = "founder" if a.get("founder_authored") else ("indep" if a.get("founder_authored") is False else "?")
            print(f"    · {a['verdict']:11} [{who}] {a['paper'][:62]}")
            if a.get("reason"):
                print(f"                  {a['reason']}")
    if report.verdict:
        print("\n  ADJUDICATION")
        print("  " + "-" * 64)
        for line in report.verdict.splitlines():
            print(f"  {line}")
    print()


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass

    ap = argparse.ArgumentParser(prog="assay-science",
                                 description="Confirm a scientific claim against the primary literature.")
    ap.add_argument("--claim", default=None, help="the technical assertion to verify")
    ap.add_argument("--claims-file", default=None,
                    help="file with one claim per line (# comments / blanks ignored) — builds a full L3 §10 ledger")
    ap.add_argument("--founders", default=None,
                    help="comma-separated names whose claim it is (enables the independence check)")
    ap.add_argument("--email", default=None, help="mailto for the OpenAlex polite pool")
    ap.add_argument("--max-works", type=int, default=25)
    ap.add_argument("--synthesize", action="store_true", help="LLM read of actual support (needs ANTHROPIC_API_KEY)")
    ap.add_argument("--format", choices=["human", "json", "l3"], default="human",
                    help="human report (default), raw json, or L3 §10 Claim Verification + Appendix A markdown")
    ap.add_argument("--json", action="store_true", help="alias for --format json")
    ap.add_argument("--no-color", action="store_true")
    args = ap.parse_args(argv)

    claims = _gather_claims(args)
    if not claims:
        ap.error("provide --claim or --claims-file")
    founders = [s.strip() for s in args.founders.split(",")] if args.founders else []

    async def _run() -> list:
        import asyncio as _a
        tasks = [verify_claim(claim=c, founders=founders, mailto=args.email,
                              max_works=args.max_works) for c in claims]
        return await _a.gather(*tasks)

    reports = asyncio.run(_run())
    if args.synthesize:
        for r in reports:
            r.verdict = synthesize_science(r)

    fmt = "json" if args.json else args.format
    if fmt == "json":
        print(json.dumps([r.to_dict() for r in reports] if len(reports) > 1 else reports[0].to_dict(),
                         indent=2, default=str))
    elif fmt == "l3":
        print(render_claim_verification(reports))
        print(render_key_publications(reports))
    else:
        for r in reports:
            _print_human(r, no_color=args.no_color)
    return 0


def _gather_claims(args) -> list[str]:
    claims = []
    if args.claim:
        claims.append(args.claim)
    if args.claims_file:
        with open(args.claims_file, encoding="utf-8") as fh:
            claims += [ln.strip() for ln in fh if ln.strip() and not ln.lstrip().startswith("#")]
    return claims


if __name__ == "__main__":
    sys.exit(main())
