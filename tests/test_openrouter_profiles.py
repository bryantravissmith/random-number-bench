"""Offline checks for frozen OpenRouter decoding profiles. Never calls a provider."""

import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import openrouter_profiles as profiles

MODEL = "deepseek/deepseek-chat-v3.1"


def profile(**changes):
    base = {
        "schema": "openrouter-fixed-decoding-v1",
        "temperature": 0.7,
        "top_p": 1.0,
        "top_k": 0,
        "frequency_penalty": 0,
        "max_tokens": 4096,
        "reasoning": {"enabled": False},
        "provider": {
            "only": ["siliconflow/fp8"],
            "allow_fallbacks": False,
            "require_parameters": True,
        },
        "expected_provider": "SiliconFlow",
        "accepted_models": [MODEL, "deepseek/deepseek-chat-v3.1-20250821"],
        "mode": "off",
        "release_period": "2025-08",
        "stratum": "matched_fp8",
        "quantization": "fp8",
    }
    base.update(changes)
    return base


ON = {"reasoning": {"enabled": True}, "mode": "on"}
NON = {"reasoning": None, "mode": "non_reasoning"}


def config(**changes):
    p = changes.pop("p", profile())
    thinking = "high" if p["mode"] in ("on", "required") else "off"
    return {
        "model": MODEL,
        "thinking": thinking,
        "prompt": "Pick a number from 1 to 10.",
        "system_prompt": "You are a helpful assistant.",
        "cwd": "/tmp/neutral",
        "openrouter_profile": p,
        **changes,
    }


def payload(cfg):
    out = {
        "model": cfg["model"],
        "messages": [
            {
                "role": "system",
                "content": cfg["system_prompt"] + "\n\n<cwd>\n/tmp/neutral\n</cwd>",
            },
            {"role": "user", "content": [{"type": "text", "text": cfg["prompt"]}]},
        ],
        "stream": True,
        "stream_options": {"include_usage": True},
        "store": False,
    }
    out.update(profiles.payload_overrides(cfg["openrouter_profile"]))
    return out


def metadata(reasoning_tokens=0, finish="stop", **changes):
    base = {
        "id": "gen-1",
        "model": MODEL,
        "provider": "SiliconFlow",
        "system_fingerprint": "fp-1",
    }
    usage = {"prompt_tokens": 20, "completion_tokens": 1}
    if reasoning_tokens is not None:
        usage["completion_tokens_details"] = {"reasoning_tokens": reasoning_tokens}
    last = {**base, "usage": usage, **changes}
    if finish is not None:
        last["finish_reasons"] = [finish]
    return [dict(base), last]


def final(stop="stop", **changes):
    return {
        "role": "assistant",
        "content": [{"type": "text", "text": "7"}],
        "stopReason": stop,
        "responseId": "gen-1",
        **changes,
    }


class ProfileValidationTest(unittest.TestCase):
    def test_valid_profiles_for_each_mode(self):
        profiles.validate_profile(profile(), MODEL, "off")
        profiles.validate_profile(profile(**NON), MODEL, "off")
        profiles.validate_profile(profile(**ON), MODEL, "high")
        profiles.validate_profile(
            profile(reasoning={"enabled": True, "effort": "high"}, mode="on"),
            MODEL,
            "high",
        )
        profiles.validate_profile(
            profile(reasoning={"enabled": True}, mode="required"), MODEL, "high"
        )
        profiles.validate_profile(
            profile(
                **NON,
                provider={
                    "only": ["deepinfra/fp4/flex"],
                    "allow_fallbacks": False,
                    "require_parameters": True,
                },
                expected_provider="DeepInfra",
                stratum="historical_anchor",
                quantization="fp4",
            ),
            MODEL,
            "off",
        )

    def test_invalid_profiles_and_types(self):
        extra = profile()
        extra["seed"] = 1
        missing = profile()
        del missing["top_k"]
        cases = {
            "not dict": [],
            "extra key": extra,
            "missing key": missing,
            "schema": profile(schema="v0"),
            "temperature value": profile(temperature=0.5),
            "temperature string": profile(temperature="0.7"),
            "temperature bool": profile(temperature=True),
            "temperature nan": profile(temperature=float("nan")),
            "top_p": profile(top_p=0.9),
            "top_p inf": profile(top_p=float("inf")),
            "top_k float": profile(top_k=0.0),
            "top_k bool": profile(top_k=False),
            "top_k value": profile(top_k=40),
            "frequency penalty": profile(frequency_penalty=0.1),
            "max tokens": profile(max_tokens=8192),
            "max tokens float": profile(max_tokens=4096.0),
            "mode": profile(mode="auto"),
            "reasoning int": profile(reasoning={"enabled": 0}),
            "reasoning mismatch": profile(reasoning={"enabled": True}),
            "reasoning extra": profile(reasoning={"enabled": False, "effort": "high"}),
            "non reasoning false": profile(
                reasoning={"enabled": False}, mode="non_reasoning"
            ),
            "on low effort": profile(
                reasoning={"enabled": True, "effort": "low"}, mode="on"
            ),
            "fallbacks": profile(
                provider={
                    "only": ["siliconflow/fp8"],
                    "allow_fallbacks": True,
                    "require_parameters": True,
                }
            ),
            "two providers": profile(
                provider={
                    "only": ["siliconflow/fp8", "deepinfra/fp8"],
                    "allow_fallbacks": False,
                    "require_parameters": True,
                }
            ),
            "tag disagrees": profile(
                provider={
                    "only": ["deepinfra/fp8"],
                    "allow_fallbacks": False,
                    "require_parameters": True,
                }
            ),
            "stratum provider": profile(expected_provider="DeepInfra"),
            "stratum quantization": profile(quantization="fp4"),
            "stratum": profile(stratum="other"),
            "model not accepted": profile(accepted_models=["deepseek/other"]),
            "duplicate models": profile(accepted_models=[MODEL, MODEL]),
            "period": profile(release_period="2025-13"),
            "period type": profile(release_period=202508),
        }
        for name, value in cases.items():
            with self.subTest(name), self.assertRaises(ValueError):
                profiles.validate_profile(value, MODEL, "off")

    def test_pi_thinking_must_match_mode(self):
        for p, thinking in (
            (profile(), "high"),
            (profile(**NON), "high"),
            (profile(**ON), "off"),
            (profile(reasoning={"enabled": True}, mode="required"), "medium"),
        ):
            with self.subTest(p["mode"]), self.assertRaises(ValueError):
                profiles.validate_profile(p, MODEL, thinking)

    def test_payload_overrides_are_exact(self):
        self.assertEqual(
            profiles.payload_overrides(profile()),
            {
                "temperature": 0.7,
                "top_p": 1.0,
                "top_k": 0,
                "frequency_penalty": 0,
                "max_tokens": 4096,
                "reasoning": {"enabled": False},
                "provider": {
                    "only": ["siliconflow/fp8"],
                    "allow_fallbacks": False,
                    "require_parameters": True,
                },
            },
        )
        self.assertNotIn("reasoning", profiles.payload_overrides(profile(**NON)))
        p = profile()
        out = profiles.payload_overrides(p)
        out["provider"]["only"].append("mutated")
        self.assertEqual(p["provider"]["only"], ["siliconflow/fp8"])


class AuditTest(unittest.TestCase):
    def run_audit(self, cfg=None, body=None, events=None, message=None):
        cfg = cfg or config()
        return profiles.audit(
            cfg,
            payload(cfg) if body is None else body,
            metadata() if events is None else events,
            final() if message is None else message,
        )

    def test_exact_payload_and_metadata_pass(self):
        result = self.run_audit()
        self.assertEqual(
            result,
            {
                "observed_model": MODEL,
                "upstream_provider": "SiliconFlow",
                "system_fingerprints": ["fp-1"],
                "response_id": "gen-1",
                "reasoning_tokens": 0,
                "truncated": False,
            },
        )

    def test_json_round_trip_numbers_still_match(self):
        cfg = config(p=profile(temperature=1.0))
        body = payload(cfg)
        body.update(temperature=1, top_p=1)
        self.assertFalse(self.run_audit(cfg, body)["truncated"])

    def test_canonical_alias_is_preserved(self):
        alias = "deepseek/deepseek-chat-v3.1-20250821"
        events = [dict(e, model=alias) for e in metadata()]
        self.assertEqual(self.run_audit(events=events)["observed_model"], alias)

    def test_wrong_route_model_prompt_or_mode(self):
        cfg = config()
        good = payload(cfg)

        def changed(**fields):
            return {**copy.deepcopy(good), **fields}

        cases = {
            "requested model": changed(model="deepseek/deepseek-v3.2"),
            "route": changed(
                provider={
                    "only": ["deepinfra/fp8"],
                    "allow_fallbacks": False,
                    "require_parameters": True,
                }
            ),
            "fallback": changed(
                provider={
                    "only": ["siliconflow/fp8"],
                    "allow_fallbacks": True,
                    "require_parameters": True,
                }
            ),
            "user prompt": changed(
                messages=[
                    good["messages"][0],
                    {"role": "user", "content": [{"type": "text", "text": "Hi"}]},
                ]
            ),
            "developer role": changed(
                messages=[
                    {**good["messages"][0], "role": "developer"},
                    good["messages"][1],
                ]
            ),
            "system cwd": changed(
                messages=[
                    {"role": "system", "content": cfg["system_prompt"]},
                    good["messages"][1],
                ]
            ),
            "reasoning on in off mode": changed(reasoning={"enabled": True}),
            "reasoning missing": {k: v for k, v in good.items() if k != "reasoning"},
            "temperature": changed(temperature=1.0),
        }
        for name, body in cases.items():
            with self.subTest(name), self.assertRaises(ValueError):
                self.run_audit(cfg, body)

        non = config(p=profile(**NON))
        with self.assertRaises(ValueError):
            self.run_audit(non, {**payload(non), "reasoning": {"enabled": False}})

    def test_unexpected_decoding_overrides(self):
        cfg = config()
        for key, value in (
            ("seed", 7),
            ("reasoning_effort", "high"),
            ("max_completion_tokens", 4096),
            ("max_output_tokens", 4096),
            ("presence_penalty", 0),
            ("min_p", 0),
            ("repetition_penalty", 1),
            ("tools", [{"type": "function"}]),
            ("tool_choice", "none"),
            ("stream_options", {"include_usage": True, "extra": 1}),
            ("stream", "yes"),
        ):
            with self.subTest(key), self.assertRaises(ValueError):
                self.run_audit(cfg, {**payload(cfg), key: value})

    def test_wrong_observed_identity_or_error(self):
        cases = {
            "unaccepted model": [dict(e, model="deepseek/other") for e in metadata()],
            "two models": [
                metadata()[0],
                dict(metadata()[1], model="deepseek/deepseek-chat-v3.1-20250821"),
            ],
            "provider": [dict(e, provider="DeepInfra") for e in metadata()],
            "two ids": [metadata()[0], dict(metadata()[1], id="gen-2")],
            "no id": [{k: v for k, v in e.items() if k != "id"} for e in metadata()],
            "error": [*metadata(), {"error": {"message": "upstream"}}],
            "empty": [],
        }
        for name, events in cases.items():
            with self.subTest(name), self.assertRaises(ValueError):
                self.run_audit(events=events)
        with self.assertRaises(ValueError):
            self.run_audit(message=final(responseId="gen-other"))

    def test_missing_reasoning_usage_is_unknown_not_zero(self):
        self.assertIsNone(
            self.run_audit(events=metadata(reasoning_tokens=None))["reasoning_tokens"]
        )
        events = metadata()
        del events[1]["usage"]
        self.assertIsNone(self.run_audit(events=events)["reasoning_tokens"])
        self.assertEqual(self.run_audit()["reasoning_tokens"], 0)

    def test_on_mode_zero_reasoning_is_accepted(self):
        cfg = config(p=profile(**ON))
        self.assertEqual(self.run_audit(cfg)["reasoning_tokens"], 0)
        cfg = config(p=profile(reasoning={"enabled": True}, mode="required"))
        self.assertIsNone(
            self.run_audit(cfg, events=metadata(reasoning_tokens=None))[
                "reasoning_tokens"
            ]
        )

    def test_cumulative_usage_is_not_summed(self):
        cfg = config(p=profile(**ON))
        events = metadata(reasoning_tokens=120)
        events[0]["usage"] = {"completion_tokens_details": {"reasoning_tokens": 80}}
        events.append(dict(events[1]))
        self.assertEqual(self.run_audit(cfg, events=events)["reasoning_tokens"], 120)

    def test_off_modes_reject_observed_reasoning(self):
        for p in (profile(), profile(**NON)):
            cfg = config(p=p)
            with self.subTest(p["mode"]):
                with self.assertRaises(ValueError):
                    self.run_audit(cfg, events=metadata(reasoning_tokens=3))
                with self.assertRaises(ValueError):
                    self.run_audit(cfg, events=metadata(has_reasoning=True))
                thinking = final(
                    content=[
                        {"type": "thinking", "thinking": "Let me pick."},
                        {"type": "text", "text": "7"},
                    ]
                )
                with self.assertRaises(ValueError):
                    self.run_audit(cfg, message=thinking)
                self.run_audit(cfg, events=metadata(has_reasoning=False))
        with self.assertRaises(ValueError):
            self.run_audit(events=metadata(reasoning_tokens=-1))

    def test_truncation_is_terminal_flag(self):
        cfg = config(p=profile(**ON))
        for events, message in (
            (metadata(finish="length"), final("length")),
            (metadata(finish="length"), final()),
            (metadata(), final("length")),
        ):
            with self.subTest(message["stopReason"]):
                self.assertTrue(
                    self.run_audit(cfg, events=events, message=message)["truncated"]
                )

    def test_unexpected_or_inconsistent_finish_reasons(self):
        cases = {
            "content filter": (metadata(finish="content_filter"), final()),
            "tool calls": (metadata(finish="tool_calls"), final()),
            "missing": (metadata(finish=None), final()),
            "native tool use": (metadata(), final("toolUse")),
            "invalid has_reasoning": (metadata(has_reasoning=1), final()),
            "conflicting": (
                [*metadata(), dict(metadata(finish="length")[1])],
                final(),
            ),
        }
        for name, (events, message) in cases.items():
            with self.subTest(name), self.assertRaises(ValueError):
                self.run_audit(events=events, message=message)


if __name__ == "__main__":
    unittest.main()
