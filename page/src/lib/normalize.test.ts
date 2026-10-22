import { describe, expect, it } from "vitest";
import { normalizeJobDescription, withinLimits } from "./normalize";

const CR = String.fromCharCode(13);

describe("normalizeJobDescription", () => {
  it("counts the way the API does", () => {
    const text = `  Senior   Engineer ${CR}\n${CR}\n\n\n\tPython\t and  AWS  \n  `;
    expect(normalizeJobDescription(text)).toBe("Senior Engineer\n\nPython and AWS");
  });

  it("accepts 200 to 20,000 characters", () => {
    expect(withinLimits(199)).toBe(false);
    expect(withinLimits(200)).toBe(true);
    expect(withinLimits(20000)).toBe(true);
    expect(withinLimits(20001)).toBe(false);
  });
});
