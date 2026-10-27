import { useLang } from "../i18n";
import { JD_MAX_CHARS, JD_MIN_CHARS, normalizeJobDescription, withinLimits } from "../lib/normalize";
import { formatNumber } from "../lib/results";

interface Props {
  value: string;
  busy: boolean;
  onChange: (value: string) => void;
  onSubmit: (jobDescription: string) => void;
}

export function CheckForm({ value, busy, onChange, onSubmit }: Props) {
  const { t } = useLang();
  const length = normalizeJobDescription(value).length;
  const valid = withinLimits(length);
  const max = formatNumber(JD_MAX_CHARS, t.locale);
  const hint = length < JD_MIN_CHARS ? t.form.atLeast(JD_MIN_CHARS) : length > JD_MAX_CHARS ? t.form.atMost(max) : t.form.ready;
  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        if (valid && !busy) onSubmit(value);
      }}
    >
      <label htmlFor="job-description" className="block font-serif text-2xl">
        {t.form.label}
      </label>
      <textarea
        id="job-description"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        rows={12}
        spellCheck={false}
        aria-describedby="job-description-count job-description-privacy"
        className="mt-3 block w-full rounded-lg border border-rule bg-surface p-3 font-sans text-base leading-relaxed"
        placeholder={t.form.placeholder}
      />
      <p id="job-description-count" className={`mt-2 text-sm ${valid ? "text-muted" : "text-ink"}`}>
        {t.form.count(formatNumber(length, t.locale), max)} {hint}
      </p>
      <p id="job-description-privacy" className="mt-1 text-sm text-muted">
        {t.form.privacy}
      </p>
      <button
        type="submit"
        disabled={!valid || busy}
        className="mt-4 rounded-md bg-ink px-5 py-2.5 font-semibold text-paper disabled:cursor-not-allowed disabled:opacity-50"
      >
        {busy ? t.form.analyzing : t.form.analyze}
      </button>
    </form>
  );
}
