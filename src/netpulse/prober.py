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
        # Bootstrap only: spawn whatever is not running yet. Later changes go
        # through apply_hosts(), which reconciles (and may cancel) tasks.
        for host in self._settings.hosts:
            if host.name not in self._tasks:
                self._hosts[host.name] = host
                self._spawn(host)
        await asyncio.Event().wait()

    def apply_hosts(self, hosts: Iterable[Host], settings: Settings | None = None) -> None:
        """Reconcile the running probe tasks with ``hosts``.

        New hosts are spawned, vanished hosts are cancelled, and hosts whose
        definition changed are respawned so they probe the updated target and
        schedule. ``settings`` refreshes the global interval/timeout fallbacks.
        """
        if settings is not None:
            self._settings = settings
        wanted = {host.name: host for host in hosts}
        for name, task in list(self._tasks.items()):
            if name not in wanted or self._hosts.get(name) != wanted[name]:
                self._tasks.pop(name)
                task.cancel()
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
        tasks = (*self._tasks.values(), *self._one_shots)
        self._tasks.clear()
        self._one_shots.clear()
        self._hosts.clear()
        for task in tasks:
            task.cancel()
        # Give cancelled probes a chance to finish unwinding before the client
        # they may still be using goes away.
        await asyncio.gather(*tasks, return_exceptions=True)
        await self._client.aclose()

    def _spawn(self, host: Host) -> None:
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
                self._on_result(
                    host, Sample(ok=False, detail=_brief_error(exc), at=perf_counter())
                )
            delay = max(0.05, self.interval_for(host) - (loop.time() - started))
            await asyncio.sleep(delay)

    async def _probe_and_report(self, host: Host) -> None:
        try:
            self._on_result(host, await self.probe_once(host))
        except Exception as exc:
            self._on_result(host, Sample(ok=False, detail=_brief_error(exc), at=perf_counter()))

    def interval_for(self, host: Host) -> float:
        return host.interval or self._settings.interval

    def timeout_for(self, host: Host) -> float:
        return host.timeout or self._settings.timeout

    async def _probe_tcp(self, host: Host) -> Sample:
        if host.port is None:
            return Sample(ok=False, detail="tcp probe needs a port")
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
                # Stream so the clock stops at the response headers, matching
                # what a probe should measure; the body is never downloaded.
                request = self._client.build_request(
                    host.method, host.probe_url, timeout=self.timeout_for(host)
                )
                response = await self._client.send(request, stream=True)
                try:
                    status_code = response.status_code
                finally:
                    await response.aclose()
        except (httpx.HTTPError, httpx.InvalidURL, OSError, TimeoutError) as exc:
            return Sample(ok=False, detail=_brief_error(exc))
        latency = perf_counter() - start
        expected = host.expect_status
        ok = (
            status_code == expected if expected is not None else status_code < 400
        )
        return Sample(ok=ok, latency=latency, detail=str(status_code))
