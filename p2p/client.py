"""Bounded OpenRouter client; never logs prompts, credentials or hidden reasoning."""
import json
import os
import re
import time
import urllib.error
import urllib.request

ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"


class BudgetError(RuntimeError):
    pass


class Client:
    def __init__(self, model, trace, started, deadline_seconds=550):
        self.model, self.trace, self.started = model, trace, started
        self.deadline = started + deadline_seconds
        self.calls = 0
        self.completion_tokens = 0
        self.prompt_tokens = 0
        self.reserved_unknown = 0
        self.usage_verified = True
        self.key = os.environ.get("OPENROUTER_API_KEY", "")
        if not self.key:
            raise ValueError("Set OPENROUTER_API_KEY in the environment; never put it in case.json.")

    def ask(self, stage, system, user, max_tokens=6500):
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        for attempt in range(2):
            remaining = self.deadline - time.monotonic()
            available = 29000 - self.completion_tokens - self.reserved_unknown
            if self.calls >= 9 or remaining < 20 or available < 500:
                raise BudgetError("Generation stopped inside API/time/completion-token limits")
            limit = min(max_tokens, available)
            payload = {"model": self.model, "messages": messages, "max_tokens": limit,
                       "temperature": 0.2, "response_format": {"type": "json_object"},
                       "reasoning": {"enabled": False, "exclude": True}, "usage": {"include": True}}
            request = urllib.request.Request(ENDPOINT, data=json.dumps(payload).encode(),
                headers={"Authorization": "Bearer " + self.key, "Content-Type": "application/json",
                         "X-Title": "Paper to Playground"})
            self.calls += 1
            call_start = time.monotonic()
            self.trace(stage, "api_request", {"call": self.calls, "model": self.model,
                "max_completion_tokens": limit, "attempt": attempt + 1})
            try:
                with urllib.request.urlopen(request, timeout=min(180, remaining - 12)) as response:
                    data = json.load(response)
            except urllib.error.HTTPError as exc:
                # Do not record arbitrary upstream bodies, which may contain secrets.
                self.trace(stage, "api_failure", {"call": self.calls, "http_status": exc.code,
                    "prompt_tokens": None, "completion_tokens": None,
                    "call_seconds": round(time.monotonic() - call_start, 3)})
                self.usage_verified = False
                self.reserved_unknown += limit
                if exc.code in (429, 500, 502, 503, 504) and attempt == 0:
                    time.sleep(min(2, max(0, self.deadline - time.monotonic() - 20)))
                    continue
                raise RuntimeError(f"OpenRouter returned HTTP {exc.code}; see trace.jsonl") from None
            except (OSError, ValueError) as exc:
                self.reserved_unknown += limit
                self.usage_verified = False
                self.trace(stage, "api_failure", {"call": self.calls, "error_type": type(exc).__name__,
                    "prompt_tokens": None, "completion_tokens": None,
                    "call_seconds": round(time.monotonic() - call_start, 3)})
                raise RuntimeError("OpenRouter connection/response failed; see trace.jsonl") from None
            usage = data.get("usage") or {}
            pt, ct = usage.get("prompt_tokens"), usage.get("completion_tokens")
            verified = isinstance(pt, int) and isinstance(ct, int) and pt >= 0 and ct >= 0
            if verified:
                self.prompt_tokens += pt
                self.completion_tokens += ct  # Includes reasoning; never add details again.
            else:
                self.usage_verified = False
                self.reserved_unknown += limit
            choice = (data.get("choices") or [{}])[0]
            self.trace(stage, "api_response", {"call": self.calls, "response_id": data.get("id"),
                "requested_model": self.model, "returned_model": data.get("model"),
                "prompt_tokens": pt, "completion_tokens": ct,
                "total_tokens": usage.get("total_tokens"),
                "completion_tokens_details": usage.get("completion_tokens_details"),
                "prompt_tokens_details": usage.get("prompt_tokens_details"),
                "usage_verified": verified, "finish_reason": choice.get("finish_reason"),
                "call_seconds": round(time.monotonic() - call_start, 3)})
            content = choice.get("message", {}).get("content")
            try:
                if choice.get("finish_reason") == "length":
                    raise ValueError("Model output reached its token limit; incomplete response")
                if not isinstance(content, str) or not content.strip():
                    raise ValueError("Model returned no usable content")
                return parse_object(content)
            except ValueError as exc:
                self.trace(stage, "response_validation_failure", {"call": self.calls,
                    "error_type": type(exc).__name__, "will_retry": attempt == 0})
                if attempt == 0:
                    messages.append({"role": "user", "content": "The previous attempt did not produce a complete valid JSON object. Return a compact, complete JSON response now. Keep prose concise and include executable code."})
                    continue
                raise
        raise RuntimeError("API attempts exhausted")


def parse_object(text):
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        # Allow a short preamble, but no silently repaired/fabricated JSON values.
        start = text.find("{")
        if start < 0:
            raise ValueError("Model did not return JSON") from None
        obj, _ = json.JSONDecoder().raw_decode(text[start:])
    if not isinstance(obj, dict):
        raise ValueError("Model response must be a JSON object")
    return obj
