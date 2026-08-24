from __future__ import annotations

import asyncio
import contextlib
import socket
from collections.abc import AsyncIterator

import pytest


@pytest.fixture
async def tcp_listener() -> AsyncIterator[int]:
    """A TCP server that accepts connections on an ephemeral port."""

    async def accept(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.close()

    server = await asyncio.start_server(accept, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    yield port
    server.close()
    await server.wait_closed()


@pytest.fixture
async def closed_port() -> AsyncIterator[int]:
    """A port that is bound but never listened on, so connects get refused."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    yield sock.getsockname()[1]
    sock.close()


HTTP_OK_RESPONSE = b"HTTP/1.1 200 OK\r\ncontent-length: 2\r\nconnection: close\r\n\r\nok"


@pytest.fixture
async def http_ok_port() -> AsyncIterator[tuple[int, bytes]]:
    """A raw HTTP responder; yields the port and the response it sends."""
    response = HTTP_OK_RESPONSE

    async def serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=2)
        writer.write(response)
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    yield port, response
    server.close()
    await server.wait_closed()
