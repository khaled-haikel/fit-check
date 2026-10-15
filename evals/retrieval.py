"""Retrieval evaluation: does POST /search put the expected cards near the top?

For every requirement with evidence in the dev and test cases, the query is the
requirement's text_en and the relevant cards are its evidence. The ranking is
the one the Lambda returns (fitcheck_api.search.rank over the packaged index).
Reports recall@1/3/5 and MRR per split, the failures (an expected card outside
the top 5) and the measured latency, and gates against evals/baseline/retrieval.json:
recall@5 of a split may not fall by more than 0.02. Without a baseline it only
reports. Needs AWS credentials that may invoke the embedding model.

Usage:
  python evals/retrieval.py --index build/index.json [--output build/retrieval.json]
      [--summary "$GITHUB_STEP_SUMMARY"] [--write-baseline build/retrieval-baseline.json]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from fitcheck_api.content import Content, card_records, load_content
from fitcheck_api.embeddings import Embedder
from fitcheck_api.metrics import K_VALUES, percentile, regressions, score_requirement, summarize
from fitcheck_api.search import SearchIndex, index_version, load_index, rank

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE = ROOT / "evals" / "baseline" / "retrieval.json"
SPLITS = ("dev", "test")
GATE_METRIC = "recall@5"
GATE_TOLERANCE = 0.02
FAILURE_K = 5
METRICS = [f"recall@{k}" for k in K_VALUES] + ["mrr"]
# The query and cards of the deploy's /search smoke test (scripts/smoke_test.sh),
# checked here so a change that would fail that test shows up before the merge.
SMOKE_QUERY = "Terraform remote state and keyless GitHub OIDC deploys"
SMOKE_CARDS = ("fitcheck-terraform-iac", "fitcheck-keyless-cicd")

DEFINITIONS = (
    "recall@k: share of a requirement's expected cards that appear in the top k, averaged over the requirements "
    "(with several expected cards, recall@1 cannot reach 1). "
    "MRR: mean of 1 / rank of the first expected card in the full ranking."
)


def check_index_matches(index: SearchIndex, records: list[dict]) -> None:
    """The eval must score the index built from the cards in this commit."""
    if index.placeholder:
        sys.exit("refusing to evaluate a placeholder index")
    expected = index_version(records, index.model, index.dimensions)
    if index.index_version != expected or list(index.cards) != records:
        sys.exit(f"the index ({index.index_version}) was not built from the current cards ({expected}); rebuild it")


def evaluate(index: SearchIndex, content: Content, embedder: Embedder) -> tuple[list[dict], dict[str, list[float]]]:
    rows = []
    latency: dict[str, list[float]] = {"embedding_ms": [], "search_ms": []}
    for case in sorted(content.cases.values(), key=lambda case: (case.split, case.id)):
        if case.split not in SPLITS:
            continue
        for requirement in case.requirements:
            if not requirement.evidence:
                continue
            relevant = set(requirement.evidence)
            started = time.perf_counter()
            vector = embedder.embed(requirement.text_en)
            embedded = time.perf_counter()
            ranking = rank(index, vector)
            finished = time.perf_counter()
            latency["embedding_ms"].append((embedded - started) * 1000)
            latency["search_ms"].append((finished - embedded) * 1000)

            ranked_ids = [card["id"] for card, _ in ranking]
            rows.append(
                {
                    "split": case.split,
                    "case": case.id,
                    "requirement": requirement.id,
                    "verdict": requirement.verdict,
                    "query": requirement.text_en,
                    "expected": {card_id: ranked_ids.index(card_id) + 1 for card_id in sorted(relevant)},
                    "top": [{"id": card["id"], "score": round(score, 4)} for card, score in ranking[:FAILURE_K]],
                    "scores": score_requirement(ranked_ids, relevant),
                }
            )
    return rows, latency


def smoke_check(index: SearchIndex, embedder: Embedder) -> dict:
    top = [card["id"] for card, _ in rank(index, embedder.embed(SMOKE_QUERY))[:3]]
    return {"query": SMOKE_QUERY, "top": top, "passes": any(card_id in top for card_id in SMOKE_CARDS)}


def latency_summary(values: list[float]) -> dict[str, float]:
    return {"p50": round(percentile(values, 50), 2), "p95": round(percentile(values, 95), 2)}


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def markdown(result: dict, failures: list[dict], baseline: dict | None, problems: list[str]) -> str:
    lines = [
        "## Retrieval evaluation",
        "",
        f"Index `{result['index_version'][:12]}`: {result['cards']} cards, {result['index_bytes']} bytes, "
        f"{result['model']} at {result['dimensions']} dimensions.",
        "",
        "| split | requirements | recall@1 | recall@3 | recall@5 | MRR | baseline recall@5 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for split, summary in result["splits"].items():
        base = (baseline or {}).get("splits", {}).get(split)
        base_cell = f"{base[GATE_METRIC]:.3f}" if base else "none"
        lines.append(
            f"| {split} | {summary['n']} | "
            + " | ".join(f"{summary[metric]:.3f}" for metric in METRICS)
            + f" | {base_cell} |"
        )
    lines += ["", DEFINITIONS, ""]

    if baseline is None:
        lines.append("Gate: no baseline at `evals/baseline/retrieval.json` yet, so this run only reports.")
    elif problems:
        lines.append("Gate: **failed**. " + " ".join(problems))
    else:
        lines.append(f"Gate: passed ({GATE_METRIC} of every split within {GATE_TOLERANCE} of the baseline).")

    embedding, search = result["latency"]["embedding_ms"], result["latency"]["search_ms"]
    lines += [
        "",
        f"Latency on the runner: embedding p50 {embedding['p50']} ms, p95 {embedding['p95']} ms; "
        f"search p50 {search['p50']} ms, p95 {search['p95']} ms.",
        "",
        f"Deploy smoke query \"{result['smoke']['query']}\": top 3 "
        + ", ".join(f"`{card_id}`" for card_id in result["smoke"]["top"])
        + (" (passes)." if result["smoke"]["passes"] else f" (**would fail**: expects one of {', '.join(SMOKE_CARDS)})."),
        "",
        f"### Failures: {len(failures)} (an expected card outside the top {FAILURE_K})",
        "",
    ]
    if failures:
        lines += ["| split | requirement | expected (rank) | top 3 returned |", "|---|---|---|---|"]
        for row in failures:
            expected = ", ".join(f"`{card_id}` ({position})" for card_id, position in row["expected"].items())
            top = ", ".join(f"`{hit['id']}` {hit['score']:.3f}" for hit in row["top"][:3])
            requirement = _cell(f"{row['case']} {row['requirement']}: {row['query']}")
            lines.append(f"| {row['split']} | {requirement} | {expected} | {top} |")
    return "\n".join(lines) + "\n"


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--output", type=Path, default=ROOT / "build" / "retrieval.json")
    parser.add_argument("--summary", type=Path, help="append the Markdown summary here (e.g. $GITHUB_STEP_SUMMARY)")
    parser.add_argument("--write-baseline", type=Path, help="also write these numbers as a baseline candidate")
    args = parser.parse_args()

    content = load_content(ROOT / "evidence", ROOT / "evals" / "cases")
    if content.errors:
        for error in content.errors:
            print(f"error: {error}", file=sys.stderr)
        sys.exit(1)
    index = load_index(args.index)
    check_index_matches(index, card_records(content))

    embedder = Embedder(index.model, index.dimensions, max_attempts=8)
    rows, latency = evaluate(index, content, embedder)
    splits = {split: summarize(row["scores"] for row in rows if row["split"] == split) for split in SPLITS}
    failures = [row for row in rows if row["scores"][f"recall@{FAILURE_K}"] < 1]
    result = {
        "index_version": index.index_version,
        "model": index.model,
        "dimensions": index.dimensions,
        "cards": len(index.cards),
        "index_bytes": args.index.stat().st_size,
        "splits": splits,
        "latency": {name: latency_summary(values) for name, values in latency.items()},
        "smoke": smoke_check(index, embedder),
        "failures": len(failures),
        "requirements": rows,
    }

    baseline = json.loads(args.baseline.read_text(encoding="utf-8")) if args.baseline.exists() else None
    problems = regressions(splits, baseline["splits"], GATE_METRIC, GATE_TOLERANCE) if baseline else []
    result["gate"] = {"baseline": baseline is not None, "passed": not problems, "problems": problems}

    write_json(args.output, result)
    if args.write_baseline:
        write_json(args.write_baseline, {"index_version": index.index_version, "model": index.model, "splits": splits})
    report = markdown(result, failures, baseline, problems)
    print(report)
    if args.summary:
        with args.summary.open("a", encoding="utf-8") as summary:
            summary.write(report)
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
