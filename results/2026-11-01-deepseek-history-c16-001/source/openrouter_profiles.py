"""Frozen OpenRouter decoding profiles and their offline evidence audit."""

import copy
import math
import re

SCHEMA = "openrouter-fixed-decoding-v1"
KEYS = {
    "schema",
    "temperature",
    "top_p",
    "top_k",
    "frequency_penalty",
    "max_tokens",
    "reasoning",
    "provider",
    "expected_provider",
    "accepted_models",
    "mode",
    "release_period",
    "stratum",
    "quantization",
}
# Sent to the provider; the remaining keys are frozen metadata only.
PAYLOAD_KEYS = (
    "temperature",
    "top_p",
    "top_k",
    "frequency_penalty",
    "max_tokens",
    "reasoning",
    "provider",
)
TRANSPORT_KEYS = {"model", "messages", "stream", "stream_options", "store"}
TEMPERATURES = (0.7, 1.0)
MODES = {
    "non_reasoning": ("off", (None,)),
    "off": ("off", ({"enabled": False},)),
    "on": ("high", ({"enabled": True}, {"enabled": True, "effort": "high"})),
    "required": ("high", ({"enabled": True}, {"enabled": True, "effort": "high"})),
}
STRATA = {
    "matched_fp8": ("SiliconFlow", "fp8"),
    "historical_anchor": ("DeepInfra", "fp4"),
}
FINISH_REASONS = {"stop", "length"}


def _number(value):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _integer(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _same(left, right):
    """JSON equality without Python's True == 1 == 1.0 coercions."""
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(
            _same(left[k], right[k]) for k in left
        )
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(map(_same, left, right))
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    return left == right


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def validate_profile(profile, model, thinking):
    _require(isinstance(profile, dict), "Profile must be an object")
    _require(
        set(profile) == KEYS,
        f"Profile keys mismatch: missing {sorted(KEYS - set(profile))}, "
        f"unexpected {sorted(set(profile) - KEYS)}",
    )
    _require(profile["schema"] == SCHEMA, "Unsupported profile schema")
    _require(
        _number(profile["temperature"]) and profile["temperature"] in TEMPERATURES,
        "Temperature must be 0.7 or 1.0",
    )
    _require(_number(profile["top_p"]) and profile["top_p"] == 1.0, "top_p must be 1.0")
    _require(_integer(profile["top_k"]) and profile["top_k"] == 0, "top_k must be 0")
    _require(
        _number(profile["frequency_penalty"]) and profile["frequency_penalty"] == 0,
        "frequency_penalty must be 0",
    )
    _require(
        _integer(profile["max_tokens"]) and profile["max_tokens"] == 4096,
        "max_tokens must be 4096",
    )
    mode = profile["mode"]
    _require(isinstance(mode, str) and mode in MODES, "Unknown reasoning mode")
    pi_thinking, reasoning = MODES[mode]
    _require(
        any(_same(profile["reasoning"], r) for r in reasoning),
        f"Reasoning setting does not match mode {mode}",
    )
    _require(thinking == pi_thinking, f"Mode {mode} requires Pi thinking={pi_thinking}")
    stratum = profile["stratum"]
    _require(isinstance(stratum, str) and stratum in STRATA, "Unknown stratum")
    provider_name, quantization = STRATA[stratum]
    _require(
        profile["expected_provider"] == provider_name,
        f"Stratum {stratum} requires provider {provider_name}",
    )
    _require(
        profile["quantization"] == quantization,
        f"Stratum {stratum} requires quantization {quantization}",
    )
    routing = profile["provider"]
    _require(
        isinstance(routing, dict)
        and set(routing) == {"only", "allow_fallbacks", "require_parameters"},
        "Provider routing keys mismatch",
    )
    _require(
        routing["allow_fallbacks"] is False and routing["require_parameters"] is True,
        "Routing must disable fallbacks and require parameters",
    )
    only = routing["only"]
    _require(
        isinstance(only, list)
        and len(only) == 1
        and isinstance(only[0], str)
        and re.fullmatch(r"[a-z0-9-]+(?:/[a-z0-9-]+)+", only[0]),
        "Routing must pin exactly one provider tag",
    )
    parts = only[0].split("/")
    _require(
        parts[0] == provider_name.lower() and parts[1] == quantization,
        "Provider tag disagrees with expected provider or quantization",
    )
    accepted = profile["accepted_models"]
    _require(
        isinstance(accepted, list)
        and accepted
        and all(isinstance(m, str) and m and m == m.strip() for m in accepted)
        and len(set(accepted)) == len(accepted),
        "accepted_models must be unique nonempty strings",
    )
    _require(
        isinstance(model, str) and model in accepted,
        "Requested model is not in accepted_models",
    )
    period = profile["release_period"]
    _require(
        isinstance(period, str)
        and re.fullmatch(r"[0-9]{4}-(?:0[1-9]|1[0-2])", period, re.ASCII),
        "release_period must be YYYY-MM",
    )


def payload_overrides(profile):
    return {
        key: copy.deepcopy(profile[key])
        for key in PAYLOAD_KEYS
        if not (key == "reasoning" and profile[key] is None)
    }


def _reasoning_tokens(event):
    usage = event.get("usage")
    if usage is None:
        return None
    _require(isinstance(usage, dict), "Usage must be an object")
    details = usage.get("completion_tokens_details")
    if details is None:
        return None
    _require(isinstance(details, dict), "completion_tokens_details must be an object")
    tokens = details.get("reasoning_tokens")
    if tokens is None:
        return None
    _require(_integer(tokens) and tokens >= 0, "Invalid reasoning_tokens")
    return tokens


def audit(config, payload, metadata, final):
    profile = config["openrouter_profile"]
    validate_profile(profile, config["model"], config["thinking"])
    overrides = payload_overrides(profile)

    _require(isinstance(payload, dict), "Payload must be an object")
    unexpected = set(payload) - TRANSPORT_KEYS - set(overrides)
    _require(not unexpected, f"Unexpected payload fields: {sorted(unexpected)}")
    _require(payload.get("model") == config["model"], "Requested model mismatch")
    expected_messages = [
        {
            "role": "system",
            "content": config["system_prompt"]
            + "\n\n<cwd>\n"
            + config["cwd"]
            + "\n</cwd>",
        },
        {"role": "user", "content": [{"type": "text", "text": config["prompt"]}]},
    ]
    _require(
        payload.get("messages") == expected_messages,
        "Effective OpenRouter messages mismatch",
    )
    for key, value in overrides.items():
        _require(
            key in payload and _same(payload[key], value),
            f"Payload {key} does not match profile",
        )
    for key in ("stream", "store"):
        _require(
            key not in payload or isinstance(payload[key], bool),
            f"Invalid transport field {key}",
        )
    if "stream_options" in payload:
        options = payload["stream_options"]
        _require(
            isinstance(options, dict)
            and set(options) <= {"include_usage"}
            and all(isinstance(v, bool) for v in options.values()),
            "Invalid stream_options",
        )

    _require(
        isinstance(metadata, list)
        and metadata
        and all(isinstance(e, dict) for e in metadata),
        "Missing OpenRouter response metadata",
    )
    _require(
        not any(e.get("error") for e in metadata), "OpenRouter metadata reported error"
    )
    ids = {e["id"] for e in metadata if e.get("id")}
    _require(len(ids) == 1, "Expected exactly one response ID")
    response_id = next(iter(ids))
    if final.get("responseId") is not None:
        _require(final["responseId"] == response_id, "Native response ID mismatch")
    models = {e["model"] for e in metadata if e.get("model")}
    _require(len(models) == 1, "Expected exactly one observed model")
    observed_model = next(iter(models))
    _require(
        observed_model in profile["accepted_models"],
        f"Observed model {observed_model} is not accepted",
    )
    providers = {e["provider"] for e in metadata if e.get("provider")}
    _require(providers == {profile["expected_provider"]}, "Upstream provider mismatch")

    finish = set()
    for event in metadata:
        reasons = event.get("finish_reasons", [])
        _require(
            isinstance(reasons, list) and all(isinstance(r, str) for r in reasons),
            "Invalid finish_reasons",
        )
        _require(
            isinstance(event.get("has_reasoning", False), bool),
            "Invalid has_reasoning",
        )
        finish.update(r for r in reasons if r)
    _require(finish, "Missing finish reason")
    _require(finish <= FINISH_REASONS, f"Unexpected finish reasons {sorted(finish)}")
    _require(len(finish) == 1, "Conflicting finish reasons")
    stop_reason = final.get("stopReason")
    _require(stop_reason in FINISH_REASONS, f"Unexpected stop reason {stop_reason}")
    # Either signal of truncation is a terminal invalid response, not a fatal error.
    truncated = stop_reason == "length" or "length" in finish

    # Cumulative usage chunks repeat totals; the maximum is the final total.
    counts = [t for t in map(_reasoning_tokens, metadata) if t is not None]
    reasoning_tokens = max(counts) if counts else None
    if profile["mode"] in ("non_reasoning", "off"):
        _require(
            not reasoning_tokens, "Reasoning-off profile reported reasoning tokens"
        )
        _require(
            not any(e.get("has_reasoning") is True for e in metadata),
            "Reasoning-off profile streamed reasoning",
        )
        _require(
            not any(
                b.get("type") == "thinking" and (b.get("thinking") or "").strip()
                for b in final.get("content", [])
            ),
            "Reasoning-off profile returned reasoning content",
        )
    return {
        "observed_model": observed_model,
        "upstream_provider": profile["expected_provider"],
        "system_fingerprints": sorted(
            {e["system_fingerprint"] for e in metadata if e.get("system_fingerprint")}
        ),
        "response_id": response_id,
        "reasoning_tokens": reasoning_tokens,
        "truncated": truncated,
    }
