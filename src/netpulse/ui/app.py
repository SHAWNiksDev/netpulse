from __future__ import annotations

import math
from pathlib import Path
from typing import ClassVar

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.message import Message
from textual.widgets import DataTable, Footer, Header, Label, Sparkline
from textual.widgets.data_table import CellDoesNotExist

from ..config import ConfigError, Host, Settings, load_settings
from ..models import STATUS_DOT, STATUS_STYLE, Sample, Status
from ..prober import Prober
from ..stats import HostStats
from .widgets import DetailPane, HelpScreen, SummaryBar, fmt_ms

_COLUMNS: tuple[tuple[str, str, bool], ...] = (
    # (column key, label, right-aligned?)
    ("dot", "", False),
    ("name", "host", False),
    ("target", "target", False),
    ("last", "last", True),
    ("avg", "avg", True),
    ("min", "min", True),
    ("max", "max", True),
    ("jitter", "jitter", True),
    ("loss", "loss", True),
    ("uptime", "up", True),
)

_SORT_SEVERITY = {
    Status.UP: 0,
    Status.DEGRADED: 1,
    Status.DOWN: 2,
    Status.PENDING: 3,
}


class ProbeUpdate(Message):
    """Posted whenever a probe attempt completes."""

    def __init__(self, host: Host, sample: Sample) -> None:
        self.host = host
        self.sample = sample
        super().__init__()


class NetPulseApp(App[None]):
    TITLE = "netpulse"

    CSS_PATH = "styles.tcss"

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("q,ctrl+q", "quit", "Quit"),
        Binding("space", "toggle_pause", "Pause", priority=True),
        Binding("r", "reprobe", "Re-probe"),
        Binding("R", "reload", "Reload"),
        Binding("s", "cycle_sort", "Sort"),
        Binding("question_mark", "help", "Help"),
    ]

    def __init__(self, settings: Settings, *, config_path: Path) -> None:
        super().__init__()
        self.settings = settings
        self.config_path = config_path
        self.SUB_TITLE = str(config_path)
        self.stats: dict[str, HostStats] = {}
        self.prober = Prober(settings, self._on_result)
        self._sort_modes = ("name", "status", "avg", "loss")
        self._sort_mode = self._sort_modes[0]
        self._selected: str | None = None
        self._probe_count = 0

    # ------------------------------------------------------------------ UI

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield SummaryBar(id="summary")
        with Horizontal(id="main"):
            yield DataTable(id="hosts", cursor_type="row", zebra_stripes=True)
            with Vertical(id="side"):
                yield Label("latency · ms", classes="chart-title")
                yield Sparkline(
                    id="chart", min_color="green", max_color="red", summary_function=max
                )
                yield DetailPane(id="detail")
        yield Footer()

    def on_mount(self) -> None:
        self._init_stats()
        self._rebuild_table()
        self._run_prober()
        self.set_interval(1.0, self._soft_refresh)
        self._refresh_summary()

    def action_quit(self) -> None:
        self.exit()

    async def on_unmount(self) -> None:
        # Runs on every exit path — quit binding, crashes, test teardown — so
        # probe tasks and the HTTP client never outlive the app.
        await self.prober.aclose()

    def action_toggle_pause(self) -> None:
        if self.prober.paused:
            self.prober.resume()
            self.notify("probing resumed", timeout=2)
        else:
            self.prober.pause()
            self.notify("probing paused", timeout=2)
        self._refresh_summary()

    def action_reprobe(self) -> None:
        host = self._selected_host()
        if host is not None:
            self.prober.poke(host)
            self.notify(f"probing {host.name}…", timeout=1)

    def action_reload(self) -> None:
        try:
            fresh = load_settings(self.config_path)
        except ConfigError as exc:
            self.notify(str(exc), severity="error", timeout=8)
            return
        previous_hosts = {host.name: host for host in self.settings.hosts}
        warn_seconds = fresh.warn_ms / 1000
        carried: dict[str, HostStats] = {}
        if fresh.history == self.settings.history:
            for host in fresh.hosts:
                old = self.stats.get(host.name)
                if old is not None and previous_hosts.get(host.name) == host:
                    # Host unchanged: keep its rolling window across the reload.
                    old.warn_seconds = warn_seconds
                    carried[host.name] = old
        self.settings = fresh
        self.stats = {
            host.name: carried.get(host.name) or HostStats(fresh.history, warn_seconds)
            for host in fresh.hosts
        }
        self._probe_count = 0
        self.prober.apply_hosts(fresh.hosts, settings=fresh)
        self._rebuild_table()
        self._refresh_summary()
        self.notify(f"reloaded {len(fresh.hosts)} host(s)", timeout=3)

    def action_cycle_sort(self) -> None:
        index = (self._sort_modes.index(self._sort_mode) + 1) % len(self._sort_modes)
        self._sort_mode = self._sort_modes[index]
        self._rebuild_table()
        self.notify(f"sorted by {self._sort_mode}", timeout=1)

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    @work(exclusive=True)
    async def _run_prober(self) -> None:
        await self.prober.run()

    # ------------------------------------------------------------- probing

    def _on_result(self, host: Host, sample: Sample) -> None:
        # Runs on the loop thread; route through the message pump so that all
        # UI mutations stay inside normal message handling.
        self.post_message(ProbeUpdate(host, sample))

    def on_probe_update(self, event: ProbeUpdate) -> None:
        window = self.stats.get(event.host.name)
        if window is None:  # host was removed between scheduling and delivery
            return
        window.add(event.sample)
        self._probe_count += 1
        self._update_row(event.host)
        if self._selected == event.host.name:
            self._refresh_side(event.host.name)
        self._refresh_summary()

    # ------------------------------------------------------------ plumbing

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        name = event.row_key.value
        if isinstance(name, str):
            self._selected = name
            self._refresh_side(name)

    def _init_stats(self) -> None:
        warn_seconds = self.settings.warn_ms / 1000
        self.stats = {
            host.name: HostStats(self.settings.history, warn_seconds)
            for host in self.settings.hosts
        }

    def _host_by_name(self, name: str | None) -> Host | None:
        if name is None:
            return None
        return next((h for h in self.settings.hosts if h.name == name), None)

    def _selected_host(self) -> Host | None:
        return self._host_by_name(self._selected)

    def _sorted_hosts(self) -> list[Host]:
        mode = self._sort_mode

        def sort_key(host: Host):
            stats = self.stats[host.name]
            if mode == "status":
                return (_SORT_SEVERITY[stats.status], host.name.lower())
            if mode == "avg":
                avg = stats.avg_ms if stats.avg_ms is not None else math.inf
                return (avg, host.name.lower())
            if mode == "loss":
                return (-stats.uptime_pct, host.name.lower())
            return (host.name.lower(),)

        return sorted(self.settings.hosts, key=sort_key)

    def _row_cells(self, host: Host) -> dict[str, Text]:
        stats = self.stats[host.name]
        status_style = STATUS_STYLE[stats.status]

        def right(value: str, style: str | None = None) -> Text:
            return Text(value, style=style or "", justify="right")

        last_sample = stats.last
        if last_sample is None:
            last = right("—", "dim")
        elif last_sample.ok and last_sample.latency is not None:
            last = right(fmt_ms(last_sample.latency * 1000), status_style)
        else:
            last = right("✗", "red bold")

        return {
            "dot": Text(STATUS_DOT[stats.status], style=status_style),
            "name": Text(host.name),
            "target": Text(host.target, style="dim"),
            "last": last,
            "avg": right(fmt_ms(stats.avg_ms), status_style),
            "min": right(fmt_ms(stats.min_ms)),
            "max": right(fmt_ms(stats.max_ms)),
            "jitter": right(fmt_ms(stats.jitter_ms)),
            "loss": right(f"{stats.loss_pct:.0f}%"),
            "uptime": right(f"{stats.uptime_pct:.0f}%"),
        }

    def _table(self) -> DataTable | None:
        # Late probe results can arrive while the app is being torn down.
        try:
            return self.query_one("#hosts", DataTable)
        except NoMatches:
            return None

    def _update_row(self, host: Host) -> None:
        table = self._table()
        if table is None:
            return
        cells = self._row_cells(host)
        try:
            for key, _label, _right in _COLUMNS:
                table.update_cell(host.name, key, cells[key], update_width=True)
        except CellDoesNotExist:
            return  # row not present yet (e.g. probe raced a reload)

    def _rebuild_table(self) -> None:
        table = self._table()
        if table is None:
            return
        previous = self._selected
        table.clear(columns=True)
        for key, label, _right in _COLUMNS:
            table.add_column(label, key=key)
        ordered = self._sorted_hosts()
        for host in ordered:
            cells = self._row_cells(host)
            table.add_row(*(cells[key] for key, _label, _right in _COLUMNS), key=host.name)
        if previous is not None and any(h.name == previous for h in ordered):
            table.move_cursor(row=next(i for i, h in enumerate(ordered) if h.name == previous))
            self._selected = previous
        else:
            self._selected = ordered[0].name if ordered else None
        if self._selected is not None:
            self._refresh_side(self._selected)

    def _refresh_side(self, name: str) -> None:
        host = self._host_by_name(name)
        try:
            detail = self.query_one("#detail", DetailPane)
            chart = self.query_one("#chart", Sparkline)
        except NoMatches:
            return
        if host is None or host.name not in self.stats:
            detail.clear_host()
            chart.data = []
            return
        window = self.stats[host.name]
        detail.show_host(
            host,
            window,
            interval=self.prober.interval_for(host),
            timeout=self.prober.timeout_for(host),
        )
        chart.data = window.sparkline_data

    def _refresh_summary(self) -> None:
        counts = {status: 0 for status in Status}
        for window in self.stats.values():
            counts[window.status] += 1
        try:
            summary = self.query_one("#summary", SummaryBar)
        except NoMatches:
            return
        summary.set_summary(counts, self._probe_count, self.prober.paused)

    def _soft_refresh(self) -> None:
        self._refresh_summary()
        if self._selected is not None:
            self._refresh_side(self._selected)
        self._resort_if_stale()

    def _resort_if_stale(self) -> None:
        # Sorting is advertised as live; re-apply it (cheap: only when the
        # computed order actually drifted from the displayed rows).
        table = self._table()
        if table is None:
            return
        wanted = [host.name for host in self._sorted_hosts()]
        current = [row.key.value for row in table.ordered_rows if isinstance(row.key.value, str)]
        if current != wanted:
            self._rebuild_table()


__all__ = ["NetPulseApp", "ProbeUpdate"]
