from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Status(StrEnum):
    PENDING = "pending"
    UP = "up"
    DEGRADED = "degraded"
    DOWN = "down"


STATUS_STYLE: dict[Status, str] = {
    Status.PENDING: "dim",
    Status.UP: "bold green",
    Status.DEGRADED: "bold yellow",
    Status.DOWN: "bold red",
}

STATUS_DOT = {
    Status.PENDING: "○",
    Status.UP: "●",
    Status.DEGRADED: "▲",
    Status.DOWN: "×",
}


@dataclass(slots=True)
class Sample:
    """Outcome of a single probe attempt."""

    ok: bool
    latency: float | None = None
    detail: str = ""
    at: float = 0.0
