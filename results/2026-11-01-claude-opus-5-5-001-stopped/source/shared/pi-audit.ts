// Observer only: no tools, prompt changes, headers, or sampling overrides.
import { appendFileSync } from "node:fs";

export default function (pi: any) {
  const path = process.env.RANDOM_BENCH_AUDIT;
  if (!path) throw new Error("RANDOM_BENCH_AUDIT is required");
  const record = (event: object) => appendFileSync(path, JSON.stringify({
    timestamp: new Date().toISOString(), ...event,
  }) + "\n");
  pi.on("before_provider_request", (event: any) => {
    const payload: Record<string, unknown> = {};
    for (const key of ["model", "input", "instructions", "reasoning", "temperature",
      "top_p", "seed", "max_output_tokens", "tools", "tool_choice", "stream", "store"])
      if (event.payload[key] !== undefined) payload[key] = event.payload[key];
    record({ type: "request", payload });
  });
  // Pi 1.3.0's response hook misses thrown HTTP errors. Observe fetch itself,
  // before the SDK rejects 4xx/5xx, without changing request/response contents.
  const upstreamFetch = globalThis.fetch;
  globalThis.fetch = async function (...args: Parameters<typeof fetch>) {
    const response = await upstreamFetch(...args);
    const input = args[0];
    const url = new URL(typeof input === "string" ? input : input instanceof URL ? input.href : input.url);
    if (!url.pathname.endsWith("/responses")) return response;
    const headers: Record<string, string> = {};
    for (const [key, value] of response.headers.entries()) {
      const name = key.toLowerCase();
      if (["retry-after", "retry-after-ms", "x-request-id", "date"].includes(name)
          || name.startsWith("x-ratelimit-")) headers[name] = String(value);
    }
    record({ type: "http_response", status: response.status, headers,
      endpoint: url.origin + url.pathname });
    return response;
  };
}
