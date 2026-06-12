"""Tests for the adversarial adjudication layer — offline, no API key.

Covers (1) abstract reconstruction from OpenAlex's inverted index, (2) the pure
aggregation that turns per-paper verdicts into a *verified* stance, and (3) the
per-paper adjudicator with an injected fake LLM. The key property: keyword hits
that don't actually support the claim yield NOT SUPPORTED even when the structural
grade said literature exists.
"""
from assay.science import reconstruct_abstract
from assay.synthesize import _adjudicate_paper, _extract_json, summarize_adjudications
from assay.models import Work


# --------------------------------------------------------- abstract rebuild ----
def test_reconstruct_abstract_orders_by_position():
    inv = {"CSP-TTK21": [0], "activates": [1], "p300": [2], "acetyltransferase": [3]}
    assert reconstruct_abstract(inv) == "CSP-TTK21 activates p300 acetyltransferase"


def test_reconstruct_abstract_handles_repeats_and_none():
    assert reconstruct_abstract(None) is None
    assert reconstruct_abstract({}) is None
    inv = {"the": [0, 2], "cell": [1], "nucleus": [3]}
    assert reconstruct_abstract(inv) == "the cell the nucleus"


# ------------------------------------------------------------ json extract ----
def test_extract_json_from_noisy_text():
    txt = 'Sure! {"verdict": "SUPPORTS", "confidence": "high", "reason": "direct"} done'
    assert _extract_json(txt)["verdict"] == "SUPPORTS"


def test_extract_json_bad_returns_empty():
    assert _extract_json("no json here") == {}


# ------------------------------------------------------------- aggregation ----
def _adj(verdict, founder, reason="r", paper="P (V, 2020)"):
    return {"verdict": verdict, "founder_authored": founder, "reason": reason, "paper": paper}


def test_independent_support_is_independently_supported():
    s = summarize_adjudications([_adj("SUPPORTS", False), _adj("IRRELEVANT", None)])
    assert s["stance"] == "INDEPENDENTLY SUPPORTED"
    assert s["counts"]["independent_supporting"] == 1


def test_only_founder_support_is_founders_group_only():
    s = summarize_adjudications([_adj("SUPPORTS", True), _adj("PARTIAL", True)])
    assert s["stance"] == "SUPPORTED (founders' group only)"


def test_all_irrelevant_is_not_supported_despite_keyword_hits():
    # The whole point: literature matched keywords but none actually supports the claim.
    s = summarize_adjudications([_adj("IRRELEVANT", False), _adj("IRRELEVANT", True)])
    assert s["stance"].startswith("NOT SUPPORTED")


def test_contradiction_without_support_is_refuted():
    s = summarize_adjudications([_adj("CONTRADICTS", False)])
    assert s["stance"] == "REFUTED"
    assert s["counts"]["contradicting"] == 1


def test_support_with_unknown_independence():
    s = summarize_adjudications([_adj("SUPPORTS", None)])
    assert s["stance"] == "SUPPORTED (independence unknown)"


# --------------------------------------------------- per-paper adjudicator ----
def test_adjudicate_paper_parses_injected_llm():
    calls = {"n": 0}

    def fake_call(system, user, model, max_tokens=200):
        calls["n"] += 1
        assert "CLAIM:" in user and "abstract:" in user  # claim + abstract are passed in
        return '{"verdict": "supports", "confidence": "high", "reason": "shows the effect"}'

    w = Work(title="A paper", venue="Nature", year=2024, founder_authored=False,
             abstract="we show the effect directly")
    out = _adjudicate_paper("the claim", w, model=None, call=fake_call)
    assert out["verdict"] == "SUPPORTS"      # normalized to upper
    assert out["founder_authored"] is False
    assert calls["n"] == 1
