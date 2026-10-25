from __future__ import annotations

from typing import ClassVar

from rich.text import Text
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

from ..config import Host
from ..models import STATUS_STYLE, Sample, Status
from ..stats import HostStats


def fmt_ms(value: float | None) -> str:
    if value is None:
        return "—"
    if value >= 1000:
        return f"{value / 1000:.2f}s"
    return f"{value:.1f}"


def fmt_pct(value: float) -> str:
    return f"{value:.0f}%"


class SummaryBar(Static):
    def set_summary(self, counts: dict[Status, int], total: int, paused: bool) -> None:
        parts = [Text(" netpulse ", style="bold black on green")]
        labels = {
            Status.UP: "up",
            Status.DEGRADED: "degraded",
            Status.DOWN: "down",
            Status.PENDING: "pending",
        }
        styles = {
            Status.UP: "green",
            Status.DEGRADED: "yellow",
            Status.DOWN: "red",
            Status.PENDING: "dim",
        }
        total_hosts = sum(counts.values())
        parts.append(Text(f" {total_hosts} host{'s' if total_hosts != 1 else ''} "))
        for status in (Status.UP, Status.DEGRADED, Status.DOWN, Status.PENDING):
            if counts[status]:
                parts.append(Text(f"{counts[status]} {labels[status]}", style=styles[status]))
        parts.append(Text(f" · {total} probes"))
        if paused:
            parts.append(Text("  ⏸ paused", style="bold yellow"))
        self.update(Text.assemble(*parts))


class DetailPane(Static):
    def __init__(
        self, *, id: str | None = None, classes: str | None = None
    ) -> None:
        super().__init__(id=id, classes=classes)
        self._host: Host | None = None
        self._stats: HostStats | None = None
        self._interval: float | None = None
        self._timeout: float | None = None

    def show_host(
        self,
        host: Host,
        stats: HostStats,
        *,
        interval: float | None,
        timeout: float | None,
    ) -> None:
        self._host = host
        self._stats = stats
        self._interval = interval
        self._timeout = timeout
        self.refresh()

    def clear_host(self) -> None:
        self._host = None
        self._stats = None
        self.refresh()

    def render(self) -> Text:
        if self._host is None or self._stats is None:
            return Text("select a host ↑", style="dim")
        host, stats = self._host, self._stats
        status = stats.status

        header = Text.assemble((host.name, "bold"), "\n", (host.target, "dim italic"))
        interval = self._interval if self._interval is not None else 5.0
        timeout = self._timeout if self._timeout is not None else 3.0
        badge = Text.from_markup(
            f"[{STATUS_STYLE[status]}]{status.value.upper()}[/]"
            f" [dim]· {host.kind}[/]"
            f" [dim]· every {interval:g}s[/]"
            f" [dim]· timeout {timeout:g}s[/]"
        )

        lines: list[Text | str] = [header, Text(), badge, Text()]
        last: Sample | None = stats.last
        if last is None:
            lines.append(Text("no samples yet", style="dim italic"))
        else:
            outcome = (
                fmt_ms(last.latency * 1000)
                if last.ok and last.latency is not None
                else "failed"
            )
            lines.append(
                Text.assemble(
                    (f"{'last probe':<12}", "dim"),
                    (outcome, STATUS_STYLE[status] if last.ok else "red"),
                )
            )
            if last.detail:
                lines.append(Text.assemble((f"{'last reply':<12}", "dim"), last.detail))
        lines.append(Text())

        rows = [
            ("avg", fmt_ms(stats.avg_ms)),
            ("min", fmt_ms(stats.min_ms)),
            ("max", fmt_ms(stats.max_ms)),
            ("jitter", fmt_ms(stats.jitter_ms)),
            ("loss", fmt_pct(stats.loss_pct)),
            ("uptime", fmt_pct(stats.uptime_pct)),
            ("samples", str(stats.attempts)),
        ]
        for label, value in rows:
            lines.append(Text.assemble((f"{label:<12}", "dim"), value))
        return Text("\n").join(lines)


HELP_TEXT = """\
[b]keys[/b]

[cyan]↑/down[/]         select host
[cyan]s[/]              cycle sort order
[cyan]r[/]              re-probe selected host now
[cyan]R[/]              reload hosts.toml
[cyan]space[/]          pause / resume probing
[cyan]?[/]              this help
[cyan]q[/]              quit

[b]statuses[/b]

[green]● up[/]           probe succeeded within thresholds
[yellow]▲ degraded[/]     replies are slower than warn_ms
[red]× down[/]          last probe failed
[dim]○ pending[/]       waiting for the first result

netpulse measures TCP handshake time (SYN → SYN/ACK) and HTTP response
latency. Plain user-space sockets only — no root required.
"""


class HelpScreen(ModalScreen[None]):
    BINDINGS: ClassVar[list] = [
        ("escape", "dismiss", ""),
        ("question_mark", "dismiss", ""),
        ("q", "dismiss", ""),
    ]

    DEFAULT_CSS: ClassVar[str] = """
    HelpScreen {
        align: center middle;
    }
    #help-dialog {
        width: 64;
        height: auto;
        max-height: 90%;
        border: round $primary;
        background: $surface;
        padding: 1 2;
    }
    """

    def compose(self):
        with Vertical(id="help-dialog"):
            yield Static(HELP_TEXT)


__all__ = ["DetailPane", "HelpScreen", "SummaryBar", "fmt_ms", "fmt_pct"]
