"""Retrieval evaluation: does POST /search put the expected cards near the top?

The query is each requirement's text_en, ranked with fitcheck_api.search.search,
the function the Lambda serves. Two channels are measured:

- evidence: meets and partial requirements; expected = the cards they cite,
  gap cards left out. hit@5, recall@5 and MRR.
- gaps: requirements that cite a gap card; expected = those gap cards. hit@1
  and hit@3, for configurations that keep gaps in a channel of their own.

Every configuration in ABLATION is scored on the same index and the same query
embeddings, which gives the ablation table. The configuration the index serves
is also gated against evals/baseline/retrieval.json (hit@5 and recall@5 of a
split may not fall by more than 0.02), reported failure by failure, and timed.
Without a baseline the run only reports. Needs AWS credentials that may invoke
the embedding model.

Usage:
  python evals/retrieval.py --index build/index.json [--splits dev,test]
      [--output build/retrieval.json] [--summary "$GITHUB_STEP_SUMMARY"]
      [--write-baseline build/retrieval-baseline.json]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from fitcheck_api.content import Content, card_records, load_content
from fitcheck_api.embeddings import Embedder
from fitcheck_api.metrics import percentile, regressions, score_ranking, summarize
from fitcheck_api.search import RetrievalConfig, SearchIndex, index_document, index_version, load_index, search

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE = ROOT / "evals" / "baseline" / "retrieval.json"
SPLITS = ("dev", "test")
EVIDENCE_VERDICTS = ("meets", "partial")
GATE_METRICS = ("hit@5", "recall@5")
GATE_TOLERANCE = 0.02
FAILURE_K = 5
GAP_K = 3
EMBED_WORKERS = 4
# The query and cards of the deploy's /search smoke test (scripts/smoke_test.sh),
# checked here so a change that would fail that test shows up before the merge.
SMOKE_QUERY = "Terraform remote state and keyless GitHub OIDC deploys"
SMOKE_CARDS = ("fitcheck-terraform-iac", "fitcheck-keyless-cicd")

# The configurations of the ablation table, each adding one change to the one
# before. The index serves the one in config/retrieval.json.
ABLATION: list[tuple[str, RetrievalConfig]] = [
    ("v0", RetrievalConfig(chunks="card")),
    ("+chunks", RetrievalConfig(chunks="claims")),
    ("+hybrid", RetrievalConfig(chunks="claims", hybrid=True)),
]

DEFINITIONS = (
    "Evidence channel: meets and partial requirements, expecting the cards they cite minus gap cards. "
    "hit@5: some expected card in the top 5. recall@5: share of the expected cards in the top 5. "
    "MRR: mean of 1 / rank of the first expected card. "
    "Gap channel: requirements that cite a gap card, expecting those gap cards among the gap results "
    "(hit@1, hit@3); a dash means the configuration has no gap channel. All metrics are averaged over requirements."
)


@dataclass(frozen=True)
class EvalQuery:
    split: str
    case: str
    requirement: str
    category: str
    verdict: str
    text: str
    # Positive cards the evidence channel should return.
    evidence: frozenset[str]
    # Gap cards the gap channel should return.
    gaps: frozenset[str]


def select_queries(content: Content, splits: tuple[str, ...]) -> list[EvalQuery]:
    gap_cards = {card_id for card_id, card in content.cards.items() if card.kind == "gap"}
    queries = []
    for case in sorted(content.cases.values(), key=lambda case: (case.split, case.id)):
        if case.split not in splits:
            continue
        for requirement in case.requirements:
            cited = set(requirement.evidence)
            evidence = cited - gap_cards if requirement.verdict in EVIDENCE_VERDICTS else set()
            gaps = cited & gap_cards
            if evidence or gaps:
                queries.append(
                    EvalQuery(
                        split=case.split,
                        case=case.id,
                        requirement=requirement.id,
                        category=requirement.category,
                        verdict=requirement.verdict,
                        text=requirement.text_en,
                        evidence=frozenset(evidence),
                        gaps=frozenset(gaps),
                    )
                )
    return queries


def has_gap_channel(config: RetrievalConfig) -> bool:
    return False


def serving_config() -> RetrievalConfig:
    return RetrievalConfig.from_dict(json.loads((ROOT / "config" / "retrieval.json").read_text(encoding="utf-8")))


def check_index_matches(index: SearchIndex, records: list[dict], config: RetrievalConfig) -> None:
    """The eval must score the index built from the cards and the configuration in this commit."""
    if index.placeholder:
        sys.exit("refusing to evaluate a placeholder index")
    expected = index_version(index_document(records, index.model, index.dimensions, config))
    if index.index_version != expected or list(index.cards) != records or index.config != config:
        sys.exit(f"the index ({index.index_version}) was not built from the current cards and config ({expected}); rebuild it")


def configurations(serving: RetrievalConfig) -> tuple[list[tuple[str, RetrievalConfig]], str]:
    """The ablation, plus the served configuration if it is not one of its rows; and the served row's name."""
    for name, config in ABLATION:
        if config == serving:
            return list(ABLATION), name
    return [*ABLATION, ("serving", serving)], "serving"


def embed_queries(embedder: Embedder, texts: list[str]) -> tuple[dict[str, list[float]], list[float]]:
    """Embeds each distinct text once, in parallel, timing every call."""
    latencies: list[float] = []

    def timed(text: str) -> list[float]:
        started = time.perf_counter()
        vector = embedder.embed(text)
        latencies.append((time.perf_counter() - started) * 1000)
        return vector

    distinct = sorted(set(texts))
    with ThreadPoolExecutor(max_workers=EMBED_WORKERS) as pool:
        vectors = dict(zip(distinct, pool.map(timed, distinct)))
    return vectors, latencies


def _channel(ranked_ids: list[str], expected: frozenset[str], top: list[dict]) -> dict:
    return {
        "expected": {card_id: (ranked_ids.index(card_id) + 1 if card_id in ranked_ids else None) for card_id in sorted(expected)},
        "top": top,
        "scores": score_ranking(ranked_ids, set(expected)),
    }


def _top(hits, k: int) -> list[dict]:
    return [{"id": hit.card["id"], "score": round(hit.score, 4), "similarity": round(hit.similarity, 4)} for hit in hits[:k]]


def evaluate(
    index: SearchIndex, queries: list[EvalQuery], vectors: dict[str, list[float]], config: RetrievalConfig
) -> tuple[list[dict], list[float]]:
    rows, search_ms = [], []
    gap_channel = has_gap_channel(config)
    for query in queries:
        started = time.perf_counter()
        result = search(index, vectors[query.text], query.text, query.category, config)
        search_ms.append((time.perf_counter() - started) * 1000)
        row = {
            "split": query.split,
            "case": query.case,
            "requirement": query.requirement,
            "category": query.category,
            "verdict": query.verdict,
            "query": query.text,
            "evidence": None,
            "gap": None,
        }
        if query.evidence:
            ranked = [hit.card["id"] for hit in result.evidence]
            row["evidence"] = _channel(ranked, query.evidence, _top(result.evidence, FAILURE_K))
        if query.gaps and gap_channel:
            ranked = [hit.card["id"] for hit in result.gaps]
            row["gap"] = _channel(ranked, query.gaps, _top(result.gaps, GAP_K))
        rows.append(row)
    return rows, search_ms


def summarize_rows(rows: list[dict], splits: tuple[str, ...], gap_channel: bool) -> dict[str, dict]:
    summary = {}
    for split in splits:
        evidence = summarize(row["evidence"]["scores"] for row in rows if row["split"] == split and row["evidence"])
        gaps = summarize(row["gap"]["scores"] for row in rows if row["split"] == split and row["gap"]) if gap_channel else None
        summary[split] = {"evidence": evidence, "gap": gaps}
    return summary


def smoke_check(index: SearchIndex, vector: list[float], config: RetrievalConfig) -> dict:
    top = [hit.card["id"] for hit in search(index, vector, SMOKE_QUERY, None, config).evidence[:3]]
    return {"query": SMOKE_QUERY, "top": top, "passes": any(card_id in top for card_id in SMOKE_CARDS)}


def latency_summary(values: list[float]) -> dict[str, float]:
    return {"p50": round(percentile(values, 50), 2), "p95": round(percentile(values, 95), 2)}


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _metric(summary: dict | None, key: str) -> str:
    if summary is None:
        return "-"
    if key == "n":
        return str(summary["n"])
    return f"{summary[key]:.3f}" if key in summary else "–"


def _ranks(expected: dict[str, int | None]) -> str:
    return ", ".join(f"`{card_id}` ({rank if rank is not None else 'excluded'})" for card_id, rank in expected.items())


def markdown(result: dict, baseline: dict | None, problems: list[str]) -> str:
    serving = result["serving"]
    lines = [
        "## Retrieval evaluation",
        "",
        f"Index `{result['index_version'][:12]}`: {result['cards']} cards, {result['vectors']} vectors, "
        f"{result['index_bytes']} bytes, {result['model']} at {result['dimensions']} dimensions. "
        f"Splits: {', '.join(result['splits'])}. Serving configuration: `{serving}` "
        f"(`{json.dumps(result['configurations'][serving])}`).",
        "",
        "### Ablation",
        "",
        "| configuration | split | evidence n | hit@5 | recall@5 | MRR | gap n | gap hit@1 | gap hit@3 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, splits in result["ablation"].items():
        for split, summary in splits.items():
            evidence, gap = summary["evidence"], summary["gap"]
            label = f"**{name}**" if name == serving else name
            lines.append(
                f"| {label} | {split} | {_metric(evidence, 'n')} | {_metric(evidence, 'hit@5')} | {_metric(evidence, 'recall@5')} | "
                f"{_metric(evidence, 'mrr')} | {_metric(gap, 'n')} | {_metric(gap, 'hit@1')} | {_metric(gap, 'hit@3')} |"
            )
    lines += ["", DEFINITIONS, ""]

    if baseline is None:
        lines.append("Gate: no baseline at `evals/baseline/retrieval.json` yet, so this run only reports.")
    elif problems:
        lines.append("Gate: **failed**. " + " ".join(problems))
    else:
        lines.append(
            f"Gate: passed ({' and '.join(GATE_METRICS)} of every split within {GATE_TOLERANCE} of the baseline, "
            f"configuration `{baseline.get('configuration', '?')}`)."
        )

    embedding, search_ms = result["latency"]["embedding_ms"], result["latency"]["search_ms"]
    smoke = result["smoke"]
    lines += [
        "",
        f"Latency on the runner: embedding p50 {embedding['p50']} ms, p95 {embedding['p95']} ms; "
        f"search (`{serving}`) p50 {search_ms['p50']} ms, p95 {search_ms['p95']} ms.",
        "",
        f"Deploy smoke query \"{smoke['query']}\": top 3 "
        + ", ".join(f"`{card_id}`" for card_id in smoke["top"])
        + (" (passes)." if smoke["passes"] else f" (**would fail**: expects one of {', '.join(SMOKE_CARDS)})."),
    ]

    rows = result["rows"][serving]
    failures = [row for row in rows if row["evidence"] and row["evidence"]["scores"][f"recall@{FAILURE_K}"] < 1]
    lines += [
        "",
        f"### Evidence failures of `{serving}`: {len(failures)} (an expected card outside the top {FAILURE_K}; "
        f"{sum(1 for row in failures if row['evidence']['scores'][f'hit@{FAILURE_K}'] == 0)} with none in the top {FAILURE_K})",
        "",
    ]
    if failures:
        lines += ["| split | requirement | category | hit@5 | expected (rank) | top 3 returned |", "|---|---|---|---|---|---|"]
        for row in failures:
            evidence = row["evidence"]
            top = ", ".join(f"`{hit['id']}`" for hit in evidence["top"][:3])
            requirement = _cell(f"{row['case']} {row['requirement']}: {row['query']}")
            hit = "yes" if evidence["scores"][f"hit@{FAILURE_K}"] else "**no**"
            lines.append(f"| {row['split']} | {requirement} | {row['category']} | {hit} | {_ranks(evidence['expected'])} | {top} |")

    gap_misses = [row for row in rows if row["gap"] and row["gap"]["scores"][f"hit@{GAP_K}"] == 0]
    if any(row["gap"] for row in rows):
        lines += ["", f"### Gap channel misses of `{serving}`: {len(gap_misses)} (no expected gap in the top {GAP_K})", ""]
        if gap_misses:
            lines += ["| split | requirement | expected (rank) | top 3 gaps |", "|---|---|---|---|"]
            for row in gap_misses:
                top = ", ".join(f"`{hit['id']}`" for hit in row["gap"]["top"])
                requirement = _cell(f"{row['case']} {row['requirement']}: {row['query']}")
                lines.append(f"| {row['split']} | {requirement} | {_ranks(row['gap']['expected'])} | {top} |")
    return "\n".join(lines) + "\n"


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def parse_splits(value: str) -> tuple[str, ...]:
    splits = tuple(split.strip() for split in value.split(",") if split.strip())
    unknown = [split for split in splits if split not in SPLITS]
    if not splits or unknown:
        raise argparse.ArgumentTypeError(f"splits must be a comma-separated subset of {', '.join(SPLITS)}")
    return splits


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--splits", type=parse_splits, default=SPLITS, help="comma-separated, default dev,test")
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--output", type=Path, default=ROOT / "build" / "retrieval.json")
    parser.add_argument("--summary", type=Path, help="append the Markdown summary here (e.g. $GITHUB_STEP_SUMMARY)")
    parser.add_argument("--write-baseline", type=Path, help="also write the serving configuration's numbers as a baseline candidate")
    args = parser.parse_args()

    content = load_content(ROOT / "evidence", ROOT / "evals" / "cases")
    if content.errors:
        for error in content.errors:
            print(f"error: {error}", file=sys.stderr)
        sys.exit(1)
    index = load_index(args.index)
    check_index_matches(index, card_records(content), serving_config())
    evaluated, serving_name = configurations(index.config)

    queries = select_queries(content, args.splits)
    embedder = Embedder(index.model, index.dimensions, max_attempts=8)
    vectors, embedding_ms = embed_queries(embedder, [query.text for query in queries] + [SMOKE_QUERY])

    ablation, all_rows, search_ms = {}, {}, []
    for name, config in evaluated:
        rows, timings = evaluate(index, queries, vectors, config)
        all_rows[name] = rows
        ablation[name] = summarize_rows(rows, args.splits, has_gap_channel(config))
        if name == serving_name:
            search_ms = timings

    serving = {split: summary["evidence"] for split, summary in ablation[serving_name].items()}
    result = {
        "index_version": index.index_version,
        "model": index.model,
        "dimensions": index.dimensions,
        "cards": len(index.cards),
        "vectors": len(index.vectors),
        "chunks": {kind: sum(1 for chunk in index.chunks if chunk.kind == kind) for kind in ("card", "summary", "claim")},
        "index_bytes": args.index.stat().st_size,
        "splits": list(args.splits),
        "serving": serving_name,
        "configurations": {name: config.to_dict() for name, config in evaluated},
        "ablation": ablation,
        "latency": {"embedding_ms": latency_summary(embedding_ms), "search_ms": latency_summary(search_ms)},
        "smoke": smoke_check(index, vectors[SMOKE_QUERY], index.config),
        "rows": all_rows,
    }

    baseline = json.loads(args.baseline.read_text(encoding="utf-8")) if args.baseline.exists() else None
    problems = regressions(serving, baseline["splits"], GATE_METRICS, GATE_TOLERANCE) if baseline else []
    result["gate"] = {"baseline": baseline is not None, "passed": not problems, "problems": problems}

    write_json(args.output, result)
    if args.write_baseline:
        write_json(
            args.write_baseline,
            {
                "configuration": serving_name,
                "retrieval": index.config.to_dict(),
                "index_version": index.index_version,
                "model": index.model,
                "splits": {split: {key: summary[key] for key in ("n", *GATE_METRICS)} for split, summary in serving.items()},
            },
        )
    report = markdown(result, baseline, problems)
    print(report)
    if args.summary:
        with args.summary.open("a", encoding="utf-8") as summary:
            summary.write(report)
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
