"""Evaluate Assay's science mode against the SciFact benchmark.

SciFact (Wadden et al., 2020 — https://github.com/allenai/scifact) is the standard
scientific claim-verification dataset: each claim is labelled SUPPORT / CONTRADICT /
NEI (not enough info) against a curated corpus, with rationale sentences.

Two modes, because Assay has two layers:

  end2end  (no API key)  — Assay does its OWN retrieval from OpenAlex and grades the
                           claim. We measure RETRIEVAL/EXISTENCE: when the gold says a
                           claim is verifiable (SUPPORT/CONTRADICT), does Assay find
                           peer-reviewed literature for it? This is the deterministic
                           system's headline axis — and the one SciFact itself flags as
                           the bottleneck.
  grader   (needs key)   — bypass retrieval; feed the gold cited abstract straight to
                           the adversarial adjudicator and score the 3-way support
                           judgement against the gold label. This matches SciFact's
                           actual task.

CAVEAT (read before quoting a number): SciFact's NEI is *corpus-relative* — "the
curated corpus has no evidence." Assay searches the open web (all of OpenAlex), so it
can legitimately find literature for an NEI claim. end2end therefore measures recall
on verifiable claims cleanly, but its NEI "false-positive" rate is an upper bound, not
a true error rate. Don't read end2end specificity as Assay being wrong; read it as
"Assay finds *something* for almost any biomedical claim" — which is exactly the
over-retrieval risk the generic-claim guard and --synthesize exist to contain.

Get the data (3 MB, not vendored):
    wget https://scifact.s3-us-west-2.amazonaws.com/release/latest/data.tar.gz
    tar xzf data.tar.gz                      # -> data/claims_dev.jsonl, data/corpus.jsonl

Run:
    python eval/scifact_eval.py --data-dir data --limit 80
    python eval/scifact_eval.py --data-dir data --limit 80 --mode grader   # needs ANTHROPIC_API_KEY
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from assay.models import EvidenceGrade, Work          # noqa: E402
from assay.science import verify_claim                # noqa: E402

# Assay grades that mean "found peer-reviewed / preprint literature for the claim".
_EVIDENCE_GRADES = {
    EvidenceGrade.REPLICATED, EvidenceGrade.PEER_REVIEWED,
    EvidenceGrade.SINGLE_GROUP, EvidenceGrade.PREPRINT_ONLY, EvidenceGrade.CONTRADICTED,
}


def gold_label(row: dict) -> str:
    ev = row.get("evidence") or {}
    if not ev:
        return "NEI"
    labels = {s["label"] for doc in ev.values() for s in doc}
    return "CONTRADICT" if "CONTRADICT" in labels else "SUPPORT"


def stratified(rows: list[dict], limit: int) -> list[dict]:
    """Deterministic, label-balanced sample (sorted by id for reproducibility)."""
    by_label: dict[str, list[dict]] = defaultdict(list)
    for r in sorted(rows, key=lambda r: r["id"]):
        by_label[gold_label(r)].append(r)
    per = max(1, limit // len(by_label))
    out: list[dict] = []
    for label in ("SUPPORT", "CONTRADICT", "NEI"):
        out += by_label.get(label, [])[:per]
    return out[:limit]


# ----------------------------------------------------------------- end2end ----
async def run_end2end(rows: list[dict], mailto: str | None, concurrency: int) -> None:
    sem = asyncio.Semaphore(concurrency)

    async def one(row):
        async with sem:
            rep = await verify_claim(row["claim"], mailto=mailto, max_works=25)
            return gold_label(row), rep.grade

    results = await asyncio.gather(*[one(r) for r in rows])

    # grade distribution per gold label
    dist: dict[str, Counter] = defaultdict(Counter)
    for gold, grade in results:
        dist[gold][grade.value] += 1

    print(f"\nSciFact end2end — {len(results)} claims (Assay retrieves + grades)\n" + "=" * 64)
    print(f"{'gold':11} {'n':>4}  grade distribution (Assay)")
    print("-" * 64)
    for gold in ("SUPPORT", "CONTRADICT", "NEI"):
        d = dist.get(gold)
        if not d:
            continue
        n = sum(d.values())
        body = ", ".join(f"{g}:{c}" for g, c in d.most_common())
        print(f"{gold:11} {n:>4}  {body}")

    # headline metric: recall on verifiable claims (did Assay find the science?)
    verifiable = [(g, gr) for g, gr in results if g in ("SUPPORT", "CONTRADICT")]
    found = sum(1 for _, gr in verifiable if gr in _EVIDENCE_GRADES)
    nei = [(g, gr) for g, gr in results if g == "NEI"]
    nei_found = sum(1 for _, gr in nei if gr in _EVIDENCE_GRADES)
    too_generic = sum(1 for _, gr in results if gr is EvidenceGrade.TOO_GENERIC)

    print("\nMetrics\n" + "-" * 64)
    if verifiable:
        print(f"recall on verifiable claims : {found}/{len(verifiable)} = {found/len(verifiable):.0%}  "
              "(found peer-reviewed/preprint evidence when gold says SUPPORT/CONTRADICT)")
    if nei:
        print(f"NEI evidence-found rate     : {nei_found}/{len(nei)} = {nei_found/len(nei):.0%}  "
              "(upper bound — Assay searches open web, SciFact NEI is corpus-relative)")
    print(f"claims flagged TOO_GENERIC   : {too_generic}/{len(results)}  "
          "(guard sanity — should be near-zero on specific biomedical claims)")


# ------------------------------------------------------------------ grader ----
async def run_grader(rows: list[dict], corpus: dict[int, dict], model: str | None) -> None:
    from assay.synthesize import _adjudicate_paper  # noqa: PLC0415

    def to_work(doc_id: int) -> Work | None:
        d = corpus.get(doc_id)
        if not d:
            return None
        return Work(title=d.get("title"), type="article", is_primary_article=True,
                    abstract=" ".join(d.get("abstract") or []), authors=["x"])

    confusion: Counter = Counter()
    n = 0
    for row in rows:
        gold = gold_label(row)
        doc_id = (row.get("cited_doc_ids") or [None])[0]
        work = to_work(doc_id) if doc_id else None
        if work is None:
            continue
        adj = _adjudicate_paper(row["claim"], work, model)
        v = adj["verdict"]
        pred = "SUPPORT" if v == "SUPPORTS" else "CONTRADICT" if v == "CONTRADICTS" else "NEI"
        confusion[(gold, pred)] += 1
        n += 1

    print(f"\nSciFact grader — {n} claims (gold abstract → adjudicator)\n" + "=" * 56)
    labels = ("SUPPORT", "CONTRADICT", "NEI")
    print(f"{'gold \\ pred':14}" + "".join(f"{p:>12}" for p in labels))
    correct = 0
    for g in labels:
        row_counts = [confusion[(g, p)] for p in labels]
        correct += confusion[(g, g)]
        print(f"{g:14}" + "".join(f"{c:>12}" for c in row_counts))
    if n:
        print(f"\naccuracy: {correct}/{n} = {correct/n:.0%}")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Evaluate Assay science mode on SciFact.")
    ap.add_argument("--data-dir", default="data", help="dir with claims_dev.jsonl + corpus.jsonl")
    ap.add_argument("--mode", choices=["end2end", "grader"], default="end2end")
    ap.add_argument("--limit", type=int, default=80)
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--email", default=None, help="mailto for the OpenAlex polite pool")
    ap.add_argument("--model", default=None)
    args = ap.parse_args()

    claims = [json.loads(l) for l in open(f"{args.data_dir}/claims_dev.jsonl", encoding="utf-8")]
    rows = stratified(claims, args.limit)

    if args.mode == "end2end":
        asyncio.run(run_end2end(rows, args.email, args.concurrency))
    else:
        corpus = {d["doc_id"]: d for d in
                  (json.loads(l) for l in open(f"{args.data_dir}/corpus.jsonl", encoding="utf-8"))}
        asyncio.run(run_grader(rows, corpus, args.model))
    return 0


if __name__ == "__main__":
    sys.exit(main())
