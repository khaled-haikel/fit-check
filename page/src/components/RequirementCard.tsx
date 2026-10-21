import type { Citation, Proof, Requirement } from "../api/types";
import { TYPE_LABEL } from "../lib/results";
import { VerdictBadge } from "./VerdictBadge";

const PROOF_LABEL: Record<Proof["type"], string> = {
  repo: "Repository",
  live: "Live site",
  demo: "Demo",
  doc: "Document",
  none: "No public link",
};

function ProofLinks({ proof }: { proof: Proof[] }) {
  if (proof.length === 0) return null;
  return (
    <ul className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-sm">
      {proof.map((item, index) => (
        <li key={index}>
          {item.url ? (
            <a href={item.url} target="_blank" rel="noopener noreferrer" className="text-accent underline underline-offset-2">
              {PROOF_LABEL[item.type]}
            </a>
          ) : (
            <span className="text-muted">{PROOF_LABEL[item.type]}</span>
          )}
          {item.note && <span className="text-muted">: {item.note}</span>}
        </li>
      ))}
    </ul>
  );
}

function CitationItem({ citation }: { citation: Citation }) {
  return (
    <li className="border-l-2 border-rule pl-4">
      <blockquote className="font-serif text-base italic leading-relaxed">{citation.quote}</blockquote>
      <p className="mt-1 text-sm font-medium">{citation.title}</p>
      <ProofLinks proof={citation.proof} />
    </li>
  );
}

export function RequirementCard({ requirement }: { requirement: Requirement }) {
  return (
    <article className="rounded-lg border border-rule bg-surface p-4 sm:p-5">
      <div className="flex flex-wrap items-center gap-2">
        {requirement.verdict && <VerdictBadge verdict={requirement.verdict} />}
        <span className="rounded-full border border-rule px-2.5 py-0.5 text-sm text-muted">{TYPE_LABEL[requirement.type]}</span>
      </div>
      <h4 className="mt-3 text-lg font-semibold leading-snug">{requirement.text_en}</h4>
      <p className="mt-2 text-sm text-muted">
        <span className="font-medium">In the job description: </span>
        {requirement.source}
      </p>
      {requirement.downgraded && (
        <p className="mt-3 text-sm font-semibold text-miss">Not verified against the evidence</p>
      )}
      {requirement.reason && <p className="mt-3 leading-relaxed">{requirement.reason}</p>}
      {requirement.citations.length > 0 && (
        <div className="mt-4">
          <h5 className="text-sm font-semibold uppercase tracking-wide text-muted">Evidence</h5>
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
