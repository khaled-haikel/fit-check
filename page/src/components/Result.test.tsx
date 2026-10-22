import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { EXAMPLES } from "../examples";
import { Result } from "./Result";

describe("a recorded example", () => {
  const example = EXAMPLES.find((e) => e.case === "dev-02-fullstack-ai-native");

  it("is recorded as a finished analysis", () => {
    expect(EXAMPLES).toHaveLength(2);
    expect(example?.response.status).toBe("done");
  });

  it("renders the score, every requirement, its source, verdict and citations", () => {
    if (!example) throw new Error("missing example");
    const check = example.response;
    const html = renderToStaticMarkup(<Result check={check} />);
    const text = html
      .replace(/<[^>]+>/g, " ")
      .replace(/&#x27;/g, "'")
      .replace(/&quot;/g, '"')
      .replace(/&lt;/g, "<")
      .replace(/&gt;/g, ">")
      .replace(/&amp;/g, "&");

    expect(text).toContain(String(check.score?.value));
    expect(text).toContain(check.analysis_version.slice(0, 12));
    for (const requirement of check.requirements) {
      expect(text).toContain(requirement.text_en);
      expect(text).toContain(requirement.source);
      for (const citation of requirement.citations) expect(text).toContain(citation.quote);
    }
    for (const label of ["Meets", "Partially meets", "Does not meet"]) expect(text).toContain(label);
    expect(html).toContain('href="https://github.com/khaled-haikel/fit-check"');
  });

  it("says when a verdict was not verified against the evidence", () => {
    if (!example) throw new Error("missing example");
    const [first, ...rest] = example.response.requirements;
    const check = { ...example.response, requirements: [{ ...first, downgraded: true }, ...rest] };
    expect(renderToStaticMarkup(<Result check={check} />)).toContain("Not verified against the evidence");
  });
});
