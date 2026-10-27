import { describeProblem, type Problem } from "../api/problems";
import { useLang } from "../i18n";

export function ProblemNotice({ problem, onRetry }: { problem: Problem; onRetry: () => void }) {
  const { t } = useLang();
  const message = describeProblem(problem, t);
  return (
    <section role="alert" className="rounded-lg border border-miss bg-miss-bg p-5">
      <h3 className="font-serif text-2xl text-miss">{message.title}</h3>
      <p className="mt-2">{message.body}</p>
      {message.code && <p className="mt-2 text-sm text-muted">{t.problem.code(message.code)}</p>}
      <div className="mt-4 flex flex-wrap gap-3">
        {message.retry && (
          <button type="button" onClick={onRetry} className="rounded-md bg-ink px-4 py-2 font-semibold text-paper">
            {t.problem.retry}
          </button>
        )}
        {message.examples && (
          <a href="#examples" className="rounded-md border border-ink px-4 py-2 font-semibold">
            {t.problem.examples}
          </a>
        )}
      </div>
    </section>
  );
}
