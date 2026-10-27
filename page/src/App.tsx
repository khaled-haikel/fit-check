import { useEffect, useRef, useState } from "react";
import { CheckForm } from "./components/CheckForm";
import { Examples } from "./components/Examples";
import { HowItWorks } from "./components/HowItWorks";
import { ProblemNotice } from "./components/ProblemNotice";
import { Progress } from "./components/Progress";
import { Result } from "./components/Result";
import { LANGS, LangContext, MESSAGES, detectLang, saveLang, withLang, type Lang } from "./i18n";
import { useCheck } from "./lib/useCheck";

const LINKS = [
  { href: "https://www.linkedin.com/in/khaled-haikel", label: "LinkedIn" },
  { href: "https://github.com/khaled-haikel", label: "GitHub" },
];

function LanguageSwitch({ lang, onChange }: { lang: Lang; onChange: (lang: Lang) => void }) {
  const t = MESSAGES[lang];
  return (
    <div role="group" aria-label={t.languageGroup} className="flex items-center gap-1 text-sm font-semibold">
      {LANGS.map((option, index) => (
        <span key={option} className="flex items-center gap-1">
          {index > 0 && (
            <span aria-hidden="true" className="text-muted">
              |
            </span>
          )}
          <button
            type="button"
            lang={MESSAGES[option].htmlLang}
            aria-label={`${option.toUpperCase()}, ${MESSAGES[option].languageNames[option]}`}
            aria-pressed={option === lang}
            onClick={() => onChange(option)}
            className={`rounded px-1.5 py-0.5 ${option === lang ? "text-ink underline underline-offset-4" : "text-muted"}`}
          >
            {option.toUpperCase()}
          </button>
        </span>
      ))}
    </div>
  );
}

export function App({ initialLang }: { initialLang?: Lang }) {
  const [lang, setLang] = useState<Lang>(() => initialLang ?? detectLang());
  const t = MESSAGES[lang];
  const [text, setText] = useState("");
  const { state, submit } = useCheck();
  const resultHeading = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    document.documentElement.lang = t.htmlLang;
    document.title = t.documentTitle;
  }, [t]);

  // Move keyboard and screen reader focus to the result once it is there.
  useEffect(() => {
    if (state.phase === "done") resultHeading.current?.focus();
  }, [state.phase]);

  // The choice is kept for next time and written into the address, without reloading.
  function chooseLang(next: Lang) {
    setLang(next);
    saveLang(next);
    window.history.replaceState(window.history.state, "", withLang(window.location.href, next));
  }

  return (
    <LangContext.Provider value={lang}>
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:bg-paper focus:p-2">
        {t.skipToContent}
      </a>
      <header className="border-b border-rule">
        <div className="mx-auto max-w-3xl px-4 py-10 sm:px-6 sm:py-14">
          <div className="flex items-center justify-between gap-4">
            <h1 className="text-sm font-semibold uppercase tracking-widest text-muted">Fit Check</h1>
            <LanguageSwitch lang={lang} onChange={chooseLang} />
          </div>
          <p className="mt-3 font-serif text-3xl leading-tight sm:text-4xl">{t.tagline}</p>
          <nav aria-label={t.profileNav} className="mt-6">
            <ul className="flex flex-wrap gap-x-6 gap-y-2">
              {LINKS.map((link) => (
                <li key={link.label}>
                  <a href={link.href} className="font-medium text-accent underline underline-offset-4">
                    {link.label}
                  </a>
                </li>
              ))}
            </ul>
          </nav>
        </div>
      </header>

      <main id="main" className="mx-auto max-w-3xl space-y-14 px-4 py-10 sm:px-6">
        <section aria-label={t.analyzeSection} className="space-y-6">
          <CheckForm value={text} busy={state.phase === "running"} onChange={setText} onSubmit={submit} />
          {state.phase === "running" && <Progress status={state.status} check={state.check} />}
          {state.phase === "error" && <ProblemNotice problem={state.problem} onRetry={() => submit(text)} />}
          {state.phase === "done" && (
            <div>
              <h2 ref={resultHeading} tabIndex={-1} className="mb-4 font-serif text-3xl">
                {t.result.heading}
              </h2>
              <Result check={state.check} headingId="result-score" />
            </div>
          )}
        </section>

        <Examples />
        <HowItWorks />
      </main>

      <footer className="border-t border-rule">
        <div className="mx-auto max-w-3xl px-4 py-6 text-sm text-muted sm:px-6">
          {t.footer.by}{" "}
          <a href="https://github.com/khaled-haikel/fit-check" className="text-accent underline underline-offset-2">
            {t.footer.source}
          </a>
        </div>
      </footer>
    </LangContext.Provider>
  );
}
