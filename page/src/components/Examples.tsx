import { useState } from "react";
import { EXAMPLES } from "../examples";
import { useLang } from "../i18n";
import { formatDate } from "../lib/results";
import { Result } from "./Result";

export function Examples() {
  const { t } = useLang();
  const [open, setOpen] = useState<number | null>(null);
  const selected = open === null ? null : EXAMPLES[open];
  return (
    <section id="examples" aria-labelledby="examples-heading" className="scroll-mt-6">
      <h2 id="examples-heading" className="font-serif text-3xl">
        {t.examples.heading}
      </h2>
      <p className="mt-2 text-muted">{t.examples.intro}</p>
      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        {EXAMPLES.map((example, index) => (
          <button
            key={example.case}
            type="button"
            aria-expanded={open === index}
            aria-controls="example-result"
            onClick={() => setOpen(open === index ? null : index)}
            className={`rounded-lg border p-4 text-left ${open === index ? "border-ink bg-surface" : "border-rule"}`}
          >
            <span className="block font-semibold leading-snug">{t.examples.names[example.case] ?? example.role_title}</span>
            <span className="mt-1 block text-sm text-muted">
              {t.examples.summary(String(example.response.score?.value ?? "n/a"), example.response.requirements.length)}
            </span>
          </button>
        ))}
      </div>
      {selected && (
        <div id="example-result" className="mt-6">
          <p className="mb-4 text-sm text-muted">
            {t.examples.recorded(formatDate(selected.recorded_at, t.locale))}{" "}
            <span className="font-mono">{selected.response.analysis_version.slice(0, 12)}</span>.
          </p>
          <Result check={selected.response} headingId="example-score" />
        </div>
      )}
    </section>
  );
}
