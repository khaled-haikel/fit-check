// Recorded analyses, shown without calling the API. scripts/refresh-examples.mjs writes them.
import type { Check } from "../api/types";

export interface Example {
  case: string;
  role_title: string;
  recorded_at: string;
  response: Check;
}

const files = import.meta.glob<Example>("./*.json", { eager: true, import: "default" });

export const EXAMPLES: Example[] = Object.keys(files)
  .sort()
  .map((path) => files[path]);
