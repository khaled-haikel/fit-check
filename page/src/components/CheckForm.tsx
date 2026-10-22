import { JD_MAX_CHARS, JD_MIN_CHARS, normalizeJobDescription, withinLimits } from "../lib/normalize";

interface Props {
  value: string;
  busy: boolean;
  onChange: (value: string) => void;
  onSubmit: (jobDescription: string) => void;
}

export function CheckForm({ value, busy, onChange, onSubmit }: Props) {
  const length = normalizeJobDescription(value).length;
  const valid = withinLimits(length);
  const hint =
    length < JD_MIN_CHARS
      ? `At least ${JD_MIN_CHARS} characters.`
      : length > JD_MAX_CHARS
        ? `At most ${JD_MAX_CHARS.toLocaleString("en-US")} characters.`
        : "Ready to analyze.";
  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        if (valid && !busy) onSubmit(value);
      }}
    >
      <label htmlFor="job-description" className="block font-serif text-2xl">
        Job description
      </label>
      <textarea
        id="job-description"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        rows={12}
        spellCheck={false}
        aria-describedby="job-description-count job-description-privacy"
        className="mt-3 block w-full rounded-lg border border-rule bg-surface p-3 font-sans text-base leading-relaxed"
        placeholder="Paste the full job posting here."
      />
      <div className="mt-2 flex flex-wrap items-baseline justify-between gap-2 text-sm">
        <p id="job-description-count" className={valid ? "text-muted" : "text-ink"}>
          {length.toLocaleString("en-US")} / {JD_MAX_CHARS.toLocaleString("en-US")} characters. {hint}
        </p>
      </div>
      <p id="job-description-privacy" className="mt-1 text-sm text-muted">
        Job descriptions are stored for 30 days to serve cached results. Your IP address is kept only as a hash, for two
        days, to apply the daily limit.
      </p>
      <button
        type="submit"
        disabled={!valid || busy}
        className="mt-4 rounded-md bg-ink px-5 py-2.5 font-semibold text-paper disabled:cursor-not-allowed disabled:opacity-50"
      >
        {busy ? "Analyzing..." : "Analyze"}
      </button>
    </form>
  );
}
