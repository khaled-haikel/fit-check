import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { App } from "./App";

describe("the page", () => {
  const html = renderToStaticMarkup(<App />);

  it("states what is kept", () => {
    expect(html).toContain(
      "Job descriptions are stored for 30 days to serve cached results. Your IP address is kept only as a hash, for two days, to apply the daily limit.",
    );
  });

  it("links LinkedIn and GitHub, and no CV", () => {
    expect(html).toContain('href="https://www.linkedin.com/in/khaled-haikel"');
    expect(html).toContain('href="https://github.com/khaled-haikel"');
    expect(html).not.toMatch(/cv\.pdf|\bCV\b/);
  });
});
