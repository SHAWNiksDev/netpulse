from __future__ import annotations

import pytest

from netpulse.models import Sample, Status
from netpulse.stats import HostStats


def make(ok: bool, latency_ms: float | None = None) -> Sample:
    latency = latency_ms / 1000 if latency_ms is not None else None
    return Sample(ok=ok, latency=latency)


def test_pending_when_empty() -> None:
    stats = HostStats(10)
    assert stats.status is Status.PENDING
    assert stats.avg_ms is None
    assert stats.loss_pct == 0.0


def test_up_and_averages() -> None:
    stats = HostStats(10)
    for latency in (10.0, 20.0, 30.0):
        stats.add(make(True, latency))
    assert stats.status is Status.UP
    assert stats.avg_ms == pytest.approx(20.0)
    assert stats.min_ms == pytest.approx(10.0)
    assert stats.max_ms == pytest.approx(30.0)


def test_jitter_uses_consecutive_differences() -> None:
    stats = HostStats(10)
    for latency in (10.0, 30.0, 10.0):
        stats.add(make(True, latency))
    # |30-10| + |10-30| averaged => 20 ms
    assert stats.jitter_ms == pytest.approx(20.0)


def test_jitter_none_for_single_sample() -> None:
    stats = HostStats(10)
    stats.add(make(True, 5.0))
    assert stats.jitter_ms is None


def test_down_on_last_failure() -> None:
    stats = HostStats(10)
    stats.add(make(True, 10.0))
    stats.add(make(False))
    assert stats.status is Status.DOWN
    assert stats.loss_pct == pytest.approx(50.0)
    # averages ignore failed probes entirely
    assert stats.avg_ms == pytest.approx(10.0)


def test_degraded_when_avg_exceeds_threshold() -> None:
    stats = HostStats(10, warn_seconds=0.05)
    for _ in range(4):
        stats.add(make(True, 60.0))
    assert stats.status is Status.DEGRADED


def test_up_when_avg_within_threshold() -> None:
    stats = HostStats(10, warn_seconds=0.05)
    stats.add(make(True, 10.0))
    stats.add(make(True, 20.0))
    assert stats.status is Status.UP


def test_window_eviction_keeps_only_recent_samples() -> None:
    stats = HostStats(3)
    for i in range(5):
        stats.add(make(ok=i != 4, latency_ms=1.0))
    assert stats.attempts == 3
    assert stats.loss_pct == pytest.approx(100.0 / 3)
    assert stats.last is not None and not stats.last.ok


def test_sparkline_maps_failures_to_zero() -> None:
    stats = HostStats(10)
    stats.add(make(True, 10.0))
    stats.add(make(False))
    stats.add(make(True, 30.0))
    assert stats.sparkline_data == pytest.approx([10.0, 0.0, 30.0])


def test_sparkline_treats_success_without_latency_as_zero() -> None:
    stats = HostStats(10)
    stats.add(make(True, 10.0))
    stats.add(Sample(ok=True, latency=None))
    stats.add(make(False))
    assert stats.sparkline_data == pytest.approx([10.0, 0.0, 0.0])


def test_uptime_pct() -> None:
    stats = HostStats(10)
    stats.add(make(True, 1.0))
    stats.add(make(True, 1.0))
    stats.add(make(False))
    assert stats.uptime_pct == pytest.approx(200.0 / 3)


def test_clear_resets_everything() -> None:
    stats = HostStats(10)
    stats.add(make(True, 1.0))
    stats.clear()
    assert stats.attempts == 0
    assert stats.status is Status.PENDING


def test_zero_size_window_rejected() -> None:
    with pytest.raises(ValueError):
        HostStats(0)
