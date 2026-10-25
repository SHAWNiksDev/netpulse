from __future__ import annotations

import itertools
from collections import deque
from statistics import fmean

from .models import Sample, Status


class HostStats:
    """Rolling window of probe samples for a single host."""

    def __init__(self, size: int, warn_seconds: float | None = None) -> None:
        if size < 1:
            raise ValueError("window size must be >= 1")
        self._window: deque[Sample] = deque(maxlen=size)
        self.warn_seconds = warn_seconds

    def add(self, sample: Sample) -> None:
        self._window.append(sample)

    def clear(self) -> None:
        self._window.clear()

    @property
    def attempts(self) -> int:
        return len(self._window)

    @property
    def loss_pct(self) -> float:
        if not self._window:
            return 0.0
        failed = sum(not s.ok for s in self._window)
        return 100.0 * failed / len(self._window)

    @property
    def ok_latencies_ms(self) -> list[float]:
        return [s.latency * 1000 for s in self._window if s.ok and s.latency is not None]

    @property
    def sparkline_data(self) -> list[float]:
        return [
            s.latency * 1000 if s.ok and s.latency is not None else 0.0
            for s in self._window
        ]

    @property
    def last(self) -> Sample | None:
        return self._window[-1] if self._window else None

    @property
    def avg_ms(self) -> float | None:
        latencies = self.ok_latencies_ms
        return fmean(latencies) if latencies else None

    @property
    def min_ms(self) -> float | None:
        return min(self.ok_latencies_ms, default=None)

    @property
    def max_ms(self) -> float | None:
        return max(self.ok_latencies_ms, default=None)

    @property
    def jitter_ms(self) -> float | None:
        """Mean absolute difference between consecutive successful probes."""
        latencies = self.ok_latencies_ms
        if len(latencies) < 2:
            return None
        gaps = [abs(b - a) for a, b in itertools.pairwise(latencies)]
        return fmean(gaps)

    @property
    def status(self) -> Status:
        if not self._window:
            return Status.PENDING
        if not self._window[-1].ok:
            return Status.DOWN
        avg = self.avg_ms
        if self.warn_seconds is not None and avg is not None and avg > self.warn_seconds * 1000:
            return Status.DEGRADED
        return Status.UP

    @property
    def uptime_pct(self) -> float:
        if not self._window:
            return 0.0
        succeeded = sum(s.ok for s in self._window)
        return 100.0 * succeeded / len(self._window)
