// The test-split numbers, read at build time from the newest evals/results/analysis-test-<date>.json.
// Only the test_runs entry is imported, so the rest of the report stays out of the bundle.

interface JudgeMetrics {
  n: number;
  accuracy: number;
  overestimation: number;
  severe_overestimation: number;
}

export type TestRuns = Record<string, { judge: JudgeMetrics }>;

export interface TestResults {
  file: string;
  date: string;
  n: number;
  accuracy: number;
  overestimation: number;
  severeOverestimation: number;
}

const reports = import.meta.glob<TestRuns>("../../../evals/results/analysis-test-*.json", { eager: true, import: "test_runs" });

export function latestTestResults(found: Record<string, TestRuns> = reports): TestResults | null {
  const path = Object.keys(found).sort().at(-1);
  if (!path) return null;
  const judge = found[path]["test split"].judge;
  return {
    file: path.slice(path.lastIndexOf("/") + 1),
    date: /analysis-test-(\d{4}-\d{2}-\d{2})\.json$/.exec(path)?.[1] ?? "",
    n: judge.n,
    accuracy: judge.accuracy,
    overestimation: judge.overestimation,
    severeOverestimation: judge.severe_overestimation,
  };
}

export const TEST_RESULTS = latestTestResults();
