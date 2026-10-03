"""Minimal OpenRouter chat client with a hard per-case budget."""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import requests

from guard import DeadlineExceeded, run_with_deadline

API_URL = "https://openrouter.ai/api/v1/chat/completions"


class BudgetError(RuntimeError):
    pass


class LLMError(RuntimeError):
    pass


@dataclass
class Budget:
    """Limits from the brief (10 min, 10 requests, 30k completion tokens) with safety margins."""

    max_requests: int = 6
    max_completion_tokens: int = 28_000
    deadline_seconds: float = 540.0
    started: float = field(default_factory=time.monotonic)
    requests: int = 0
    completion_tokens: int = 0
    prompt_tokens: int = 0

    def elapsed(self) -> float:
        return time.monotonic() - self.started

    def remaining_seconds(self) -> float:
        return self.deadline_seconds - self.elapsed()

    def remaining_completion(self) -> int:
        return self.max_completion_tokens - self.completion_tokens


class OpenRouter:
    def __init__(self, model: str, budget: Budget, record: Callable[..., None], api_key: str | None = None) -> None:
        self.model = model
        self.budget = budget
        self.record = record
        self.api_key = api_key or os.environ.get("OPENROUTER_API_KEY", "")
        if not self.api_key:
            raise LLMError("OPENROUTER_API_KEY is not set")

    def chat(self, stage: str, messages: list[dict], max_tokens: int, reasoning_effort: str | None = "off",
             json_mode: bool = True) -> str:
        """One request (plus at most one retry on transient errors). Every attempt counts."""
        attempts = 0
        while True:
            attempts += 1
            b = self.budget
            if b.requests >= b.max_requests:
                raise BudgetError("request budget exhausted")
            cap = min(max_tokens, b.remaining_completion())
            if cap < 1500:
                raise BudgetError("completion-token budget exhausted")
            timeout = b.remaining_seconds() - 15
            if timeout < 20:
                raise BudgetError("time budget exhausted")
            body: dict[str, Any] = {
                "model": self.model,
                "messages": messages,
                "max_tokens": cap,
                "temperature": 0.2,
                "seed": 7,
                "usage": {"include": True},
                "provider": {"sort": "throughput"},
            }
            if json_mode:
                body["response_format"] = {"type": "json_object"}
            effort = os.environ.get("EXPLAINER_REASONING", reasoning_effort or "")
            if effort == "off":
                body["reasoning"] = {"enabled": False, "exclude": True}
            elif effort:
                body["reasoning"] = {"effort": effort, "exclude": True}
            b.requests += 1
            t0 = time.monotonic()
            status, data, err = None, None, None
            try:
                limit = min(timeout, 300)
                resp = run_with_deadline(
                    requests.post, limit, API_URL,
                    headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json",
                             "X-Title": "paper-explainer-agent"},
                    json=body, timeout=(10, limit),
                )
                status = resp.status_code
                data = resp.json() if resp.content else None
            except (requests.RequestException, ValueError, DeadlineExceeded) as exc:
                err = f"{type(exc).__name__}: {exc}"
            seconds = round(time.monotonic() - t0, 2)
            usage = (data or {}).get("usage") or {}
            pt, ct = int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)
            b.prompt_tokens += pt
            b.completion_tokens += ct
            choice = ((data or {}).get("choices") or [{}])[0]
            content = ((choice.get("message") or {}).get("content")) or ""
            if data and data.get("error"):
                err = str(data["error"].get("message", data["error"]))[:300]
            elif status and status >= 400:
                err = f"HTTP {status}"
            self.record(
                stage, "llm_call", "ok" if not err and content else "error",
                request_index=b.requests, attempt=attempts, model=self.model,
                served_model=(data or {}).get("model"),
                generation_id=(data or {}).get("id"), provider=(data or {}).get("provider"),
                http_status=status, call_seconds=seconds, max_tokens=cap,
                prompt_tokens=pt, completion_tokens=ct,
                reasoning_tokens=((usage.get("completion_tokens_details") or {}).get("reasoning_tokens")),
                cached_tokens=((usage.get("prompt_tokens_details") or {}).get("cached_tokens")),
                total_tokens=pt + ct, cost=usage.get("cost"),
                finish_reason=choice.get("finish_reason"), error=err,
                totals={"requests": b.requests, "prompt_tokens": b.prompt_tokens,
                        "completion_tokens": b.completion_tokens},
            )
            if not err and content:
                return content
            transient = status is None or status in (408, 429, 500, 502, 503, 504) or not content
            if attempts >= 2 or not transient:
                raise LLMError(err or "empty completion")
            time.sleep(2)


def parse_json(text: str) -> dict:
    """Parse a JSON object from a completion, tolerating code fences or stray prose."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise
        obj = json.loads(text[start:end + 1])
    if not isinstance(obj, dict):
        raise ValueError("completion is not a JSON object")
    return obj
