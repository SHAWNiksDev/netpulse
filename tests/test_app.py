from __future__ import annotations

import asyncio
from pathlib import Path
from time import monotonic

from textual.widgets import DataTable

from netpulse.config import Host, Settings, load_settings
from netpulse.models import Status
from netpulse.ui.app import NetPulseApp


async def wait_for(pilot, predicate, timeout: float = 4.0) -> None:
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        await pilot.pause()
        if predicate():
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"condition not met before {timeout=} timeout")


def build_settings(config_path: Path | None = None) -> tuple[Settings, Path]:
    settings = Settings(
        hosts=(
            Host(name="idle-a", address="127.0.0.1", port=1),
            Host(name="idle-b", address="127.0.0.1", port=1),
        ),
        interval=0.15,
        timeout=0.8,
        history=30,
        warn_ms=5_000,
    )
    return settings, config_path or Path("hosts.toml")


def row_names(table: DataTable) -> list[str]:
    return [str(row.key.value) for row in table.ordered_rows]


async def test_dashboard_reaches_up_and_down_states(tcp_listener: int, closed_port: int) -> None:
    settings = Settings(
        hosts=(
            Host(name="open", address="127.0.0.1", port=tcp_listener),
            Host(name="closed", address="127.0.0.1", port=closed_port),
        ),
        interval=0.15,
        timeout=0.8,
        history=30,
        warn_ms=5_000,
    )
    app = NetPulseApp(settings, config_path=Path("hosts.toml"))
    async with app.run_test(size=(110, 32)) as pilot:
        await wait_for(
            pilot,
            lambda: (
                app.stats["open"].status is Status.UP and app.stats["closed"].status is Status.DOWN
            ),
        )
        table = app.query_one("#hosts", DataTable)
        assert table.row_count == 2
        assert app._probe_count >= 2

        await pilot.press("q")
        await wait_for(pilot, lambda: not app.is_running)


async def test_pause_binding_toggles_prober(tcp_listener: int) -> None:
    settings, path = build_settings()
    app = NetPulseApp(settings, config_path=path)
    async with app.run_test(size=(110, 32)) as pilot:
        assert not app.prober.paused
        await pilot.press("space")
        await pilot.pause()
        assert app.prober.paused
        await pilot.press("space")
        await pilot.pause()
        assert not app.prober.paused


async def test_sort_actually_reorders_rows(tcp_listener: int, closed_port: int) -> None:
    # the down host sorts first by name so each mode implies a distinct order
    settings = Settings(
        hosts=(
            Host(name="adown", address="127.0.0.1", port=closed_port),
            Host(name="zopen", address="127.0.0.1", port=tcp_listener),
        ),
        interval=0.15,
        timeout=0.8,
        history=30,
        warn_ms=5_000,
    )
    app = NetPulseApp(settings, config_path=Path("hosts.toml"))
    async with app.run_test(size=(110, 32)) as pilot:
        table = app.query_one("#hosts", DataTable)
        assert row_names(table) == ["adown", "zopen"]  # default sort: name

        await wait_for(
            pilot,
            lambda: app.stats["adown"].status is Status.DOWN
            and app.stats["zopen"].status is Status.UP,
        )
        await pilot.press("s")  # status: up first, down last
        await pilot.pause()
        assert row_names(table) == ["zopen", "adown"]

        await pilot.press("s")  # avg: measured latency beats "no data"
        await pilot.pause()
        assert row_names(table) == ["zopen", "adown"]

        await pilot.press("s")  # loss: 0% before 100%
        await pilot.pause()
        assert row_names(table) == ["zopen", "adown"]

        await pilot.press("s")  # back to name
        await pilot.pause()
        assert row_names(table) == ["adown", "zopen"]


async def test_table_resorts_live_as_statuses_change(
    tcp_listener: int, closed_port: int, tmp_path: Path
) -> None:
    config_path = tmp_path / "hosts.toml"
    config_path.write_text(
        f'[[host]]\nname = "adown"\naddress = "127.0.0.1"\nport = {tcp_listener}\n'
        f'interval = 0.15\ntimeout = 0.8\n'
        f'[[host]]\nname = "zopen"\naddress = "127.0.0.1"\nport = {tcp_listener}\n'
        f'interval = 0.15\ntimeout = 0.8\n',
        encoding="utf-8",
    )
    settings = load_settings(config_path)
    app = NetPulseApp(settings, config_path=config_path)
    async with app.run_test(size=(110, 32)) as pilot:
        table = app.query_one("#hosts", DataTable)
        await pilot.press("s")  # sort by status
        await pilot.pause()
        await wait_for(pilot, lambda: app.stats["adown"].status is Status.UP)
        assert row_names(table) == ["adown", "zopen"]  # both up: name order

        # flip adown to a closed port via reload
        config_path.write_text(
            f'[[host]]\nname = "adown"\naddress = "127.0.0.1"\nport = {closed_port}\n'
            f'interval = 0.15\ntimeout = 0.8\n'
            f'[[host]]\nname = "zopen"\naddress = "127.0.0.1"\nport = {tcp_listener}\n'
            f'interval = 0.15\ntimeout = 0.8\n',
            encoding="utf-8",
        )
        await pilot.press("R")
        await wait_for(pilot, lambda: app.stats["adown"].status is Status.DOWN)
        # the periodic soft refresh must reorder without any keypress
        await wait_for(pilot, lambda: row_names(table) == ["zopen", "adown"], timeout=5.0)


async def test_help_screen_opens_and_closes() -> None:
    settings, path = build_settings()
    app = NetPulseApp(settings, config_path=path)
    async with app.run_test(size=(110, 32)) as pilot:
        await pilot.press("?")
        await pilot.pause()
        assert len(app.screen_stack) == 2
        await pilot.press("escape")
        await pilot.pause()
        assert len(app.screen_stack) == 1


async def test_reload_picks_up_new_config(tmp_path: Path) -> None:
    config_path = tmp_path / "hosts.toml"
    config_path.write_text(
        '[[host]]\nname = "one"\naddress = "127.0.0.1"\nport = 80\ninterval = 0.15\n',
        encoding="utf-8",
    )
    settings = load_settings(config_path)
    app = NetPulseApp(settings, config_path=config_path)
    async with app.run_test(size=(110, 32)) as pilot:
        assert app.query_one("#hosts", DataTable).row_count == 1

        config_path.write_text(
            '[[host]]\nname = "one"\naddress = "127.0.0.1"\nport = 80\ninterval = 0.15\n'
            '[[host]]\nname = "two"\naddress = "127.0.0.1"\nport = 81\ninterval = 0.15\n',
            encoding="utf-8",
        )
        await pilot.press("R")
        await wait_for(pilot, lambda: app.query_one("#hosts", DataTable).row_count == 2)
        assert {h.name for h in app.settings.hosts} == {"one", "two"}


async def test_reload_respawns_redefined_host(
    tcp_listener: int, closed_port: int, tmp_path: Path
) -> None:
    config_path = tmp_path / "hosts.toml"
    config_path.write_text(
        f'[[host]]\nname = "one"\naddress = "127.0.0.1"\nport = {closed_port}\ninterval = 0.15\n',
        encoding="utf-8",
    )
    settings = load_settings(config_path)
    app = NetPulseApp(settings, config_path=config_path)
    async with app.run_test(size=(110, 32)) as pilot:
        await wait_for(pilot, lambda: app.stats["one"].status is Status.DOWN)

        # same host name, new address: the prober must follow the new config
        config_path.write_text(
            f'[[host]]\nname = "one"\naddress = "127.0.0.1"\nport = {tcp_listener}\n'
            "interval = 0.15\n",
            encoding="utf-8",
        )
        await pilot.press("R")
        await wait_for(pilot, lambda: app.stats["one"].status is Status.UP)


async def test_reload_picks_up_new_global_interval_and_timeout(tmp_path: Path) -> None:
    config_path = tmp_path / "hosts.toml"
    config_path.write_text(
        'interval = 30\ntimeout = 9\n'
        '[[host]]\nname = "one"\naddress = "127.0.0.1"\nport = 80\n',
        encoding="utf-8",
    )
    settings = load_settings(config_path)
    app = NetPulseApp(settings, config_path=config_path)
    async with app.run_test(size=(110, 32)) as pilot:
        host = app.settings.hosts[0]
        assert app.prober.interval_for(host) == 30.0
        assert app.prober.timeout_for(host) == 9.0

        config_path.write_text(
            'interval = 7\ntimeout = 2\n'
            '[[host]]\nname = "one"\naddress = "127.0.0.1"\nport = 80\n',
            encoding="utf-8",
        )
        await pilot.press("R")
        await pilot.pause()
        assert app.prober.interval_for(host) == 7.0
        assert app.prober.timeout_for(host) == 2.0


async def test_reload_keeps_history_of_unchanged_hosts(tcp_listener: int, tmp_path: Path) -> None:
    config_path = tmp_path / "hosts.toml"
    config_path.write_text(
        f'[[host]]\nname = "one"\naddress = "127.0.0.1"\nport = {tcp_listener}\ninterval = 0.15\n',
        encoding="utf-8",
    )
    settings = load_settings(config_path)
    app = NetPulseApp(settings, config_path=config_path)
    async with app.run_test(size=(110, 32)) as pilot:
        await wait_for(pilot, lambda: app.stats["one"].attempts >= 2)
        window = app.stats["one"]
        before = window.attempts

        await pilot.press("R")  # identical config
        await pilot.pause()
        assert app.stats["one"] is window  # same rolling window, not a fresh one
        assert app.stats["one"].attempts >= before


async def test_reload_with_broken_config_keeps_running(tmp_path: Path) -> None:
    config_path = tmp_path / "hosts.toml"
    good = '[[host]]\nname = "one"\naddress = "127.0.0.1"\nport = 80\ninterval = 0.15\n'
    config_path.write_text(good, encoding="utf-8")
    settings = load_settings(config_path)
    app = NetPulseApp(settings, config_path=config_path)
    async with app.run_test(size=(110, 32)) as pilot:
        config_path.write_text('[[host]]\nname = ""\n', encoding="utf-8")
        await pilot.press("R")
        await pilot.pause()
        # still the old settings, app alive and probing
        assert [h.name for h in app.settings.hosts] == ["one"]
        assert app.is_running
