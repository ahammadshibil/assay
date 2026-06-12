"""The engine: pull all three sources concurrently, then apply deterministic
flag rules. The rules encode the questions that actually decide a deeptech
deal — most importantly, *does the founder own the IP, or does their old lab?*
"""
from __future__ import annotations

import asyncio
import datetime as _dt
from dataclasses import dataclass

from .models import Author, Flag, Grant, Patent, ProvenanceReport, Severity
from .sources import NIHReporterClient, OpenAlexClient, PatentClient, SBIRClient
from .verticals import VerticalContext, get_vertical

# Words that mark an assignee as an institution rather than the founder's company.
_INSTITUTIONAL = (
    "university", "institute", "institut", "college", "council", "csir",
    "iit", "iisc", "laboratory", "laboratoire", "academy", "akademie",
    "national lab", "hospital", "foundation", "trust", "ministry", "cnrs",
    "max planck", "inserm",
)
_STALE_YEARS = 3


async def run_check(
    founder: str,
    company: str,
    institution: str | None = None,
    mailto: str | None = None,
    patents_api_key: str | None = None,
    vertical: str | None = None,
) -> ProvenanceReport:
    report = ProvenanceReport(founder=founder, company=company, institution=institution)

    oa = OpenAlexClient(mailto=mailto)
    pat = PatentClient(api_key=patents_api_key)
    sbir = SBIRClient()
    nih = NIHReporterClient()
    module = get_vertical(vertical)

    core = asyncio.gather(
        oa.find_author(founder, institution),
        pat.search_by_inventor(founder),
        sbir.search_by_firm(company),
        nih.search_by_pi(founder),
    )
    if module is not None:
        ctx = VerticalContext(founder=founder, company=company, institution=institution)
        core_res, vres = await asyncio.gather(core, module.evaluate(ctx))
    else:
        core_res, vres = await core, None
        if vertical:  # asked for a vertical we don't have
            report.source_errors.append(f"unknown vertical: {vertical}")

    (author, oa_err), (patents, pat_err), (sbir_g, sbir_err), (nih_g, nih_err) = core_res

    report.author = author
    report.patents = patents
    report.grants = (sbir_g or []) + (nih_g or [])
    report.source_errors += [e for e in (oa_err, pat_err, sbir_err, nih_err) if e]

    # Per-source error state matters for flag integrity: a source that *failed*
    # must never produce the same flag as a source that *succeeded and found
    # nothing*. See _evaluate.
    errors = _SourceErrors(research=oa_err, patents=pat_err, sbir=sbir_err, nih=nih_err)
    flags = _evaluate(report, errors)
    if vres is not None:
        report.vertical = vertical
        report.vertical_findings = vres.findings
        flags += vres.flags
        report.source_errors += vres.errors

    report.flags = _sort_flags(flags)
    return report


def _sort_flags(flags: list[Flag]) -> list[Flag]:
    order = {Severity.RISK: 0, Severity.WATCH: 1, Severity.INFO: 2}
    return sorted(flags, key=lambda f: order[f.severity])


@dataclass
class _SourceErrors:
    """Error string (or None) per upstream source for this run."""
    research: str | None = None   # OpenAlex
    patents: str | None = None    # PatentsView / USPTO
    sbir: str | None = None       # SBIR.gov
    nih: str | None = None        # NIH RePORTER

    @property
    def grants_all_failed(self) -> bool:
        return self.sbir is not None and self.nih is not None

    @property
    def grants_partial(self) -> str | None:
        if self.sbir and not self.nih:
            return "SBIR"
        if self.nih and not self.sbir:
            return "NIH"
        return None


def _evaluate(r: ProvenanceReport, errors: _SourceErrors | None = None) -> list[Flag]:
    errors = errors or _SourceErrors()
    flags: list[Flag] = []
    flags += _research_flags(r.author, errors.research)
    flags += _patent_flags(r.founder, r.company, r.patents, errors.patents)
    flags += _grant_flags(r.company, r.grants, errors)
    return flags


def _research_flags(a: Author | None, error: str | None = None) -> list[Flag]:
    # Source failed: absence is UNKNOWN, not confirmed. Never let a failed lookup
    # read as "this founder has no research record".
    if error is not None:
        return [Flag("research_source_unavailable", Severity.WATCH,
                     "Could not query OpenAlex — research record is UNVERIFIED, not "
                     "absent. Re-run or check manually.",
                     {"error": error})]
    if a is None:
        return [Flag("no_research_record", Severity.WATCH,
                     "No OpenAlex author match. Founder may be non-academic, or "
                     "name needs manual disambiguation.")]
    out: list[Flag] = []
    if a.match_confidence == "unverified":
        out.append(Flag("weak_author_match", Severity.WATCH,
                        f"Author match is low-confidence ({a.works_count} works). Verify identity manually.",
                        {"openalex_id": a.openalex_id}))
    if a.last_publication_year:
        gap = _dt.date.today().year - a.last_publication_year
        if gap > _STALE_YEARS:
            out.append(Flag("stale_research", Severity.WATCH,
                            f"Last publication was {gap} years ago ({a.last_publication_year}).",
                            {"last_publication_year": a.last_publication_year}))
    out.append(Flag("research_profile", Severity.INFO,
                    f"h-index {a.h_index}, {a.works_count} works, {a.cited_by_count} citations"
                    f"{' @ ' + a.last_institution if a.last_institution else ''}.",
                    {"h_index": a.h_index, "topics": a.top_topics}))
    return out


def _patent_flags(founder: str, company: str, patents: list[Patent],
                  error: str | None = None) -> list[Flag]:
    # The most dangerous false-clean in the whole tool: a patent source that
    # failed must NOT look like "no IP encumbrance found". IP ownership is the
    # headline check — when we can't run it, say so loudly.
    if error is not None:
        return [Flag("patent_source_unavailable", Severity.WATCH,
                     "Could not query the patent source — IP ownership is UNVERIFIED. "
                     "Do not read this as clean IP. (USPTO PatentSearch is mid-migration "
                     "to data.uspto.gov; pass --patents-api-key once ODP access is set up.)",
                     {"error": error})]
    if not patents:
        return [Flag("no_patents_found", Severity.WATCH,
                     "No patents found naming the founder as inventor. Confirm whether the "
                     "moat is patent-based, trade-secret-based, or not yet filed.")]
    company_l = company.lower()
    institutional, owned_by_company = [], False
    for p in patents:
        for org in p.assignee_organizations:
            ol = org.lower()
            if any(tok in ol for tok in _INSTITUTIONAL) and company_l not in ol:
                institutional.append(org)
            if company_l and company_l in ol:
                owned_by_company = True
    institutional = sorted(set(institutional))

    out: list[Flag] = []
    if institutional and not owned_by_company:
        out.append(Flag(
            "ip_owned_by_institution", Severity.RISK,
            "Founder is named as inventor but the assignee is an institution, not the "
            "company. The startup likely needs an explicit field-of-use license before "
            "this IP is investable.",
            {"institutional_assignees": institutional},
        ))
    elif institutional and owned_by_company:
        out.append(Flag("mixed_ip_ownership", Severity.WATCH,
                        "IP is split between the company and an institution — map which "
                        "patents sit where before relying on the moat.",
                        {"institutional_assignees": institutional}))
    if owned_by_company:
        out.append(Flag("ip_owned_by_company", Severity.INFO,
                        f"At least one patent is assigned to {company}."))
    out.append(Flag("patent_count", Severity.INFO,
                    f"{len(patents)} patent(s) name the founder as inventor.",
                    {"patent_ids": [p.patent_id for p in patents]}))
    return out


def _grant_flags(company: str, grants: list[Grant],
                 errors: _SourceErrors | None = None) -> list[Flag]:
    errors = errors or _SourceErrors()
    # Both grant sources down: non-dilutive history is unknown, not absent.
    if not grants and errors.grants_all_failed:
        return [Flag("grant_sources_unavailable", Severity.WATCH,
                     "Could not query SBIR or NIH — non-dilutive funding history is "
                     "UNVERIFIED, not absent.",
                     {"sbir_error": errors.sbir, "nih_error": errors.nih})]
    if not grants:
        msg = ("No SBIR/NIH non-dilutive funding found (US-only sources; "
               "Indian/EU grants are not yet wired in).")
        partial = errors.grants_partial
        if partial:  # one source answered, the other failed — coverage is partial
            msg += f" Note: {partial} did not respond, so this is partial coverage."
        return [Flag("no_grants_found", Severity.INFO, msg,
                     {"partial_source_down": partial} if partial else {})]
    company_l = company.lower()
    to_company = [g for g in grants if g.recipient and company_l and company_l in g.recipient.lower()]
    out: list[Flag] = []
    if to_company:
        total = sum(g.amount or 0 for g in to_company)
        out.append(Flag("non_dilutive_validation", Severity.INFO,
                        f"{len(to_company)} grant(s) to the company (~${total:,.0f}). "
                        "Independent technical validation signal.",
                        {"sources": sorted({g.source for g in to_company})}))
    else:
        out.append(Flag("grants_to_other_entity", Severity.WATCH,
                        "Grants found, but to the founder/an institution rather than the "
                        "company — confirm the funded work actually transfers to the startup."))
    return out
