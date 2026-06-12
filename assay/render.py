"""Render Assay reports into Shibil's L3/L5 diligence-template section shapes.

The diligence ladder (vault): L2 Questions-to-Close → L3 Deep Diligence → L4
post-conversation → L5 IC memo. Assay is the evidence spine. These pure renderers
emit the sections it can draft:

  science (ClaimReport)      → L3 §10 "Claim Verification" (✅ checks out /
                               ⚠️ inflated / ❌ couldn't verify) + L5 "Appendix A —
                               Key Publications"
  provenance (ProvenanceReport) → L3 §8 / L5 §3 "IP Portfolio" + founder research lineage

No network, no LLM — just string building. Output is markdown to paste into the
memo; Assay never writes into the vault itself.
"""
from __future__ import annotations

from .models import ClaimReport, EvidenceGrade, ProvenanceReport
from .synthesize import summarize_adjudications


# ----------------------------------------------------- science → L3 §10 ----
def _stance(report: ClaimReport) -> str | None:
    """The adjudicated (verified) stance, if --synthesize was run; else None."""
    if report.adjudications:
        return summarize_adjudications(report.adjudications)["stance"]
    return None


def bucket_for_claim(report: ClaimReport) -> str:
    """Map a claim's evidence to one of Shibil's three L3 §10 buckets:
    'checks_out' (✅) / 'inflated' (⚠️) / 'unverified' (❌).

    Prefers the adjudicated stance (actual support) when present; otherwise falls
    back to the structural grade (does independent literature exist)."""
    codes = {f.code for f in report.flags}
    grade = report.grade
    stance = _stance(report)

    if "literature_source_unavailable" in codes or "claim_too_generic" in codes:
        return "unverified"
    if stance:
        if stance.startswith("INDEPENDENTLY SUPPORTED"):
            return "checks_out"
        if stance.startswith("SUPPORTED (founders"):
            return "inflated"   # true, but overstated as if independently validated
        if stance.startswith("SUPPORTED"):
            return "checks_out"
        if stance == "REFUTED":
            return "inflated"
        if stance.startswith("NOT SUPPORTED"):
            return "unverified"
    # Structural fallback (no --synthesize).
    if grade is EvidenceGrade.UNSUPPORTED:
        return "unverified"
    if grade is EvidenceGrade.CONTRADICTED:
        return "inflated"
    if grade in (EvidenceGrade.SINGLE_GROUP, EvidenceGrade.PREPRINT_ONLY) or "replication_thin" in codes:
        return "inflated"
    if grade in (EvidenceGrade.REPLICATED, EvidenceGrade.PEER_REVIEWED):
        return "checks_out"
    return "unverified"


def _strongest(report: ClaimReport) -> str:
    pool = report.independent_works or [w for w in report.works if w.is_primary_article] or report.works
    top = max(pool, key=lambda w: w.cited_by_count, default=None)
    return f"{top.title} ({top.venue}, {top.year}, {top.cited_by_count}c)" if top else "—"


def _claim_reason(report: ClaimReport) -> str:
    codes = {f.code for f in report.flags}
    grade = report.grade
    stance = _stance(report)
    cite = _strongest(report)

    if "literature_source_unavailable" in codes:
        return "Literature source unreachable — UNVERIFIED, not absent."
    if "claim_too_generic" in codes:
        return ("Claim too generic — no specific verifiable term; matches the field, not the "
                "company. Needs a named compound / target / mechanism.")
    if grade is EvidenceGrade.UNSUPPORTED or (stance and stance.startswith("NOT SUPPORTED")):
        return "No literature establishes the claim — keyword matches only."
    if stance == "REFUTED" or grade is EvidenceGrade.CONTRADICTED:
        return f"Contradicted / retracted. Strongest: {cite}."
    if bucket_for_claim(report) == "checks_out":
        lead = stance or grade.value
        return f"{lead}. Strongest: {cite}."
    # inflated bucket
    if "replication_thin" in codes:
        return f"Corroboration thin — single, lightly-cited. {cite}."
    if grade is EvidenceGrade.SINGLE_GROUP or (stance and stance.startswith("SUPPORTED (founders")):
        return f"Peer-reviewed but founders' group only — no independent replication. {cite}."
    if grade is EvidenceGrade.PREPRINT_ONLY:
        return f"Preprint-only — not yet peer-reviewed. {cite}."
    return f"{grade.value}. {cite}."


def render_claim_verification(reports: list[ClaimReport]) -> str:
    """The L3 §10 'Claim Verification' section: one row per claim, sorted into the
    three buckets exactly as Shibil writes them by hand."""
    buckets: dict[str, list[tuple[str, str]]] = {"checks_out": [], "inflated": [], "unverified": []}
    for r in reports:
        buckets[bucket_for_claim(r)].append((r.claim, _claim_reason(r)))

    lines = ["## 10. CLAIM VERIFICATION", ""]

    def table(heading: str, col2: str, rows: list[tuple[str, str]]) -> None:
        lines.append(f"### {heading}")
        if not rows:
            lines.append("")
            lines.append("_None in this bucket._")
            lines.append("")
            return
        lines.append("")
        lines.append(f"| Claim | {col2} |")
        lines.append("|-------|--------|")
        for claim, reason in rows:
            lines.append(f"| {claim} | {reason} |")
        lines.append("")

    table("Claims That Check Out ✅", "Verdict", buckets["checks_out"])
    table("Claims That Are Inflated or Misleading ⚠️", "Reality", buckets["inflated"])
    table("Claims That Could Not Be Verified ❌", "Issue", buckets["unverified"])
    return "\n".join(lines).rstrip() + "\n"


def render_key_publications(reports: list[ClaimReport], limit: int = 12) -> str:
    """L5 'Appendix A — Key Publications': de-duped, citation-ranked evidence base."""
    seen: set = set()
    works = []
    for r in reports:
        for w in r.works:
            key = w.id or w.title
            if key in seen:
                continue
            seen.add(key)
            works.append(w)
    works.sort(key=lambda w: w.cited_by_count, reverse=True)

    lines = ["## Appendix A — Key Publications", ""]
    if not works:
        lines.append("_No publications surfaced._")
        return "\n".join(lines) + "\n"
    for w in works[:limit]:
        tags = []
        if w.is_preprint:
            tags.append("preprint")
        if w.founder_authored:
            tags.append("founders' group")
        if w.is_retracted:
            tags.append("RETRACTED")
        suffix = f" — _{', '.join(tags)}_" if tags else ""
        lines.append(f"- [{w.year}] **{w.title}** — {w.venue} ({w.cited_by_count} cites){suffix}")
    return "\n".join(lines) + "\n"


# ------------------------------------------------ provenance → L3 §8 / §3 ----
def render_ip_portfolio(report: ProvenanceReport) -> str:
    """L3 §8 / L5 §3 'IP Portfolio': Verified table + Concerns + Assessment."""
    codes = {f.code for f in report.flags}
    lines = ["## 8. IP PORTFOLIO", "", "**Verified:**", ""]
    if report.patents:
        lines.append("| Patent | Assignee(s) | Date |")
        lines.append("|--------|-------------|------|")
        for p in report.patents:
            assignees = ", ".join(p.assignee_organizations) or "—"
            lines.append(f"| {p.patent_id or '—'} | {assignees} | {p.date or '—'} |")
        lines.append("")
    else:
        lines.append("_No patents resolved from the public source._")
        lines.append("")

    concerns = []
    if "patent_source_unavailable" in codes:
        concerns.append("Patent source unreachable — **IP ownership UNVERIFIED, not clean.** Re-run with `--patents-api-key`.")
    if "ip_owned_by_institution" in codes:
        concerns.append("Founder is named inventor but the assignee is an **institution, not the company** — needs an explicit field-of-use licence before the IP is investable.")
    if "mixed_ip_ownership" in codes:
        concerns.append("IP is **split** between the company and an institution — map which patents sit where.")
    if "no_patents_found" in codes:
        concerns.append("No patents name the founder as inventor — confirm the moat is patent-, trade-secret-, or not-yet-filed.")
    lines.append("**Concerns:**")
    lines.append("")
    lines += [f"- {c}" for c in concerns] if concerns else ["- None surfaced."]
    lines.append("")

    # Assessment — one honest line derived from the flags.
    if "ip_owned_by_institution" in codes:
        assess = "IP sits with an institution, not the company. **Gate** until a field-of-use licence is in hand."
    elif "patent_source_unavailable" in codes:
        assess = "IP ownership could not be verified this run — treat as an open item, not a clean bill."
    elif "ip_owned_by_company" in codes:
        assess = "At least one patent is assigned to the company — ownership signal is positive; confirm coverage and grant status."
    else:
        assess = "No hard IP-ownership signal either way — confirm the moat directly with the founder."
    lines.append(f"**Assessment:** {assess}")
    return "\n".join(lines).rstrip() + "\n"


def render_research_lineage(report: ProvenanceReport) -> str:
    """L3 §3 / L5 §6: founder research record from OpenAlex."""
    a = report.author
    lines = ["## 3. FOUNDER — RESEARCH LINEAGE", ""]
    if a is None:
        if any(f.code == "research_source_unavailable" for f in report.flags):
            lines.append("_Research source unreachable — record UNVERIFIED, not absent._")
        else:
            lines.append("_No OpenAlex author match — founder may be non-academic, or the name needs manual disambiguation._")
        return "\n".join(lines) + "\n"
    lines.append(f"- **{a.display_name or report.founder}** — h-index {a.h_index}, "
                 f"{a.works_count} works, {a.cited_by_count} citations")
    if a.last_institution:
        lines.append(f"- Last institution: {a.last_institution}  _(identity match: {a.match_confidence})_")
    elif a.match_confidence:
        lines.append(f"- _Institution not matched — identity match: {a.match_confidence} (verify this is the right person)._")
    if a.top_topics:
        lines.append(f"- Topics: {', '.join(a.top_topics)}")
    return "\n".join(lines) + "\n"


def render_provenance_l3(report: ProvenanceReport) -> str:
    return render_research_lineage(report) + "\n---\n\n" + render_ip_portfolio(report)
