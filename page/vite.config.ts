import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { loadEnv, type Plugin } from "vite";
import { defineConfig } from "vitest/config";

// A content security policy for the built page: its own scripts, styles and images only,
// and network calls only to the API.
function contentSecurityPolicy(apiUrl: string | undefined): Plugin {
  const connect = apiUrl ? new URL(apiUrl).origin : "'none'";
  const policy = [
    "default-src 'none'",
    "script-src 'self'",
    "style-src 'self'",
    "img-src 'self'",
    "font-src 'self'",
    `connect-src ${connect}`,
    "base-uri 'none'",
    "form-action 'none'",
  ].join("; ");
  return {
    name: "content-security-policy",
    apply: "build",
    transformIndexHtml: () => [{ tag: "meta", attrs: { "http-equiv": "Content-Security-Policy", content: policy }, injectTo: "head-prepend" }],
  };
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ".", "VITE_");
  return {
    // Served from https://khaled-haikel.github.io/fit-check/.
    base: "/fit-check/",
    plugins: [react(), tailwindcss(), contentSecurityPolicy(env.VITE_API_URL)],
    // The test numbers are read from ../evals/results.
    server: { fs: { allow: [".."] } },
    test: { environment: "node" },
  };
});
