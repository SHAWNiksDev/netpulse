from __future__ import annotations

import asyncio

import pytest

from netpulse.config import Host, Settings
from netpulse.models import Sample
from netpulse.prober import Prober


def make_settings(**overrides) -> Settings:
    defaults = dict(interval=0.1, timeout=1.0, history=20, warn_ms=5_000)
    defaults.update(overrides)
    return Settings(hosts=(), **defaults)


def tcp_host(port: int, **overrides) -> Host:
    fields = dict(name="tcp-target", address="127.0.0.1", port=port, kind="tcp")
    fields.update(overrides)
    return Host(**fields)


def http_host(port: int, **overrides) -> Host:
    fields = dict(
        name="http-target",
        address="127.0.0.1",
        kind="http",
        url=f"http://127.0.0.1:{port}/",
        method="GET",
    )
    fields.update(overrides)
    return Host(**fields)


async def test_tcp_probe_succeeds_against_listener(tcp_listener: int) -> None:
    prober = Prober(make_settings(), lambda host, sample: None)
    try:
        sample = await prober.probe_once(tcp_host(tcp_listener))
        assert sample.ok
        assert sample.latency is not None and sample.latency < 0.5
        assert sample.detail == "connected"
    finally:
        await prober.aclose()


async def test_tcp_probe_reports_refused_connection(closed_port: int) -> None:
    prober = Prober(make_settings(), lambda host, sample: None)
    try:
        sample = await prober.probe_once(tcp_host(closed_port))
        assert not sample.ok
        assert sample.latency is None
        assert sample.detail  # human-readable reason present
    finally:
        await prober.aclose()


async def test_http_probe_sees_status_code(http_ok_port: tuple[int, bytes]) -> None:
    port, _ = http_ok_port
    prober = Prober(make_settings(), lambda host, sample: None)
    try:
        sample = await prober.probe_once(http_host(port))
        assert sample.ok
        assert sample.detail == "200"
        assert sample.latency is not None
    finally:
        await prober.aclose()


async def test_http_probe_derives_url_for_directly_built_host(
    http_ok_port: tuple[int, bytes],
) -> None:
    port, _ = http_ok_port
    prober = Prober(make_settings(), lambda host, sample: None)
    try:
        sample = await prober.probe_once(
            Host(name="bare", address=f"127.0.0.1:{port}", kind="http", method="GET")
        )
        assert not sample.ok  # https against a plain-http test server
        # but the URL itself was derived, not empty
        assert sample.detail and "missing" not in sample.detail
    finally:
        await prober.aclose()


async def test_http_probe_fails_on_unexpected_status(http_ok_port: tuple[int, bytes]) -> None:
    port, _ = http_ok_port
    prober = Prober(make_settings(), lambda host, sample: None)
    try:
        sample = await prober.probe_once(http_host(port, expect_status=418))
        assert not sample.ok
        assert sample.detail == "200"
    finally:
        await prober.aclose()


async def test_http_probe_fails_when_server_is_gone(closed_port: int) -> None:
    prober = Prober(make_settings(), lambda host, sample: None)
    try:
        sample = await prober.probe_once(http_host(closed_port))
        assert not sample.ok
        assert sample.detail
    finally:
        await prober.aclose()


async def test_engine_reports_results_periodically(tcp_listener: int) -> None:
    received: list[Sample] = []
    prober = Prober(
        make_settings(interval=0.05),
        lambda host, sample: received.append(sample),
    )

    async def engine() -> None:
        await prober.run()

    runner = asyncio.get_running_loop().create_task(engine())
    try:
        prober.apply_hosts([tcp_host(tcp_listener)])
        await asyncio.sleep(0.35)
    finally:
        await prober.aclose()
        runner.cancel()
        with pytest.raises(asyncio.CancelledError):
            await runner

    assert len(received) >= 2
    assert all(s.ok for s in received)


async def test_apply_hosts_adds_and_removes_loops(tcp_listener: int) -> None:
    received: list[str] = []
    settings = make_settings(interval=0.05)
    prober = Prober(settings, lambda host, sample: received.append(host.name))

    async def engine() -> None:
        await prober.run()

    runner = asyncio.get_running_loop().create_task(engine())
    try:
        prober.apply_hosts([tcp_host(tcp_listener, name="a")])
        await asyncio.sleep(0.2)
        prober.apply_hosts([tcp_host(tcp_listener, name="b")])
        await asyncio.sleep(0.2)
        count_for_a_before_stop = received.count("a")
        await asyncio.sleep(0.25)
        count_for_a_after_stop = received.count("a")
    finally:
        await prober.aclose()
        runner.cancel()
        with pytest.raises(asyncio.CancelledError):
            await runner

    assert count_for_a_before_stop > 0
    assert count_for_a_after_stop == count_for_a_before_stop  # its loop was removed
    assert received.count("b") > 0


async def test_apply_hosts_respawns_redefined_host(closed_port: int, tcp_listener: int) -> None:
    received: list[Sample] = []
    prober = Prober(make_settings(interval=0.05), lambda host, sample: received.append(sample))
    try:
        prober.apply_hosts([tcp_host(closed_port, name="target")])
        await asyncio.sleep(0.15)
        assert received and not any(s.ok for s in received)

        # same name, new port: the running loop must switch to the new target
        prober.apply_hosts([tcp_host(tcp_listener, name="target")])
        received.clear()
        await asyncio.sleep(0.25)
        assert received and any(s.ok for s in received)
    finally:
        await prober.aclose()


async def test_apply_hosts_refreshes_global_settings() -> None:
    prober = Prober(make_settings(interval=5.0, timeout=1.0), lambda host, sample: None)
    try:
        host = tcp_host(1)
        assert prober.interval_for(host) == 5.0
        assert prober.timeout_for(host) == 1.0
        prober.apply_hosts([host], settings=make_settings(interval=0.5, timeout=4.0))
        assert prober.interval_for(host) == 0.5
        assert prober.timeout_for(host) == 4.0
        # per-host overrides still win over the refreshed globals
        assert prober.timeout_for(tcp_host(1, timeout=9.0)) == 9.0
    finally:
        await prober.aclose()


async def test_tcp_probe_without_port_fails_gracefully() -> None:
    prober = Prober(make_settings(), lambda host, sample: None)
    try:
        sample = await prober.probe_once(Host(name="p", address="127.0.0.1", kind="tcp"))
        assert not sample.ok
        assert "port" in sample.detail
    finally:
        await prober.aclose()


async def test_http_probe_returns_sample_for_unusable_url() -> None:
    prober = Prober(make_settings(), lambda host, sample: None)
    try:
        sample = await prober.probe_once(Host(name="bad", address="", kind="http"))
        assert not sample.ok
        assert sample.detail  # reason present, no exception escaped
    finally:
        await prober.aclose()


async def test_pause_gates_the_schedule_but_not_pokes(tcp_listener: int) -> None:
    received: list[Sample] = []
    prober = Prober(
        make_settings(interval=0.05),
        lambda host, sample: received.append(sample),
    )

    async def engine() -> None:
        await prober.run()

    runner = asyncio.get_running_loop().create_task(engine())
    try:
        prober.apply_hosts([tcp_host(tcp_listener)])
        await asyncio.sleep(0.2)
        prober.pause()
        await asyncio.sleep(0.2)
        frozen_count = len(received)
        await asyncio.sleep(0.3)
        assert len(received) == frozen_count  # schedule fully frozen
        manual = await prober.probe_once(tcp_host(tcp_listener))
        assert manual.ok  # manual probes still work while paused
        prober.resume()
        await asyncio.sleep(0.25)
        assert len(received) > frozen_count
    finally:
        await prober.aclose()
        runner.cancel()
        with pytest.raises(asyncio.CancelledError):
            await runner

    assert prober.paused is False
