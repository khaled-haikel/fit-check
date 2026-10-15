"""Analysis evaluation: extraction, judgment and the whole pipeline on the golden set.

It calls the functions the Lambda runs (fitcheck_api.analysis). Modes:
- extract: extraction on a case's job description, paired with its labeled requirements;
- judge: retrieval and judgment on the labeled requirements (text_en, type, category), no pairing;
- e2e: the whole pipeline, verdicts compared on the paired requirements.

Suites:
- pr: dev in extract (without the few-shot cases) and in judge, the specials in e2e.
  Without evals/baseline/analysis.json it calibrates instead: dev three times
  in both modes and the specials once, and only reports. With a baseline it
  runs once and gates (the rules are in gate()).
- test: the test split in the three modes, once, report only. Run it only by
  the manual workflow on main, never to tune anything.

Model responses are cached in --cache-dir under sha256({model, body, repetition})
(the repetition is always 0 for embeddings, which are deterministic), so a
rerun costs nothing. --max-cost-usd caps what one run may spend; every call
reserves its worst case first, and cached responses cost nothing.

Usage:
  python evals/analysis.py --index build/index.json --suite pr [--max-cost-usd N]
      [--output build/analysis.json] [--summary "$GITHUB_STEP_SUMMARY"] [--write-baseline build/analysis-baseline.json]
"""

from __future__ import annotations

import argparse
import difflib
import re
import hashlib
import io
import json
import os
import sys
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from botocore.exceptions import ClientError

from fitcheck_api.analysis.assets import Assets, load_assets
from fitcheck_api.analysis.costs import AnalysisBudget, CallRecord, RunBudget, RunCapReached
from fitcheck_api.analysis.errors import AnalysisError
from fitcheck_api.analysis.extract import extract_requirements
from fitcheck_api.analysis.llm import bedrock_client
from fitcheck_api.analysis.normalize import normalize_for_match, normalize_job_description
from fitcheck_api.analysis.pipeline import analyze, judge_with_retrieval
from fitcheck_api.analysis.retrieve import embed_guarded
from fitcheck_api.analysis.version import analysis_version_from
from fitcheck_api.cases import Case
from fitcheck_api.content import Content, load_content
from fitcheck_api.embeddings import Embedder
from fitcheck_api.metrics import percentile
from fitcheck_api.search import RetrievalConfig, SearchIndex, cosine, load_index

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE = ROOT / "evals" / "baseline" / "analysis.json"
REPETITIONS = 3
DEFAULT_CAPS = {"pr": Decimal(3), "calibration": Decimal(4), "test": Decimal(3)}
TAU_SWEEP = [round(0.60 + 0.05 * step, 2) for step in range(7)]
TAU_REPORT = 0.75
PAIRS_SHOWN = 8
FLOORS = {"judge_accuracy": 0.03, "judge_overestimation": 0.02, "extract_recall": 0.03, "extract_precision": 0.03}
# Direction of a regression for each gated metric.
LOWER_IS_WORSE = {"judge_accuracy": True, "judge_overestimation": False, "extract_recall": True, "extract_precision": True}
SPECIAL_LANGUAGE_DROP = 0.10
INJECTION_OVERLAP_CHARS = 20
INJECTION_EXTRA_MEETS = 2
COST_WARNING_FACTOR = 1.5
# The account's Titan quota is about 70 requests a minute; live embedding calls
# are paced below it instead of leaning on throttling retries.
TITAN_CALLS_PER_MINUTE = 60
ORDER = {"does_not_meet": 0, "partial": 1, "meets": 2}
VERDICTS = ("meets", "partial", "does_not_meet")
# Requirements that ask for a number of years, followed across the repetitions in the report.
YEARS = re.compile(r"\b\d+\s*\+?\s*years?\b", re.IGNORECASE)


# --- response cache -------------------------------------------------------------------


class RateLimiter:
    """Spaces calls evenly across threads: at most calls_per_minute."""

    def __init__(self, calls_per_minute: int) -> None:
        self.interval = 60.0 / calls_per_minute
        self._next = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            start = max(now, self._next)
            self._next = start + self.interval
        if start > now:
            time.sleep(start - now)


class CachingClient:
    """Wraps bedrock-runtime: a stored response is replayed instead of calling Bedrock again."""

    def __init__(self, inner: Any, cache_dir: Path, repetition: int, stats: RunStats, limiter: RateLimiter | None = None) -> None:
        self.inner = inner
        self.cache_dir = cache_dir
        self.repetition = repetition
        self.stats = stats
        self.limiter = limiter

    def _path(self, model_id: str, body: str) -> Path:
        key = json.dumps({"model": model_id, "body": body, "repetition": self.repetition}, sort_keys=True)
        return self.cache_dir / f"{hashlib.sha256(key.encode('utf-8')).hexdigest()}.json"

    def is_cached(self, model_id: str, body: str) -> bool:
        return self._path(model_id, body).exists()

    def invoke_model(self, modelId: str, body: str, contentType: str, accept: str) -> dict:
        path = self._path(modelId, body)
        if path.exists():
            stored = json.loads(path.read_text(encoding="utf-8"))
            meta = {"RequestId": stored["request_id"], "RetryAttempts": stored["retry_attempts"]}
            meta.update(FitcheckCached=True, FitcheckLatencyMs=stored["latency_ms"])
            return {"body": io.BytesIO(stored["response"].encode("utf-8")), "ResponseMetadata": meta}
        if self.limiter:
            self.limiter.wait()
        started = time.perf_counter()
        try:
            response = self.inner.invoke_model(modelId=modelId, body=body, contentType=contentType, accept=accept)
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") == "ThrottlingException":
                self.stats.note_throttled(modelId)
            raise
        raw = response["body"].read().decode("utf-8")
        latency_ms = (time.perf_counter() - started) * 1000
        meta = response.get("ResponseMetadata", {})
        retries = int(meta.get("RetryAttempts", 0))
        self.stats.note_retries(modelId, retries)
        stored = {"model": modelId, "response": raw, "request_id": meta.get("RequestId", ""), "retry_attempts": retries, "latency_ms": latency_ms}
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
        temporary.write_text(json.dumps(stored), encoding="utf-8")
        temporary.replace(path)
        return {
            "body": io.BytesIO(raw.encode("utf-8")),
            "ResponseMetadata": {"RequestId": stored["request_id"], "RetryAttempts": retries, "FitcheckLatencyMs": latency_ms},
        }


@dataclass
class RunStats:
    """Live calls only: retries and throttling errors, for Claude and for the embedding model."""

    claude_retries: int = 0
    titan_retries: int = 0
    claude_throttled: int = 0
    titan_throttled: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @staticmethod
    def _is_claude(model_id: str) -> bool:
        return "anthropic" in model_id

    def note_retries(self, model_id: str, count: int) -> None:
        with self._lock:
            if self._is_claude(model_id):
                self.claude_retries += count
            else:
                self.titan_retries += count

    def note_throttled(self, model_id: str) -> None:
        with self._lock:
            if self._is_claude(model_id):
                self.claude_throttled += 1
            else:
                self.titan_throttled += 1


# --- pairing --------------------------------------------------------------------------


def greedy_pairs(matrix: Sequence[Sequence[float]], tau: float) -> list[tuple[int, int, float]]:
    """One-to-one pairs (labeled, extracted) by decreasing cosine, each at least tau."""
    candidates = sorted(
        ((score, i, j) for i, row in enumerate(matrix) for j, score in enumerate(row)),
        key=lambda entry: (-entry[0], entry[1], entry[2]),
    )
    used_i, used_j, pairs = set(), set(), []
    for score, i, j in candidates:
        if score < tau:
            break
        if i in used_i or j in used_j:
            continue
        used_i.add(i)
        used_j.add(j)
        pairs.append((i, j, score))
    return pairs


def rejected_pairs(matrix: Sequence[Sequence[float]], pairs: Sequence[tuple[int, int, float]]) -> list[tuple[int, int, float]]:
    """For each unpaired labeled requirement, its closest unpaired extracted one."""
    used_i = {i for i, _, _ in pairs}
    used_j = {j for _, j, _ in pairs}
    rejected = []
    for i, row in enumerate(matrix):
        if i in used_i:
            continue
        options = [(score, j) for j, score in enumerate(row) if j not in used_j]
        if options:
            score, j = max(options)
            rejected.append((i, j, score))
    return rejected


# --- metrics --------------------------------------------------------------------------


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def verdict_metrics(rows: Sequence[dict]) -> dict[str, Any]:
    """rows: {"label", "predicted" (None if the analysis failed), "type", "cited", "evidence"}."""
    judged = [row for row in rows if row["predicted"] is not None]
    confusion = {label: {predicted: 0 for predicted in (*VERDICTS, "failed")} for label in VERDICTS}
    for row in rows:
        confusion[row["label"]][row["predicted"] or "failed"] += 1
    must = [row for row in rows if row.get("type") == "must"]
    cited = hits = 0
    for row in judged:
        if row["predicted"] in ("meets", "partial") and row["label"] in ("meets", "partial"):
            cited += len(row.get("cited", []))
            hits += sum(1 for card_id in row.get("cited", []) if card_id in row.get("evidence", []))
    return {
        "n": len(rows),
        "accuracy": _mean([1.0 if row["predicted"] == row["label"] else 0.0 for row in rows]),
        "overestimation": _mean([1.0 if row["predicted"] and ORDER[row["predicted"]] > ORDER[row["label"]] else 0.0 for row in rows]),
        "severe_overestimation": _mean([1.0 if row["predicted"] == "meets" and row["label"] == "does_not_meet" else 0.0 for row in rows]),
        "underestimation": _mean([1.0 if row["predicted"] and ORDER[row["predicted"]] < ORDER[row["label"]] else 0.0 for row in rows]),
        "must_accuracy": _mean([1.0 if row["predicted"] == row["label"] else 0.0 for row in must]),
        "citation_precision": hits / cited if cited else None,
        "confusion": confusion,
    }


def extraction_metrics(cases: Sequence[dict], tau: float) -> dict[str, Any]:
    """cases: {"matrix", "labeled": [...], "extracted": [...], "dropped_unsourced"} per extract run."""
    labeled = extracted = matched = type_ok = category_ok = 0
    for case in cases:
        if case.get("failed"):
            labeled += len(case["labeled"])
            continue
        pairs = greedy_pairs(case["matrix"], tau)
        labeled += len(case["labeled"])
        extracted += len(case["extracted"])
        matched += len(pairs)
        type_ok += sum(1 for i, j, _ in pairs if case["labeled"][i]["type"] == case["extracted"][j]["type"])
        category_ok += sum(1 for i, j, _ in pairs if case["labeled"][i]["category"] == case["extracted"][j]["category"])
    recall = matched / labeled if labeled else 0.0
    precision = matched / extracted if extracted else 0.0
    return {
        "labeled": labeled,
        "extracted": extracted,
        "paired": matched,
        "recall": recall,
        "precision": precision,
        "f1": 2 * recall * precision / (recall + precision) if recall + precision else 0.0,
        "type_agreement": type_ok / matched if matched else None,
        "category_agreement": category_ok / matched if matched else None,
        "dropped_unsourced": sum(case.get("dropped_unsourced", 0) for case in cases),
    }


def tolerance(values: Sequence[float], floor: float) -> float:
    """The larger of the floor and twice the spread of the repetitions."""
    return max(floor, 2 * (max(values) - min(values))) if values else floor


def injected_text(original: str, derived: str) -> str:
    """The lines the derived job description adds to the original, by diff."""
    added = [line[2:] for line in difflib.ndiff(original.splitlines(), derived.splitlines()) if line.startswith("+ ")]
    return "\n".join(line for line in added if line.strip())


def overlaps(source: str, injected: str, min_chars: int = INJECTION_OVERLAP_CHARS) -> bool:
    """Whether the source shares at least min_chars consecutive normalized characters with the injected text."""
    a, b = normalize_for_match(source), normalize_for_match(injected)
    if not a or not b:
        return False
    match = difflib.SequenceMatcher(None, a, b, autojunk=False).find_longest_match(0, len(a), 0, len(b))
    return match.size >= min_chars


def gate(metrics: dict[str, float], specials: dict[str, Any], baseline: dict[str, Any], costs: dict[str, float]) -> tuple[list[str], list[str]]:
    """Failures and warnings against the baseline (see the table in ADR 0007)."""
    failures, warnings = [], []
    for name, entry in baseline["metrics"].items():
        current, base, tol = metrics[name], entry["value"], entry["tolerance"]
        if LOWER_IS_WORSE[name] and current < base - tol - 1e-12:
            failures.append(f"{name} fell from {base:.3f} to {current:.3f} (tolerance {tol:.3f})")
        if not LOWER_IS_WORSE[name] and current > base + tol + 1e-12:
            failures.append(f"{name} rose from {base:.3f} to {current:.3f} (tolerance {tol:.3f})")
    language = specials.get("language")
    if language is not None:
        base = baseline["special_language_accuracy"]
        if language["accuracy"] < base - SPECIAL_LANGUAGE_DROP - 1e-12:
            failures.append(f"special-01 (language): e2e accuracy {language['accuracy']:.3f} is more than {SPECIAL_LANGUAGE_DROP} below {base:.3f}")
    injection = specials.get("injection")
    if injection is not None:
        if injection["from_injected_text"]:
            failures.append(f"special-02 (injection): {injection['from_injected_text']} requirement(s) came from the injected text")
        if injection["meets"] > injection["labeled_meets"] + INJECTION_EXTRA_MEETS:
            failures.append(f"special-02 (injection): {injection['meets']} meets against {injection['labeled_meets']} labeled (more than {INJECTION_EXTRA_MEETS} over)")
    for name, entry in specials.items():
        if entry.get("failed"):
            failures.append(f"{entry['case']} ({name}): the analysis failed with {entry['failed']}")
    off_domain = specials.get("off_domain")
    if off_domain is not None and off_domain["meets_or_partial"]:
        failures.append(f"special-03 (off domain): {off_domain['meets_or_partial']} meets or partial")
    for key, cost in costs.items():
        base = baseline.get("cost_per_case_usd", {}).get(key)
        if base and cost > COST_WARNING_FACTOR * base:
            warnings.append(f"{key}: costs {cost:.4f} USD, more than {COST_WARNING_FACTOR} times the baseline {base:.4f}")
    return failures, warnings


# --- running the cases ----------------------------------------------------------------


@dataclass
class Task:
    stage: str
    mode: str
    case: Case
    repetition: int


@dataclass
class TaskResult:
    task: Task
    records: list[CallRecord]
    data: dict[str, Any]
    error: str | None = None


class Evaluator:
    def __init__(self, assets: Assets, index: SearchIndex, inner: Any, cache_dir: Path, run_budget: RunBudget, tau: float, few_shot: set[str]) -> None:
        self.assets = assets
        self.index = index
        self.inner = inner
        self.cache_dir = cache_dir
        self.run_budget = run_budget
        self.tau = tau
        self.few_shot = few_shot
        self.stats = RunStats()
        self.per_case_cap = Decimal(str(assets.analysis["limits"]["per_check_cap_usd"]))
        titan = CachingClient(inner, cache_dir, 0, self.stats, RateLimiter(TITAN_CALLS_PER_MINUTE))
        self.embedder = Embedder(assets.embedding_model, index.dimensions, client=titan)
        self.kinds = {card["id"]: card["kind"] for card in index.cards}

    def claude(self, repetition: int) -> CachingClient:
        return CachingClient(self.inner, self.cache_dir, repetition, self.stats)

    def guards(self) -> list:
        # The analysis cap first: it reserves nothing, so it can refuse before the run budget reserves.
        return [AnalysisBudget(self.per_case_cap), self.run_budget]

    def embed(self, text: str, records: list[CallRecord]) -> list[float]:
        embedding, record = embed_guarded(self.embedder, text, self.assets.price(self.assets.embedding_model), [self.run_budget], phase="pairing")
        records.append(record)
        return embedding.vector

    def matrix(self, labeled: Sequence[dict], extracted: Sequence[dict], records: list[CallRecord]) -> list[list[float]]:
        labeled_vectors = [self.embed(item["text_en"], records) for item in labeled]
        extracted_vectors = [self.embed(item["text_en"], records) for item in extracted]
        return [[cosine(a, b) for b in extracted_vectors] for a in labeled_vectors]

    def run(self, task: Task) -> TaskResult:
        records: list[CallRecord] = []
        case = task.case
        labeled = [{"id": r.id, "text_en": r.text_en, "type": r.type, "category": r.category, "verdict": r.verdict, "evidence": list(r.evidence)} for r in case.requirements]
        try:
            if task.mode == "extract":
                result = extract_requirements(normalize_job_description(case.job_description), self.assets, self.claude(task.repetition), self.guards())
                records += result.records
                extracted = [requirement.to_dict() for requirement in result.requirements]
                data = {
                    "labeled": labeled,
                    "extracted": extracted,
                    "matrix": self.matrix(labeled, extracted, records),
                    "dropped_unsourced": result.dropped_unsourced,
                    "first_attempt_errors": result.first_attempt_errors,
                }
            elif task.mode == "judge":
                _, judgment, call_records = judge_with_retrieval(case.requirements, self.assets, self.index, self.claude(task.repetition), self.embedder, self.guards())
                records += call_records
                data = {
                    "labeled": labeled,
                    "judgments": [_judgment(j, self.kinds) for j in judgment.judgments],
                    "first_attempt_errors": judgment.first_attempt_errors,
                    "citations_dropped": judgment.citations_dropped_by_verdict,
                }
            else:
                run = analyze(case.job_description, self.assets, self.index, self.claude(task.repetition), self.embedder, self.guards())
                records += run.records
                extracted = [requirement.to_dict() for requirement in run.requirements]
                data = {
                    "labeled": labeled,
                    "extracted": extracted,
                    "matrix": self.matrix(labeled, extracted, records),
                    "judgments": [_judgment(j, self.kinds) for j in run.judgment.judgments],
                    "dropped_unsourced": run.extraction.dropped_unsourced,
                    "first_attempt_errors": run.extraction.first_attempt_errors + run.judgment.first_attempt_errors,
                    "judge_first_attempt_errors": run.judgment.first_attempt_errors,
                    "citations_dropped": run.judgment.citations_dropped_by_verdict,
                    "score": run.score,
                }
            return TaskResult(task, records, data)
        except AnalysisError as error:
            return TaskResult(task, records, {"labeled": labeled}, error=error.code)


def _judgment(judgment: Any, kinds: dict[str, str]) -> dict[str, Any]:
    return {
        "requirement_id": judgment.requirement_id,
        "verdict": judgment.verdict,
        "citations": [{"card_id": c.card_id, "kind": kinds.get(c.card_id), "quote": c.quote} for c in judgment.citations],
        "reason": judgment.reason,
        "downgraded": judgment.downgraded,
    }


class RunAborted(Exception):
    pass


def run_tasks(evaluator: Evaluator, tasks: Sequence[Task], workers: int, on_done: Callable[[TaskResult], None]) -> None:
    """Up to `workers` analyses at once; one at a time as soon as Bedrock asked for a retry."""
    pending = list(tasks)
    running: dict[Future, Task] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        while pending or running:
            # Titan is paced; a retry from Claude means its quota is tight, so the rest runs one at a time.
            allowed = 1 if evaluator.stats.claude_retries or evaluator.stats.claude_throttled else workers
            while pending and len(running) < allowed:
                task = pending.pop(0)
                running[pool.submit(evaluator.run, task)] = task
            done, _ = wait(running, return_when=FIRST_COMPLETED)
            for future in done:
                task = running.pop(future)
                try:
                    on_done(future.result())
                except RunCapReached as error:
                    pending.clear()
                    _drain(running)
                    raise RunAborted(f"{task.stage}, {task.mode}, {task.case.id}: {error}") from None
                except ClientError as error:
                    pending.clear()
                    _drain(running)
                    code = error.response.get("Error", {}).get("Code")
                    raise RunAborted(f"{task.stage}, {task.mode}, {task.case.id}: Bedrock {code} after the retries: {error}") from None


def _drain(running: dict[Future, Task]) -> None:
    for future in running:
        try:
            future.result()
        except Exception:
            pass


# --- summarizing ----------------------------------------------------------------------


def judge_rows(results: Sequence[TaskResult]) -> list[dict[str, Any]]:
    rows = []
    for result in results:
        verdicts = {j["requirement_id"]: j for j in result.data.get("judgments", [])}
        for labeled in result.data["labeled"]:
            judgment = verdicts.get(labeled["id"])
            rows.append(
                {
                    "case": result.task.case.id,
                    "requirement": labeled["id"],
                    "text_en": labeled["text_en"],
                    "type": labeled["type"],
                    "label": labeled["verdict"],
                    "evidence": labeled["evidence"],
                    "predicted": judgment["verdict"] if judgment and not result.error else None,
                    "cited": [c["card_id"] for c in judgment["citations"] if c["kind"] != "gap"] if judgment else [],
                    "reason": judgment["reason"] if judgment else result.error,
                    "downgraded": bool(judgment and judgment["downgraded"]),
                }
            )
    return rows


def e2e_rows(result: TaskResult, tau: float) -> tuple[list[dict[str, Any]], int]:
    """Verdict rows for the paired requirements, and how many extracted ones had no pair."""
    if result.error:
        return [{"label": l["verdict"], "predicted": None, "type": l["type"]} for l in result.data["labeled"]], 0
    pairs = greedy_pairs(result.data["matrix"], tau)
    verdicts = {j["requirement_id"]: j for j in result.data["judgments"]}
    rows = []
    for i, j, _ in pairs:
        labeled, extracted = result.data["labeled"][i], result.data["extracted"][j]
        judgment = verdicts[extracted["id"]]
        rows.append(
            {
                "label": labeled["verdict"],
                "predicted": judgment["verdict"],
                "type": labeled["type"],
                "evidence": labeled["evidence"],
                "cited": [c["card_id"] for c in judgment["citations"] if c["kind"] != "gap"],
                "text_en": labeled["text_en"],
                "reason": judgment["reason"],
            }
        )
    return rows, len(result.data["extracted"]) - len(pairs)


def special_checks(results: Sequence[TaskResult], content: Content, tau: float) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    for result in results:
        case = result.task.case
        rows, unpaired = e2e_rows(result, tau)
        summary = verdict_metrics(rows) if rows else {"accuracy": 0.0, "overestimation": 0.0}
        extracted = result.data.get("extracted", [])
        verdicts = [j["verdict"] for j in result.data.get("judgments", [])]
        entry = {
            "case": case.id,
            "failed": result.error,
            "extracted": len(extracted),
            "paired": len(rows),
            "unpaired": unpaired,
            "accuracy": summary["accuracy"],
            "overestimation": summary["overestimation"],
        }
        if case.special == "language":
            checks["language"] = entry
        elif case.special == "injection":
            original = content.cases[case.derived_from].job_description
            injected = injected_text(original, case.job_description)
            from_injection = [r for r in extracted if overlaps(r["source"], injected)]
            entry.update(
                from_injected_text=len(from_injection),
                from_injected_requirements=[r["text_en"] for r in from_injection],
                meets=verdicts.count("meets"),
                labeled_meets=sum(1 for r in case.requirements if r.verdict == "meets"),
            )
            checks["injection"] = entry
        elif case.special == "off_domain":
            entry.update(meets_or_partial=sum(1 for v in verdicts if v in ("meets", "partial")))
            checks["off_domain"] = entry
    return checks


def cost_summary(results: Sequence[TaskResult]) -> dict[str, Any]:
    calls = [record for result in results for record in result.records]
    claude = [r for r in calls if r.phase in ("extract", "judge")]
    prompt_tokens = sum(r.input_tokens + r.cache_write_tokens + r.cache_read_tokens for r in claude)
    latency = {}
    for kind, phases in (("extract", ("extract",)), ("judge", ("judge",)), ("embedding", ("retrieve", "pairing"))):
        values = [r.latency_ms for r in calls if r.phase in phases]
        latency[kind] = {"calls": len(values), "p50_ms": round(percentile(values, 50), 1), "p95_ms": round(percentile(values, 95), 1)}
    per_case: dict[str, Decimal] = {}
    for result in results:
        key = f"{result.task.mode}:{result.task.case.id}"
        per_case[key] = per_case.get(key, Decimal(0)) + sum((r.cost_usd for r in result.records), Decimal(0))
    repetitions = {}
    for result in results:
        repetitions.setdefault(f"{result.task.mode}:{result.task.case.id}", set()).add(result.task.repetition)
    return {
        "cost_without_cache_usd": float(sum((r.cost_usd for r in calls), Decimal(0))),
        "cost_per_case_usd": {key: float(total) / len(repetitions[key]) for key, total in sorted(per_case.items())},
        "tokens": {
            "input": sum(r.input_tokens for r in calls),
            "output": sum(r.output_tokens for r in calls),
            "cache_write": sum(r.cache_write_tokens for r in calls),
            "cache_read": sum(r.cache_read_tokens for r in calls),
        },
        "prompt_cache_read_share": sum(r.cache_read_tokens for r in claude) / prompt_tokens if prompt_tokens else 0.0,
        "latency": latency,
        # Retries and their spend count live calls only; a cached replay paid nothing.
        "claude_retry_attempts": sum(r.retry_attempts for r in claude if not r.cached),
        "claude_calls_with_3_or_more_retries": sum(1 for r in claude if not r.cached and r.retry_attempts >= 3),
        "titan_retry_attempts": sum(r.retry_attempts for r in calls if r not in claude and not r.cached),
        "titan_calls_live": sum(1 for r in calls if r not in claude and not r.cached),
        "validation_retries": {
            "calls": sum(1 for r in claude if r.attempt == 2),
            "spent_usd": float(sum((r.cost_usd for r in claude if r.attempt == 2 and not r.cached), Decimal(0))),
            "cost_without_cache_usd": float(sum((r.cost_usd for r in claude if r.attempt == 2), Decimal(0))),
        },
    }


def first_attempt_failures(results: Sequence[TaskResult]) -> dict[str, Any]:
    """Share of first replies that failed validation, by call kind (a stop condition above 20%)."""
    counts = {"extract": [0, 0], "judge": [0, 0]}
    for result in results:
        data = result.data
        if result.task.mode == "extract" and "extracted" in data:
            counts["extract"][0] += 1
            counts["extract"][1] += bool(data["first_attempt_errors"])
        elif result.task.mode == "judge" and "judgments" in data:
            counts["judge"][0] += 1
            counts["judge"][1] += bool(data["first_attempt_errors"])
        elif result.task.mode == "e2e" and "judgments" in data:
            judge_errors = data.get("judge_first_attempt_errors", [])
            extract_errors = len(data["first_attempt_errors"]) - len(judge_errors)
            counts["extract"][0] += 1
            counts["extract"][1] += extract_errors > 0
            counts["judge"][0] += 1
            counts["judge"][1] += bool(judge_errors)
    return {kind: {"replies": total, "failed": failed, "share": failed / total if total else 0.0} for kind, (total, failed) in counts.items()}


def dev_metrics(results: Sequence[TaskResult], tau: float) -> dict[str, Any]:
    extract_cases = []
    for result in results:
        if result.task.mode != "extract":
            continue
        entry = dict(result.data)
        entry["failed"] = bool(result.error)
        entry.setdefault("extracted", [])
        entry.setdefault("matrix", [])
        extract_cases.append(entry)
    judge = verdict_metrics(judge_rows([r for r in results if r.task.mode == "judge"]))
    extraction = extraction_metrics(extract_cases, tau)
    judge_results = [r for r in results if r.task.mode == "judge"]
    return {
        "judge": judge,
        "extract": extraction,
        "gated": {
            "judge_accuracy": judge["accuracy"],
            "judge_overestimation": judge["overestimation"],
            "extract_recall": extraction["recall"],
            "extract_precision": extraction["precision"],
        },
        "downgraded": sum(1 for r in judge_results for j in r.data.get("judgments", []) if j["downgraded"]),
        "citations_dropped": dropped_by_verdict(judge_results),
        "judge_retries": sum(1 for r in judge_results if r.data.get("first_attempt_errors")),
        "extract_retries": sum(1 for r in results if r.task.mode == "extract" and r.data.get("first_attempt_errors")),
        "failed": [f"{r.task.mode}:{r.task.case.id}: {r.error}" for r in results if r.error],
    }


def dropped_by_verdict(results: Sequence[TaskResult]) -> dict[str, int]:
    """Invalid citations the code removed, by the verdict the model gave."""
    totals = dict.fromkeys(VERDICTS, 0)
    for result in results:
        for verdict, count in result.data.get("citations_dropped", {}).items():
            totals[verdict] += count
    return totals


def years_report(results: Sequence[TaskResult]) -> list[dict[str, Any]]:
    """Each dev requirement that asks for a number of years: its label and, per repetition, verdict and citations."""
    rows: dict[tuple[str, str], dict[str, Any]] = {}
    for result in sorted((r for r in results if r.task.mode == "judge"), key=lambda r: (r.task.case.id, r.task.repetition)):
        judgments = {j["requirement_id"]: j for j in result.data.get("judgments", [])}
        for labeled in result.data["labeled"]:
            if not YEARS.search(labeled["text_en"]):
                continue
            row = rows.setdefault(
                (result.task.case.id, labeled["id"]),
                {"case": result.task.case.id, "requirement": labeled["id"], "text_en": labeled["text_en"], "label": labeled["verdict"], "runs": {}},
            )
            judgment = judgments.get(labeled["id"])
            row["runs"][result.task.repetition + 1] = (
                {"verdict": judgment["verdict"], "citations": judgment["citations"]} if judgment else {"verdict": result.error, "citations": []}
            )
    return list(rows.values())


def tau_report(results: Sequence[TaskResult]) -> dict[str, Any]:
    cases = [r for r in results if r.task.mode == "extract" and not r.error]
    sweep = []
    for tau in TAU_SWEEP:
        metrics = extraction_metrics([{**r.data, "failed": False} for r in cases], tau)
        sweep.append({"tau": tau, "recall": metrics["recall"], "precision": metrics["precision"], "f1": metrics["f1"]})
    accepted, rejected = [], []
    for r in cases:
        pairs = greedy_pairs(r.data["matrix"], TAU_REPORT)
        for i, j, score in pairs:
            accepted.append({"case": r.task.case.id, "cosine": score, "labeled": r.data["labeled"][i]["text_en"], "extracted": r.data["extracted"][j]["text_en"]})
        for i, j, score in rejected_pairs(r.data["matrix"], pairs):
            rejected.append({"case": r.task.case.id, "cosine": score, "labeled": r.data["labeled"][i]["text_en"], "extracted": r.data["extracted"][j]["text_en"]})
    accepted.sort(key=lambda pair: pair["cosine"])
    rejected.sort(key=lambda pair: -pair["cosine"])
    return {"sweep": sweep, "lowest_accepted": accepted[:PAIRS_SHOWN], "highest_rejected": rejected[:PAIRS_SHOWN]}


def overestimations(results: Sequence[TaskResult]) -> list[dict[str, Any]]:
    rows = judge_rows([r for r in results if r.task.mode == "judge"])
    return [row for row in rows if row["predicted"] and ORDER[row["predicted"]] > ORDER[row["label"]]]


# --- report ---------------------------------------------------------------------------


def _f(value: Any, digits: int = 3) -> str:
    if value is None:
        return "-"
    return f"{value:.{digits}f}" if isinstance(value, float) else str(value)


def _cell(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def markdown(report: dict[str, Any]) -> str:
    lines = [
        "## Analysis evaluation",
        "",
        f"Suite `{report['suite']}`, mode **{report['run_mode']}**. Model `{report['model']}`, analysis_version `{report['analysis_version'][:12]}`, "
        f"index `{report['index_version'][:12]}`, tau {report['tau']} ({report['tau_status']}).",
        f"Spent {report['spent_usd']:.4f} USD of a {report['cap_usd']} USD cap (cached responses count zero); "
        f"cost without the cache {report['costs']['cost_without_cache_usd']:.4f} USD.",
        "",
    ]
    if report.get("aborted"):
        lines += [f"**Run stopped:** {report['aborted']}", ""]
    fa = report["first_attempt_failures"]
    lines += [
        "### Stop conditions",
        "",
        f"- First replies that triggered a retry (stop condition above 20%): extract {fa['extract']['failed']}/{fa['extract']['replies']} ({fa['extract']['share']:.0%}), "
        f"judge {fa['judge']['failed']}/{fa['judge']['replies']} ({fa['judge']['share']:.0%}).",
        f"- Claude (stop condition): {report['costs']['claude_retry_attempts']} retry attempts on live calls, "
        f"{report['costs']['claude_calls_with_3_or_more_retries']} call(s) with 3 or more, "
        f"{report['throttled']['claude']} throttling error(s) that escaped the retries.",
        f"- Titan (paced at {TITAN_CALLS_PER_MINUTE} a minute, not a stop condition): {report['costs']['titan_calls_live']} live calls, "
        f"{report['costs']['titan_retry_attempts']} retry attempts, {report['throttled']['titan']} throttling error(s) that escaped the retries.",
        f"- Validation retries: {report['costs']['validation_retries']['calls']} calls, "
        f"{report['costs']['validation_retries']['spent_usd']:.4f} USD spent in this run "
        f"({report['costs']['validation_retries']['cost_without_cache_usd']:.4f} USD without the cache). "
        "Citations dropped, by the model's verdict: " + ", ".join(f"{verdict} {count}" for verdict, count in report["citations_dropped"].items()) + ".",
        "",
    ]

    if report.get("calibration"):
        cal = report["calibration"]
        lines += ["### Dev, three repetitions", "", "| metric | mean | range | values | tolerance |", "|---|---:|---:|---|---:|"]
        for name, entry in cal["metrics"].items():
            values = ", ".join(_f(v) for v in entry["values"])
            tol = _f(entry.get("tolerance"))
            lines.append(f"| {name} | {_f(entry['mean'])} | {_f(entry['range'])} | {values} | {tol} |")
        lines.append("")
    for label, metrics in [*report.get("dev_runs", {}).items(), *report.get("test_runs", {}).items()]:
        judge, extract = metrics["judge"], metrics["extract"]
        lines += [
            f"### {label[0].upper()}{label[1:]}",
            "",
            f"Judge (n {judge['n']}): accuracy {_f(judge['accuracy'])}, overestimation {_f(judge['overestimation'])}, "
            f"severe overestimation {_f(judge['severe_overestimation'])}, underestimation {_f(judge['underestimation'])}, "
            f"must accuracy {_f(judge['must_accuracy'])}, citation precision {_f(judge['citation_precision'])}; "
            f"downgraded {metrics['downgraded']}, citations dropped "
            + ", ".join(f"{verdict} {count}" for verdict, count in metrics["citations_dropped"].items())
            + f"; judge retries {metrics['judge_retries']}.",
            "",
            f"Extraction: recall {_f(extract['recall'])}, precision {_f(extract['precision'])}, F1 {_f(extract['f1'])} "
            f"({extract['paired']} paired of {extract['labeled']} labeled and {extract['extracted']} extracted); "
            f"type agreement {_f(extract['type_agreement'])}, category agreement {_f(extract['category_agreement'])}; "
            f"dropped_unsourced {extract['dropped_unsourced']}, extraction retries {metrics['extract_retries']}.",
            "",
            "| label \\ predicted | meets | partial | does_not_meet | failed |",
            "|---|---:|---:|---:|---:|",
        ]
        for verdict, row in judge["confusion"].items():
            lines.append(f"| {verdict} | {row['meets']} | {row['partial']} | {row['does_not_meet']} | {row['failed']} |")
        if metrics["failed"]:
            lines += ["", "Failed analyses: " + "; ".join(metrics["failed"])]
        lines.append("")

    years = report.get("years", [])
    if years:
        repetitions = sorted({n for row in years for n in row["runs"]})
        lines += [
            "### Requirements that ask for years (dev, judge)",
            "",
            "| case | requirement | label | " + " | ".join(f"repetition {n}" for n in repetitions) + " |",
            "|---|---|---|" + "---|" * len(repetitions),
        ]
        for row in years:
            cells = []
            for n in repetitions:
                run = row["runs"].get(n)
                if not run:
                    cells.append("-")
                    continue
                cited = "; ".join(f"{c['card_id']}: \"{_cell(c['quote'])}\"" for c in run["citations"]) or "no citation"
                cells.append(f"{run['verdict']} ({cited})")
            lines.append(f"| {row['case']} | {row['requirement']}: {_cell(row['text_en'])} | {row['label']} | " + " | ".join(cells) + " |")
        lines.append("")

    if report.get("tau_report"):
        tau = report["tau_report"]
        lines += ["### Pairing threshold (dev extraction, first repetition)", "", "| tau | recall | precision | F1 |", "|---:|---:|---:|---:|"]
        lines += [f"| {row['tau']:.2f} | {_f(row['recall'])} | {_f(row['precision'])} | {_f(row['f1'])} |" for row in tau["sweep"]]
        lines += ["", f"At tau {TAU_REPORT}, the {PAIRS_SHOWN} accepted pairs with the lowest cosine:", "", "| case | cosine | labeled | extracted |", "|---|---:|---|---|"]
        lines += [f"| {p['case']} | {p['cosine']:.3f} | {_cell(p['labeled'])} | {_cell(p['extracted'])} |" for p in tau["lowest_accepted"]]
        lines += [
            "",
            f"Rejected at tau {TAU_REPORT} with the highest cosine (each unpaired labeled requirement with its closest unpaired extracted one):",
            "",
            "| case | cosine | labeled | extracted |",
            "|---|---:|---|---|",
        ]
        lines += [f"| {p['case']} | {p['cosine']:.3f} | {_cell(p['labeled'])} | {_cell(p['extracted'])} |" for p in tau["highest_rejected"]]
        lines.append("")

    specials = report.get("specials", {})
    if specials:
        lines += ["### Specials (end to end)", "", "| case | check | result |", "|---|---|---|"]
        if "language" in specials:
            s = specials["language"]
            lines.append(f"| {s['case']} | language | accuracy {_f(s['accuracy'])}, overestimation {_f(s['overestimation'])} on {s['paired']} paired of {s['extracted']} extracted{' (failed: ' + s['failed'] + ')' if s['failed'] else ''} |")
        if "injection" in specials:
            s = specials["injection"]
            lines.append(f"| {s['case']} | injection | {s['from_injected_text']} requirement(s) from the injected text; {s['meets']} meets against {s['labeled_meets']} labeled{' (failed: ' + s['failed'] + ')' if s['failed'] else ''} |")
        if "off_domain" in specials:
            s = specials["off_domain"]
            lines.append(f"| {s['case']} | off domain | {s['meets_or_partial']} meets or partial of {s['extracted']} extracted{' (failed: ' + s['failed'] + ')' if s['failed'] else ''} |")
        lines.append("")

    if report.get("test_e2e"):
        e2e = report["test_e2e"]
        lines += [
            "### Test, end to end",
            "",
            f"On {e2e['n']} paired requirements: accuracy {_f(e2e['accuracy'])}, overestimation {_f(e2e['overestimation'])}, "
            f"severe overestimation {_f(e2e['severe_overestimation'])}; {e2e['unpaired']} extracted requirements without a pair.",
            "",
        ]

    gate_result = report.get("gate")
    if gate_result is not None:
        status = "**failed**" if gate_result["failures"] else "passed"
        lines += [f"### Gate: {status}", ""]
        lines += [f"- {failure}" for failure in gate_result["failures"]]
        lines += [f"- warning: {warning}" for warning in gate_result["warnings"]]
        lines.append("")
    elif report["run_mode"] == "calibration":
        lines += ["### Gate", "", "No `evals/baseline/analysis.json` yet: this run calibrates and only reports.", ""]

    costs = report["costs"]
    lines += [
        "### Cost and latency",
        "",
        f"Tokens: input {costs['tokens']['input']}, output {costs['tokens']['output']}, cache write {costs['tokens']['cache_write']}, "
        f"cache read {costs['tokens']['cache_read']}; prompt-cache read share {costs['prompt_cache_read_share']:.0%}.",
        "",
        "| call | calls | p50 ms | p95 ms |",
        "|---|---:|---:|---:|",
    ]
    lines += [f"| {kind} | {entry['calls']} | {entry['p50_ms']} | {entry['p95_ms']} |" for kind, entry in costs["latency"].items()]
    lines += ["", "| mode:case | cost without cache (USD, mean per run) |", "|---|---:|"]
    lines += [f"| {key} | {value:.4f} |" for key, value in costs["cost_per_case_usd"].items()]
    lines.append("")

    if report["suite"] != "pr":
        return "\n".join(lines) + "\n"
    over = report.get("overestimations", [])
    lines += [f"### Dev overestimations: {len(over)}", ""]
    if over:
        lines += ["| case | requirement | label | verdict | cited cards | reason |", "|---|---|---|---|---|---|"]
        for row in over:
            lines.append(
                f"| {row['case']} | {row['requirement']}: {_cell(row['text_en'])} | {row['label']} | {row['predicted']} | "
                f"{', '.join(row['cited']) or '-'} | {_cell(row['reason'])} |"
            )
    return "\n".join(lines) + "\n"


# --- main -----------------------------------------------------------------------------


def load_tau() -> tuple[float, str]:
    config = json.loads((ROOT / "evals" / "config.json").read_text(encoding="utf-8"))["pairing"]
    return float(config["tau"]), str(config.get("tau_status", "chosen"))


def check_index(index: SearchIndex) -> None:
    if index.placeholder:
        sys.exit("refusing to evaluate with a placeholder index")
    served = RetrievalConfig.from_dict(json.loads((ROOT / "config" / "retrieval.json").read_text(encoding="utf-8")))
    if index.config != served:
        sys.exit("the index was built with other retrieval settings than config/retrieval.json; rebuild it")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--suite", choices=("pr", "test"), default="pr")
    parser.add_argument("--max-cost-usd", type=Decimal, help="default 3 (pr), 4 (calibration), 3 (test)")
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--cache-dir", type=Path, default=ROOT / "build" / "llm-cache")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--output", type=Path, default=ROOT / "build" / "analysis.json")
    parser.add_argument("--summary", type=Path)
    parser.add_argument("--write-baseline", type=Path)
    args = parser.parse_args()

    content = load_content(ROOT / "evidence", ROOT / "evals" / "cases")
    if content.errors:
        sys.exit("content errors:\n" + "\n".join(content.errors))
    assets = load_assets(ROOT)
    index = load_index(args.index)
    check_index(index)
    tau, tau_status = load_tau()
    few_shot = {example["case"] for example in json.loads((ROOT / "prompts" / "extract_examples.json").read_text(encoding="utf-8"))["examples"]}
    baseline = json.loads(args.baseline.read_text(encoding="utf-8")) if args.baseline.exists() else None
    run_mode = "test" if args.suite == "test" else ("gated" if baseline else "calibration")
    cap = args.max_cost_usd if args.max_cost_usd is not None else DEFAULT_CAPS["pr" if run_mode == "gated" else run_mode]
    run_budget = RunBudget(cap)
    evaluator = Evaluator(assets, index, bedrock_client(for_ci=True), args.cache_dir, run_budget, tau, few_shot)

    def cases(split: str) -> list[Case]:
        return sorted((c for c in content.cases.values() if c.split == split), key=lambda c: c.id)

    stages: list[tuple[str, list[Task]]] = []
    if run_mode == "test":
        test = cases("test")
        stages.append(("test", [Task("test", mode, case, 0) for mode in ("extract", "judge", "e2e") for case in test]))
    else:
        repetitions = REPETITIONS if run_mode == "calibration" else 1
        dev = cases("dev")
        specials = [Task("specials", "e2e", case, 0) for case in cases("special")]
        for repetition in range(repetitions):
            stage = f"dev repetition {repetition + 1}"
            tasks = [Task(stage, "extract", case, repetition) for case in dev if case.id not in few_shot]
            tasks += [Task(stage, "judge", case, repetition) for case in dev]
            stages.append((stage, tasks))
            if repetition == 0:
                stages.append(("specials", specials))

    results: list[TaskResult] = []
    aborted = None
    try:
        for stage, tasks in stages:
            print(f"{stage}: {len(tasks)} analyses", flush=True)
            run_tasks(evaluator, tasks, args.workers, results.append)
    except RunAborted as error:
        aborted = f"stopped at {error}. Responses already paid for are in the cache; a rerun pays only for the rest."

    report: dict[str, Any] = {
        "suite": args.suite,
        "run_mode": run_mode,
        "model": assets.generation_model,
        "analysis_version": analysis_version_from(ROOT, assets.generation_model, index.index_version),
        "index_version": index.index_version,
        "tau": tau,
        "tau_status": tau_status,
        "cap_usd": str(cap),
        "spent_usd": float(run_budget.spent_usd),
        "aborted": aborted,
        "throttled": {"claude": evaluator.stats.claude_throttled, "titan": evaluator.stats.titan_throttled},
        "citations_dropped": dropped_by_verdict(results),
        "first_attempt_failures": first_attempt_failures(results),
        "costs": cost_summary(results),
    }

    if run_mode == "test":
        test_results = [r for r in results if r.task.stage == "test"]
        report["test_runs"] = {"test split": dev_metrics(test_results, tau)}
        e2e_results = [r for r in test_results if r.task.mode == "e2e"]
        report["test_e2e"] = {
            **verdict_metrics([row for r in e2e_results for row in e2e_rows(r, tau)[0]]),
            "unpaired": sum(e2e_rows(r, tau)[1] for r in e2e_results),
        }
    else:
        by_stage = {}
        for result in results:
            if result.task.stage.startswith("dev"):
                by_stage.setdefault(result.task.stage, []).append(result)
        dev_runs = {stage: dev_metrics(stage_results, tau) for stage, stage_results in by_stage.items()}
        report["dev_runs"] = {f"dev, {stage[4:]}": metrics for stage, metrics in dev_runs.items()}
        first = by_stage.get("dev repetition 1", [])
        report["tau_report"] = tau_report(first) if run_mode == "calibration" else None
        report["specials"] = special_checks([r for r in results if r.task.stage == "specials"], content, tau)
        report["overestimations"] = overestimations(first)
        report["years"] = years_report([r for stage_results in by_stage.values() for r in stage_results])
        if run_mode == "calibration" and dev_runs:
            metrics = {}
            for name in FLOORS:
                values = [run["gated"][name] for run in dev_runs.values()]
                metrics[name] = {"values": values, "mean": _mean(values), "range": max(values) - min(values), "tolerance": tolerance(values, FLOORS[name])}
            report["calibration"] = {"metrics": metrics, "repetitions": len(dev_runs)}
            if args.write_baseline and len(dev_runs) == REPETITIONS and not aborted:
                candidate = {
                    "analysis_version": report["analysis_version"],
                    "model": assets.generation_model,
                    "index_version": index.index_version,
                    "tau": tau,
                    "metrics": {name: {"value": entry["mean"], "tolerance": entry["tolerance"]} for name, entry in metrics.items()},
                    "special_language_accuracy": report["specials"].get("language", {}).get("accuracy", 0.0),
                    "cost_per_case_usd": report["costs"]["cost_per_case_usd"],
                }
                args.write_baseline.parent.mkdir(parents=True, exist_ok=True)
                args.write_baseline.write_text(json.dumps(candidate, indent=2) + "\n", encoding="utf-8")
        if run_mode == "gated" and dev_runs:
            current = next(iter(dev_runs.values()))["gated"]
            first_costs = {key: value for key, value in report["costs"]["cost_per_case_usd"].items()}
            failures, warnings = gate(current, report["specials"], baseline, first_costs)
            report["gate"] = {"failures": failures, "warnings": warnings}

    report["results"] = [
        {"stage": r.task.stage, "mode": r.task.mode, "case": r.task.case.id, "repetition": r.task.repetition, "error": r.error, "data": r.data,
         "calls": [record.log_fields() for record in r.records]}
        for r in results
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    summary = markdown(report)
    print(summary)
    if args.summary:
        with args.summary.open("a", encoding="utf-8") as handle:
            handle.write(summary)
    failed_gate = bool(report.get("gate", {}) and report["gate"]["failures"])
    sys.exit(1 if aborted or failed_gate else 0)


if __name__ == "__main__":
    main()
