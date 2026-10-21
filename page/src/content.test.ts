import { describe, expect, it } from "vitest";
import { EXAMPLES } from "./examples";
import { TEST_RESULTS } from "./lib/testResults";

// The page's own copy, not the recorded model output.
const SOURCES = import.meta.glob<string>(["./**/*.tsx", "./**/*.ts", "!./**/*.test.*"], {
  eager: true,
  query: "?raw",
  import: "default",
});
const DASHES = [String.fromCodePoint(0x2014), String.fromCodePoint(0x2013)];

describe("page copy", () => {
  it("has no em or en dash", () => {
    const offenders = Object.entries(SOURCES)
      .filter(([, source]) => DASHES.some((dash) => source.includes(dash)))
      .map(([path]) => path);
    expect(offenders).toEqual([]);
  });
});

describe("test-split numbers", () => {
  it("come from evals/results", () => {
    expect(TEST_RESULTS?.file).toMatch(/^analysis-test-\d{4}-\d{2}-\d{2}\.json$/);
    expect(TEST_RESULTS?.n).toBeGreaterThan(0);
    for (const value of [TEST_RESULTS?.accuracy, TEST_RESULTS?.overestimation, TEST_RESULTS?.severeOverestimation]) {
      expect(value).toBeGreaterThanOrEqual(0);
      expect(value).toBeLessThanOrEqual(1);
    }
  });
});

describe("recorded examples", () => {
  it("carry their date and analysis_version", () => {
    for (const example of EXAMPLES) {
      expect(Date.parse(example.recorded_at)).not.toBeNaN();
      expect(example.response.analysis_version).toMatch(/^[0-9a-f]{64}$/);
    }
  });
});
