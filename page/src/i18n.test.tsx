import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { RequirementCard } from "./components/RequirementCard";
import { EXAMPLES } from "./examples";
import { LANGS, LangContext, MESSAGES, detectLang, pickLang, readStoredLang, saveLang, withLang } from "./i18n";

const PRIVACY = {
  en: "Job descriptions are stored for 30 days to serve cached results. Your IP address is kept only as a hash, for two days, to apply the daily limit.",
  pt: "A descrição da vaga fica guardada por 30 dias para responder pelo cache. Seu endereço IP é guardado só como hash, por dois dias, para aplicar o limite diário.",
};
const DASHES = [String.fromCodePoint(0x2014), String.fromCodePoint(0x2013)];

// Every key path of a dictionary, with arrays by index.
function keyPaths(value: unknown, prefix = ""): string[] {
  if (typeof value !== "object" || value === null) return [prefix];
  return Object.entries(value).flatMap(([key, child]) => keyPaths(child, prefix ? `${prefix}.${key}` : key));
}

// Every text a dictionary can produce; functions are called with sample arguments.
function texts(value: unknown): string[] {
  if (typeof value === "string") return [value];
  if (typeof value === "function") {
    return [1, 2].map((sample) => String((value as (...args: unknown[]) => unknown)(...Array(value.length).fill(sample))));
  }
  if (typeof value === "object" && value !== null) return Object.values(value).flatMap(texts);
  return [];
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("the dictionaries", () => {
  it("have the same keys in both languages", () => {
    expect(keyPaths(MESSAGES.pt).sort()).toEqual(keyPaths(MESSAGES.en).sort());
  });

  it("have no em or en dash in any text", () => {
    for (const lang of LANGS) {
      const offenders = texts(MESSAGES[lang]).filter((text) => DASHES.some((dash) => text.includes(dash)));
      expect(offenders).toEqual([]);
    }
  });

  it("carry the exact privacy sentences, and the page shows them", () => {
    for (const lang of LANGS) {
      expect(MESSAGES[lang].form.privacy).toBe(PRIVACY[lang]);
      expect(renderToStaticMarkup(<App initialLang={lang} />)).toContain(PRIVACY[lang]);
    }
  });

  it("carry the fixed Portuguese texts", () => {
    expect(MESSAGES.pt.tagline).toBe(
      "Cole a descrição de uma vaga e veja, requisito por requisito, se o Khaled Haikel atende, com a evidência.",
    );
    expect(MESSAGES.pt.result.score).toBe("Aderência");
    expect(MESSAGES.pt.requirement.citedCards).toBe("Fichas citadas (texto original em inglês)");
    expect(MESSAGES.pt.verdicts).toEqual({ meets: "Atende", partial: "Atende em parte", does_not_meet: "Não atende" });
    expect(MESSAGES.pt.types).toEqual({ must: "Obrigatório", nice: "Desejável" });
  });

  it("name every recorded example", () => {
    for (const lang of LANGS) {
      for (const example of EXAMPLES) expect(MESSAGES[lang].examples.names[example.case]).toBeTruthy();
    }
  });
});

describe("the first language", () => {
  it("comes from ?lang= first", () => {
    expect(pickLang("?lang=pt", "en", ["en-US"])).toBe("pt");
    expect(pickLang("?x=1&lang=en", "pt", ["pt-BR"])).toBe("en");
  });

  it("then from the saved choice", () => {
    expect(pickLang("", "pt", ["en-US"])).toBe("pt");
    expect(pickLang("?lang=fr", "en", ["pt-BR"])).toBe("en");
  });

  it("then from the browser: pt* is Portuguese, anything else English", () => {
    expect(pickLang("", null, ["pt-BR", "en"])).toBe("pt");
    expect(pickLang("", null, ["PT"])).toBe("pt");
    expect(pickLang("", "xx", ["es-ES", "pt-BR"])).toBe("en");
    expect(pickLang("", null, [])).toBe("en");
  });

  it("survives a localStorage that throws", () => {
    const denied = () => {
      throw new DOMException("denied", "SecurityError");
    };
    expect(readStoredLang(denied)).toBeNull();
    expect(() => saveLang("pt", denied)).not.toThrow();
    const failing = () => ({ getItem: denied, setItem: denied });
    expect(readStoredLang(failing)).toBeNull();
    expect(() => saveLang("pt", failing)).not.toThrow();

    vi.stubGlobal("window", {
      location: { search: "" },
      get localStorage(): Storage {
        throw new DOMException("denied", "SecurityError");
      },
    });
    vi.stubGlobal("navigator", { languages: ["pt-BR"], language: "pt-BR" });
    expect(detectLang()).toBe("pt");
  });

  it("is written into the address without losing the rest of it", () => {
    expect(withLang("https://khaled-haikel.github.io/fit-check/?x=1#examples", "pt")).toBe("/fit-check/?x=1&lang=pt#examples");
    expect(withLang("https://khaled-haikel.github.io/fit-check/?lang=pt", "en")).toBe("/fit-check/?lang=en");
  });
});

describe("a requirement in Portuguese", () => {
  const requirement = EXAMPLES[0].response.requirements.find((r) => r.source !== r.text_en) ?? EXAMPLES[0].response.requirements[0];

  function render(lang: "en" | "pt"): string {
    return renderToStaticMarkup(
      <LangContext.Provider value={lang}>
        <RequirementCard requirement={requirement} />
      </LangContext.Provider>,
    );
  }

  const escape = (text: string) => text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#x27;");

  it("leads with the posting's own words, with the English version below", () => {
    const html = render("pt");
    const heading = /<h4[^>]*>([\s\S]*?)<\/h4>/.exec(html)?.[1];
    expect(heading).toBe(escape(requirement.source));
    expect(html.indexOf(escape(requirement.text_en))).toBeGreaterThan(html.indexOf("</h4>"));
    expect(html).toContain("Em inglês: ");
    expect(html).toContain("Fichas citadas (texto original em inglês)");
  });

  it("leads with the English version in English", () => {
    const heading = /<h4[^>]*>([\s\S]*?)<\/h4>/.exec(render("en"))?.[1];
    expect(heading).toBe(escape(requirement.text_en));
  });
});
