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
    raise AssertionError("condition not met before timeout")


def build_settings(config_path: Path | None = None) -> tuple[Settings, Path]:
    settings = Settings(
        hosts=(
            Host(name="open", address="127.0.0.1", port=1),
            Host(name="closed", address="127.0.0.1", port=1),
        ),
        interval=0.15,
        timeout=0.8,
        history=30,
        warn_ms=5_000,
    )
    return settings, config_path or Path("hosts.toml")


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
    assert not app.is_running


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


async def test_sort_cycles_without_losing_rows() -> None:
    settings, path = build_settings()
    app = NetPulseApp(settings, config_path=path)
    async with app.run_test(size=(110, 32)) as pilot:
        for mode in ("status", "avg", "loss", "name"):
            await pilot.press("s")
            await pilot.pause()
            assert app._sort_mode == mode
        assert app.query_one("#hosts", DataTable).row_count == len(settings.hosts)


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
