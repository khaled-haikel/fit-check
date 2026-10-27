import type { Citation, Proof, Requirement } from "../api/types";
import { useLang } from "../i18n";
import { VerdictBadge } from "./VerdictBadge";

// Card titles, quotes and notes and the model's reasons stay in English (lang="en"), as written.
function ProofLinks({ proof }: { proof: Proof[] }) {
  const { t } = useLang();
  if (proof.length === 0) return null;
  return (
    <ul className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-sm">
      {proof.map((item, index) => (
        <li key={index}>
          {item.url ? (
            <a href={item.url} target="_blank" rel="noopener noreferrer" className="text-accent underline underline-offset-2">
              {t.proof[item.type]}
            </a>
          ) : (
            <span className="text-muted">{t.proof[item.type]}</span>
          )}
          {item.note && (
            <span className="text-muted">
              : <span lang="en">{item.note}</span>
            </span>
          )}
        </li>
      ))}
    </ul>
  );
}

function CitationItem({ citation }: { citation: Citation }) {
  return (
    <li className="border-l-2 border-rule pl-4">
      <blockquote lang="en" className="font-serif text-base italic leading-relaxed">
        {citation.quote}
      </blockquote>
      <p lang="en" className="mt-1 text-sm font-medium">
        {citation.title}
      </p>
      <ProofLinks proof={citation.proof} />
    </li>
  );
}

export function RequirementCard({ requirement }: { requirement: Requirement }) {
  const { lang, t } = useLang();
  // In Portuguese the posting's own words lead, with the English version below; in English, the reverse.
  const original = lang === "pt";
  return (
    <article className="rounded-lg border border-rule bg-surface p-4 sm:p-5">
      <div className="flex flex-wrap items-center gap-2">
        {requirement.verdict && <VerdictBadge verdict={requirement.verdict} />}
        <span className="rounded-full border border-rule px-2.5 py-0.5 text-sm text-muted">{t.types[requirement.type]}</span>
      </div>
      <h4 className="mt-3 text-lg font-semibold leading-snug" lang={original ? undefined : "en"}>
        {original ? requirement.source : requirement.text_en}
      </h4>
      <p className="mt-2 text-sm text-muted">
        <span className="font-medium">{original ? t.requirement.inEnglish : t.requirement.inPosting}</span>
        <span lang={original ? "en" : undefined}>{original ? requirement.text_en : requirement.source}</span>
      </p>
      {requirement.downgraded && <p className="mt-3 text-sm font-semibold text-miss">{t.requirement.notVerified}</p>}
      {requirement.reason && (
        <p lang="en" className="mt-3 leading-relaxed">
          {requirement.reason}
        </p>
      )}
      {requirement.citations.length > 0 && (
        <div className="mt-4">
          <h5 className="text-sm font-semibold uppercase tracking-wide text-muted">{t.requirement.citedCards}</h5>
          <ul className="mt-2 space-y-3">
            {requirement.citations.map((citation, index) => (
              <CitationItem key={`${citation.card_id}-${index}`} citation={citation} />
            ))}
          </ul>
        </div>
      )}
    </article>
  );
}
