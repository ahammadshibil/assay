"""Optional synthesis layer. The engine already produces deterministic flags;
this turns them into an analyst-readable verdict. It is strictly optional — the
tool is fully usable (and fully open) without an Anthropic key.
"""
from __future__ import annotations

import json
import os

from .models import ClaimReport, ProvenanceReport

_SYS = (
    "You are a deeptech VC diligence assistant. Given a structured provenance "
    "report on a founder and company, write a 4-6 sentence verdict for an "
    "investment analyst. Lead with the single most decision-relevant fact "
    "(usually IP ownership). Be concrete, cite the flags, and do not invent "
    "facts beyond the data provided. End with one explicit next step."
)

# --- adversarial claim adjudication (Co-Scientist Reflection+Ranking, adapted) ---
# DeepMind's Co-Scientist (Nature, 2026) spends most of its compute on *verifying*
# claims against the literature via a "virtual peer reviewer" (Reflection) and an
# idea tournament (Ranking). We adapt the inverse task — given a founder's existing
# claim, does each surfaced paper actually SUPPORT it, or just match keywords? Each
# paper gets a single self-adversarial pass (steelman support, steelman refutation,
# then rule), and a meta-review aggregates. This turns the *structural* grade ("does
# independent literature exist?") into a *verified* one ("does it support the claim?").

_PAPER_ADJ_SYS = (
    "You are a skeptical peer reviewer. You are given a scientific CLAIM and ONE "
    "paper (title, venue, year, abstract). Decide whether that paper actually "
    "supports the claim. Reason adversarially in your head: first steelman how it "
    "SUPPORTS the claim, then steelman how it does NOT (only shares keywords, studies "
    "a different system/model, or contradicts it), then rule. Do not be charitable to "
    "the claim; keyword overlap is not support. Respond with ONLY a JSON object: "
    '{"verdict": "SUPPORTS|PARTIAL|IRRELEVANT|CONTRADICTS|UNCLEAR", '
    '"confidence": "low|medium|high", "reason": "<=20 words"}'
)

_META_SYS = (
    "You are a scientific diligence assistant for a deeptech VC with a biology "
    "background. You are given a CLAIM, the deterministic evidence grade, and a set "
    "of per-paper adjudications (whether each paper actually supports the claim, and "
    "whether it is from the founders' own group). Write a 4-6 sentence verdict for an "
    "investment analyst. Lead with whether the claim is genuinely supported, and by "
    "whom (independent groups vs the founders only). Name the single strongest "
    "supporting paper. Flag contradictions or keyword-only matches explicitly. Do not "
    "invent facts beyond the adjudications. End with one explicit next step."
)

_SUPPORTING = {"SUPPORTS", "PARTIAL"}


def _llm(system: str, user: str, model: str | None, max_tokens: int = 600) -> str:
    try:
        import anthropic  # noqa: PLC0415
    except ImportError:
        return "(synthesis skipped: `pip install anthropic` to enable)"
    if not os.getenv("ANTHROPIC_API_KEY"):
        return "(synthesis skipped: set ANTHROPIC_API_KEY to enable)"
    model = model or os.getenv("ASSAY_MODEL", "claude-sonnet-4-6")
    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=model, max_tokens=max_tokens, system=system,
        messages=[{"role": "user", "content": user}],
    )
    return "".join(b.text for b in msg.content if getattr(b, "type", None) == "text").strip()


def _extract_json(text: str) -> dict:
    """Defensively pull the first {...} object out of an LLM response."""
    start, depth = text.find("{"), 0
    if start < 0:
        return {}
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    return {}
    return {}


def _select_for_adjudication(report: ClaimReport, top_k: int) -> list:
    """Pick the most decision-relevant works: peer-reviewed primary first, then by
    citations — but always keep at least the independent works (they set the grade)."""
    works = [w for w in report.works if not w.is_retracted]
    ranked = sorted(works, key=lambda w: (w.is_primary_article, w.cited_by_count), reverse=True)
    chosen = ranked[:top_k]
    for w in report.independent_works:  # never drop an independent paper from the panel
        if w not in chosen:
            chosen.append(w)
    return chosen


def _adjudicate_paper(claim: str, work, model: str | None, call=_llm) -> dict:
    body = (f"CLAIM: {claim}\n\nPAPER\n title: {work.title}\n venue: {work.venue}\n "
            f"year: {work.year}\n abstract: {work.abstract or '(no abstract available)'}")
    parsed = _extract_json(call(_PAPER_ADJ_SYS, body, model, 200))
    return {
        "paper": f"{work.title} ({work.venue}, {work.year})",
        "founder_authored": work.founder_authored,
        "cited_by_count": work.cited_by_count,
        "verdict": (parsed.get("verdict") or "UNCLEAR").upper(),
        "confidence": parsed.get("confidence") or "low",
        "reason": parsed.get("reason") or "",
    }


def summarize_adjudications(adjudications: list[dict]) -> dict:
    """Pure aggregation: turn per-paper verdicts into a *verified* stance on the claim.
    Distinct from the structural grade — this reflects whether papers actually support
    the claim, not merely that matching literature exists."""
    supporting = [a for a in adjudications if a.get("verdict") in _SUPPORTING]
    contradicting = [a for a in adjudications if a.get("verdict") == "CONTRADICTS"]
    indep_support = [a for a in supporting if a.get("founder_authored") is False]
    founder_support = [a for a in supporting if a.get("founder_authored") is True]

    if contradicting and not supporting:
        stance = "REFUTED"
    elif indep_support:
        stance = "INDEPENDENTLY SUPPORTED"
    elif founder_support:
        stance = "SUPPORTED (founders' group only)"
    elif supporting:
        stance = "SUPPORTED (independence unknown)"
    else:
        stance = "NOT SUPPORTED — surfaced literature matches keywords but does not establish the claim"

    return {
        "stance": stance,
        "counts": {
            "supporting": len(supporting), "independent_supporting": len(indep_support),
            "founder_supporting": len(founder_support), "contradicting": len(contradicting),
            "adjudicated": len(adjudications),
        },
        "contradicting": contradicting,
        "lines": [f"{a.get('verdict', 'UNCLEAR'):11} {'[founder]' if a.get('founder_authored') else '[indep]   ' if a.get('founder_authored') is False else '[?]       '} "
                  f"{(a.get('paper') or '')[:70]} — {a.get('reason', '')}" for a in adjudications],
    }


def synthesize_science(report: ClaimReport, model: str | None = None, top_k: int = 5) -> str:
    """Adversarially adjudicate whether the surfaced literature actually supports the
    claim (Co-Scientist Reflection+Ranking pattern). Populates report.adjudications and
    returns an analyst verdict, or a skip message if the SDK/key is absent."""
    try:
        import anthropic  # noqa: F401,PLC0415
    except ImportError:
        return "(synthesis skipped: `pip install anthropic` to enable)"
    if not os.getenv("ANTHROPIC_API_KEY"):
        return "(synthesis skipped: set ANTHROPIC_API_KEY to enable)"
    if not report.works:
        return "(no literature surfaced to adjudicate)"

    adjudications = [_adjudicate_paper(report.claim, w, model)
                     for w in _select_for_adjudication(report, top_k)]
    report.adjudications = adjudications
    summary = summarize_adjudications(adjudications)

    user = (f"CLAIM: {report.claim}\n"
            f"deterministic grade: {report.grade.value}\n"
            f"verified stance: {summary['stance']}\n"
            f"counts: {json.dumps(summary['counts'])}\n\n"
            "per-paper adjudications:\n" + "\n".join(summary["lines"]))
    paragraph = _llm(_META_SYS, user, model)
    return f"STANCE: {summary['stance']}\n\n{paragraph}"


def synthesize(report: ProvenanceReport, model: str | None = None) -> str:
    """Return an LLM verdict, or a helpful message if the SDK/key is absent."""
    try:
        import anthropic  # noqa: PLC0415
    except ImportError:
        return "(synthesis skipped: `pip install anthropic` to enable)"
    if not os.getenv("ANTHROPIC_API_KEY"):
        return "(synthesis skipped: set ANTHROPIC_API_KEY to enable)"

    model = model or os.getenv("ASSAY_MODEL", "claude-sonnet-4-6")
    payload = json.dumps(report.to_dict(), indent=2, default=str)
    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=model,
        max_tokens=600,
        system=_SYS,
        messages=[{"role": "user", "content": f"Provenance report:\n{payload}"}],
    )
    return "".join(b.text for b in msg.content if getattr(b, "type", None) == "text").strip()
