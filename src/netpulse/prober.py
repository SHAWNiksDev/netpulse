from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable, Iterable
from time import perf_counter

import httpx

from . import __version__
from .config import Host, Settings
from .models import Sample


def _brief_error(exc: BaseException) -> str:
    text = str(exc).strip().splitlines()
    message = text[0] if text else ""
    return (message[:80] or type(exc).__name__).strip()


class Prober:
    """Periodically probes every host and reports results through a callback.

    All probes run as tasks on the current event loop; ``on_result`` is always
    invoked from the loop thread, so it may touch the UI directly.
    """

    def __init__(self, settings: Settings, on_result: Callable[[Host, Sample], None]) -> None:
        self._settings = settings
        self._on_result = on_result
        self._client = httpx.AsyncClient(
            headers={"user-agent": f"netpulse/{__version__}"},
            follow_redirects=True,
        )
        self._hosts: dict[str, Host] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._one_shots: set[asyncio.Task[None]] = set()
        self._running = asyncio.Event()
        self._running.set()

    @property
    def paused(self) -> bool:
        return not self._running.is_set()

    def pause(self) -> None:
        self._running.clear()

    def resume(self) -> None:
        self._running.set()

    async def run(self) -> None:
        for host in self._settings.hosts:
            self._spawn(host)
        await asyncio.Event().wait()

    def apply_hosts(self, hosts: Iterable[Host]) -> None:
        wanted = {host.name: host for host in hosts}
        for name in list(self._tasks):
            if name not in wanted:
                self._tasks.pop(name).cancel()
        for name, host in wanted.items():
            if name not in self._tasks:
                self._spawn(host)
        self._hosts = wanted

    def poke(self, host: Host) -> None:
        """Run an immediate one-off probe outside of the regular schedule."""
        task = asyncio.get_running_loop().create_task(self._probe_and_report(host))
        self._one_shots.add(task)
        task.add_done_callback(self._one_shots.discard)

    async def probe_once(self, host: Host) -> Sample:
        stamp = perf_counter()
        sample = await self._probe_tcp(host) if host.kind == "tcp" else await self._probe_http(host)
        sample.at = stamp
        return sample

    async def aclose(self) -> None:
        for task in (*self._tasks.values(), *self._one_shots):
            task.cancel()
        self._tasks.clear()
        self._one_shots.clear()
        await self._client.aclose()

    def _spawn(self, host: Host) -> None:
        self._hosts[host.name] = host
        self._tasks[host.name] = asyncio.get_running_loop().create_task(
            self._probe_loop(host), name=f"netpulse:{host.name}"
        )

    async def _probe_loop(self, host: Host) -> None:
        loop = asyncio.get_running_loop()
        while True:
            await self._running.wait()
            started = loop.time()
            try:
                self._on_result(host, await self.probe_once(host))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._on_result(host, Sample(ok=False, detail=_brief_error(exc)))
            delay = max(0.05, self.interval_for(host) - (loop.time() - started))
            await asyncio.sleep(delay)

    async def _probe_and_report(self, host: Host) -> None:
        try:
            self._on_result(host, await self.probe_once(host))
        except Exception as exc:
            self._on_result(host, Sample(ok=False, detail=_brief_error(exc)))

    def interval_for(self, host: Host) -> float:
        return host.interval or self._settings.interval

    def timeout_for(self, host: Host) -> float:
        return host.timeout or self._settings.timeout

    async def _probe_tcp(self, host: Host) -> Sample:
        assert host.port is not None
        start = perf_counter()
        writer: asyncio.StreamWriter | None = None
        try:
            async with asyncio.timeout(self.timeout_for(host)):
                _, writer = await asyncio.open_connection(host.address, host.port)
        except (OSError, TimeoutError) as exc:
            return Sample(ok=False, detail=_brief_error(exc))
        latency = perf_counter() - start
        if writer is not None:
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()
        return Sample(ok=True, latency=latency, detail="connected")

    async def _probe_http(self, host: Host) -> Sample:
        start = perf_counter()
        try:
            async with asyncio.timeout(self.timeout_for(host)):
                response = await self._client.request(host.method, host.probe_url)
        except (httpx.HTTPError, OSError, TimeoutError) as exc:
            return Sample(ok=False, detail=_brief_error(exc))
        latency = perf_counter() - start
        expected = host.expect_status
        ok = (
            response.status_code == expected if expected is not None else response.status_code < 400
        )
        return Sample(ok=ok, latency=latency, detail=str(response.status_code))
