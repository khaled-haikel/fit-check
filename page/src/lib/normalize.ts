// The character count the API checks, computed the way the API normalizes a job description
// (normalize_job_description in api/src/fitcheck_api/analysis/normalize.py).

export const JD_MIN_CHARS = 200;
export const JD_MAX_CHARS = 20000;

const CR = String.fromCharCode(13);

export function normalizeJobDescription(text: string): string {
  const lines = text
    .normalize("NFKC")
    .split(CR + "\n")
    .join("\n")
    .split(CR)
    .join("\n")
    .split("\n")
    .map((line) => line.replace(/[^\S\n]+/g, " ").trim());
  return lines.join("\n").replace(/\n{3,}/g, "\n\n").trim();
}

export function withinLimits(length: number): boolean {
  return length >= JD_MIN_CHARS && length <= JD_MAX_CHARS;
}
