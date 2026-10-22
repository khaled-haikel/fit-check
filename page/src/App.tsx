import { useEffect, useRef, useState } from "react";
import { CheckForm } from "./components/CheckForm";
import { Examples } from "./components/Examples";
import { HowItWorks } from "./components/HowItWorks";
import { ProblemNotice } from "./components/ProblemNotice";
import { Progress } from "./components/Progress";
import { Result } from "./components/Result";
import { useCheck } from "./lib/useCheck";

const LINKS = [
  { href: "https://www.linkedin.com/in/khaled-haikel", label: "LinkedIn" },
  { href: "https://github.com/khaled-haikel", label: "GitHub" },
];

export function App() {
  const [text, setText] = useState("");
  const { state, submit } = useCheck();
  const resultHeading = useRef<HTMLHeadingElement>(null);

  // Move keyboard and screen reader focus to the result once it is there.
  useEffect(() => {
    if (state.phase === "done") resultHeading.current?.focus();
  }, [state.phase]);

  return (
    <>
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:bg-paper focus:p-2">
        Skip to content
      </a>
      <header className="border-b border-rule">
        <div className="mx-auto max-w-3xl px-4 py-10 sm:px-6 sm:py-14">
          <h1 className="text-sm font-semibold uppercase tracking-widest text-muted">Fit Check</h1>
          <p className="mt-3 font-serif text-3xl leading-tight sm:text-4xl">
            Paste a job description and see, requirement by requirement, whether Khaled Haikel meets it, with the evidence.
          </p>
          <nav aria-label="Profile" className="mt-6">
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
        <section aria-label="Analyze a job description" className="space-y-6">
          <CheckForm value={text} busy={state.phase === "running"} onChange={setText} onSubmit={submit} />
          {state.phase === "running" && <Progress status={state.status} check={state.check} />}
          {state.phase === "error" && <ProblemNotice problem={state.problem} onRetry={() => submit(text)} />}
          {state.phase === "done" && (
            <div>
              <h2 ref={resultHeading} tabIndex={-1} className="mb-4 font-serif text-3xl">
                Result
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
          Fit Check by Khaled Haikel.{" "}
          <a href="https://github.com/khaled-haikel/fit-check" className="text-accent underline underline-offset-2">
            Source code
          </a>
        </div>
      </footer>
    </>
  );
}
