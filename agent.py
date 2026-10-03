"""Command-line entry point for the paper explanation generator."""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


@dataclass(frozen=True)
class Case:
    source_url: str
    focus: str
    audience: str


class CaseError(ValueError):
    pass


def load_case(path: Path) -> Case:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CaseError(f"Cannot read input JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise CaseError("Input must be a JSON object")

    values: dict[str, str] = {}
    for name in ("source_url", "focus", "audience"):
        value = data.get(name)
        if not isinstance(value, str) or not value.strip():
            raise CaseError(f"{name} must be a nonempty string")
        values[name] = value.strip()

    parsed = urlparse(values["source_url"])
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise CaseError("source_url must be an HTTP or HTTPS URL")
    return Case(**values)


class Trace:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.started = time.monotonic()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("", encoding="utf-8")

    def record(self, stage: str, action: str, result: str, **details: Any) -> None:
        event = {
            "stage": stage,
            "action": action,
            "result": result,
            "elapsed_seconds": round(time.monotonic() - self.started, 3),
            **details,
        }
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False) + "\n")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--model", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    trace = Trace(args.output / "trace.jsonl")
    try:
        case = load_case(args.input)
        trace.record(
            "input", "validate case", "ok", source_url=case.source_url,
            focus=case.focus, audience=case.audience,
        )
        if not args.model.strip():
            raise CaseError("model must be a nonempty string")
        trace.record("generation", "build explanation", "pending", model=args.model)
        raise NotImplementedError("The OpenRouter generation step is not connected yet")
    except (CaseError, NotImplementedError, OSError) as exc:
        trace.record("run", "finish", "failed", error=str(exc))
        print(f"agent.py: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
