// Applies one frozen OpenRouter decoding profile. Load before pi-audit.ts so the
// observer records the effective payload. Prompts, tools and headers are untouched.
import { readFileSync } from "node:fs";

const SCHEMA = "openrouter-fixed-decoding-v1";
const SAMPLING = ["temperature", "top_p", "top_k", "frequency_penalty", "max_tokens"];
const REPLACED = ["reasoning", "reasoning_effort", "max_completion_tokens", "max_output_tokens"];

export default function (pi: any) {
  const path = process.env.RANDOM_BENCH_REQUEST_PROFILE;
  if (!path) throw new Error("RANDOM_BENCH_REQUEST_PROFILE is required");
  const profile = JSON.parse(readFileSync(path, "utf8"));
  if (profile?.schema !== SCHEMA) throw new Error("Unsupported request profile schema");
  for (const key of SAMPLING)
    if (typeof profile[key] !== "number" || !Number.isFinite(profile[key]))
      throw new Error(`Request profile ${key} must be a finite number`);
  if (!Array.isArray(profile.accepted_models) || !profile.accepted_models.length)
    throw new Error("Request profile accepted_models is required");
  if (profile.reasoning !== null && (typeof profile.reasoning !== "object" || Array.isArray(profile.reasoning)))
    throw new Error("Request profile reasoning must be null or an object");
  if (typeof profile.provider !== "object" || !Array.isArray(profile.provider?.only))
    throw new Error("Request profile provider routing is required");

  pi.on("before_provider_request", (event: any) => {
    const payload = event.payload;
    if (!payload || typeof payload !== "object" || !Array.isArray(payload.messages))
      throw new Error("Expected an OpenRouter Chat Completions payload");
    if (event.model?.provider !== undefined && event.model.provider !== "openrouter")
      throw new Error(`Request profile applies only to openrouter, got ${event.model.provider}`);
    if (!profile.accepted_models.includes(payload.model))
      throw new Error(`Model ${payload.model} is not permitted by the request profile`);
    for (const key of REPLACED) delete payload[key];
    for (const key of SAMPLING) payload[key] = profile[key];
    // Non-reasoning profiles omit the field rather than sending enabled=false.
    if (profile.reasoning !== null) payload.reasoning = structuredClone(profile.reasoning);
    payload.provider = structuredClone(profile.provider);
    return payload;
  });
}
