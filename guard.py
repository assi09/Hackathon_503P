"""Hard wall-clock limits for steps that can block (network, parsing, model calls).

Library timeouts (e.g. requests' read timeout) apply per socket read, so a server
that trickles bytes can stall far longer. Running the step in a daemon thread and
abandoning it at the deadline bounds the real elapsed time.
"""

from __future__ import annotations

import os
import threading
from typing import Any, Callable


class DeadlineExceeded(TimeoutError):
    pass


def run_with_deadline(fn: Callable[..., Any], seconds: float, *args: Any, **kwargs: Any) -> Any:
    box: dict[str, Any] = {}

    def target() -> None:
        try:
            box["value"] = fn(*args, **kwargs)
        except BaseException as exc:  # re-raised in the caller's thread
            box["error"] = exc

    worker = threading.Thread(target=target, daemon=True)
    worker.start()
    worker.join(max(0.0, seconds))
    if worker.is_alive():
        raise DeadlineExceeded(f"step exceeded its {seconds:.1f}s limit")
    if "error" in box:
        raise box["error"]
    return box.get("value")


def start_watchdog(seconds: float, on_expire: Callable[[], None]) -> threading.Timer:
    """Last line of defence: log and exit nonzero before the case time limit."""

    def fire() -> None:
        try:
            on_expire()
        finally:
            os._exit(3)

    timer = threading.Timer(seconds, fire)
    timer.daemon = True
    timer.start()
    return timer
