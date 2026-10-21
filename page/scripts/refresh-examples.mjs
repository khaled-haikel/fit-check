// Re-records the page's examples: runs the job descriptions of two dev cases
// through the public API and saves each final GET /checks/{id} response in
// src/examples/. Each run is a paid analysis unless the API already has it.
//
//   API_URL=https://... node scripts/refresh-examples.mjs
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const CASES = ["dev-01-ai-automation-latam", "dev-02-fullstack-ai-native"];
const MAX_WAIT_MS = 5 * 60 * 1000;

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "..", "..");
const apiUrl = (process.env.API_URL || process.env.VITE_API_URL || "").replace(/\/+$/, "");
if (!apiUrl) {
  console.error("Set API_URL to the API's base URL.");
  process.exit(1);
}

// The case files are YAML; only two keys are needed, so they are read without a YAML library.
function readCase(id) {
  const lines = readFileSync(join(root, "evals", "cases", "dev", `${id}.yaml`), "utf8").split(/\r?\n/);
  const titleLine = lines.find((line) => line.startsWith("role_title:"));
  let roleTitle = titleLine.slice("role_title:".length).trim();
  if (roleTitle.startsWith("'")) roleTitle = roleTitle.slice(1, -1).replaceAll("''", "'");
  else if (roleTitle.startsWith('"')) roleTitle = JSON.parse(roleTitle);

  const start = lines.findIndex((line) => /^job_description: \|[-+]?$/.test(line));
  if (start < 0) throw new Error(`${id}: job_description is not a literal block`);
  const block = [];
  for (const line of lines.slice(start + 1)) {
    if (line !== "" && !line.startsWith("  ")) break;
    block.push(line.slice(2));
  }
  while (block.length && block[block.length - 1] === "") block.pop();
  return { roleTitle, jobDescription: block.join("\n") + "\n" };
}

async function call(method, path, body) {
  const response = await fetch(apiUrl + path, {
    method,
    headers: body ? { "content-type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  return { status: response.status, body: await response.json() };
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

for (const id of CASES) {
  const { roleTitle, jobDescription } = readCase(id);
  const posted = await call("POST", "/checks", { job_description: jobDescription });
  if (posted.status !== 200 && posted.status !== 202) {
    console.error(`${id}: POST /checks answered ${posted.status} ${JSON.stringify(posted.body)}`);
    process.exit(1);
  }
  const { check_id: checkId } = posted.body;
  const interval = posted.body.poll_after_ms ?? 3000;
  const started = Date.now();
  let check;
  for (;;) {
    const got = await call("GET", `/checks/${checkId}`);
    check = got.body;
    if (got.status !== 200 || check.status === "done" || check.status === "failed") break;
    if (Date.now() - started > MAX_WAIT_MS) break;
    await sleep(interval);
  }
  if (check.status !== "done") {
    console.error(`${id}: ${checkId} ended as ${check.status} (${check.error ?? "no error code"})`);
    process.exit(1);
  }
  const example = { case: id, role_title: roleTitle, recorded_at: new Date().toISOString(), response: check };
  writeFileSync(join(here, "..", "src", "examples", `${id}.json`), JSON.stringify(example, null, 2) + "\n");
  console.log(`${id}: ${checkId} done, score ${check.score.value}, ${check.requirements.length} requirements, ${check.usage.cost_usd} USD`);
}
