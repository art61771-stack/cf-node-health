from __future__ import annotations

import ssl
import struct
import time
import uuid
from dataclasses import dataclass

import websocket

from .models import CheckResult, Node
from .parser import split_endpoint

PAGES_HOST = "quiet-meadow-pages-qx.pages.dev"
TARGET_HOST = "www.gstatic.com"
TARGET_PORT = 443


@dataclass(slots=True)
class VlessChecker:
    node_uuid: str
    connect_timeout: float = 8.0
    io_timeout: float = 8.0
    total_timeout: float = 20.0
    attempts: int = 2

    def check(self, node: Node) -> CheckResult:
        details: list[str] = []
        for _ in range(self.attempts):
            try:
                self._attempt(node)
                return CheckResult(True, "ok")
            except Exception as exc:  # deliberately summarized; never include endpoint/UUID
                details.append(type(exc).__name__)
        return CheckResult(False, "/".join(details))

    def _attempt(self, node: Node) -> None:
        entry_host, entry_port = split_endpoint(node.endpoint)
        deadline = time.monotonic() + self.total_timeout
        url_host = f"[{entry_host}]" if ":" in entry_host else entry_host
        ws = websocket.create_connection(
            f"wss://{url_host}:{entry_port}{node.path}",
            timeout=self.connect_timeout,
            host=PAGES_HOST,
            origin=f"https://{PAGES_HOST}",
            sslopt={"cert_reqs": ssl.CERT_REQUIRED, "check_hostname": True, "server_hostname": PAGES_HOST},
            http_no_proxy=[entry_host],
            enable_multithread=False,
        )
        try:
            ws.settimeout(min(self.io_timeout, _remaining(deadline)))
            incoming = ssl.MemoryBIO()
            outgoing = ssl.MemoryBIO()
            context = ssl.create_default_context()
            tls = context.wrap_bio(incoming, outgoing, server_side=False, server_hostname=TARGET_HOST)
            try:
                tls.do_handshake()
            except ssl.SSLWantReadError:
                pass
            hello = outgoing.read() if outgoing.pending else b""
            if not hello:
                raise RuntimeError("TLS ClientHello unavailable")
            # Send VLESS header and the first TLS bytes together. This server
            # emits its VLESS response header with downstream data, so waiting
            # after a header-only frame would deadlock.
            ws.send_binary(_vless_request(self.node_uuid) + hello)
            initial = _recv_binary(ws, deadline, self.io_timeout)
            while len(initial) < 2:
                initial += _recv_binary(ws, deadline, self.io_timeout)
            if initial[0] != 0:
                raise RuntimeError("invalid VLESS response")
            addon_len = initial[1]
            while len(initial) < 2 + addon_len:
                initial += _recv_binary(ws, deadline, self.io_timeout)
            tunneled = initial[2 + addon_len :]
            self._tls_http_probe(ws, tunneled, deadline, tls, incoming, outgoing)
        finally:
            ws.close()

    def _tls_http_probe(self, ws: websocket.WebSocket, initial: bytes, deadline: float,
                        tls: ssl.SSLObject, incoming: ssl.MemoryBIO, outgoing: ssl.MemoryBIO) -> None:
        if initial:
            incoming.write(initial)
        _drive_handshake(tls, incoming, outgoing, ws, deadline, self.io_timeout)
        request = (
            f"HEAD /generate_204 HTTP/1.1\r\nHost: {TARGET_HOST}\r\n"
            "Connection: close\r\nUser-Agent: cf-node-health/1\r\n\r\n"
        ).encode("ascii")
        _tls_write_all(tls, request, incoming, outgoing, ws, deadline, self.io_timeout)
        response = bytearray()
        while b"\r\n" not in response and len(response) < 4096:
            try:
                response.extend(tls.read(4096))
            except ssl.SSLWantWriteError:
                _flush(outgoing, ws)
            except ssl.SSLWantReadError:
                _flush(outgoing, ws)
                incoming.write(_recv_binary(ws, deadline, self.io_timeout))
        status = bytes(response).split(b"\r\n", 1)[0]
        if not status.startswith(b"HTTP/") or b" 204 " not in status + b" ":
            raise RuntimeError("unexpected HTTP status")


def _remaining(deadline: float) -> float:
    value = deadline - time.monotonic()
    if value <= 0:
        raise TimeoutError("probe deadline exceeded")
    return value


def _recv_binary(ws: websocket.WebSocket, deadline: float, io_timeout: float) -> bytes:
    ws.settimeout(min(io_timeout, _remaining(deadline)))
    while True:
        opcode, data = ws.recv_data()
        if opcode == websocket.ABNF.OPCODE_BINARY:
            return bytes(data)
        if opcode == websocket.ABNF.OPCODE_CLOSE:
            raise ConnectionError("websocket closed")


def _flush(outgoing: ssl.MemoryBIO, ws: websocket.WebSocket) -> None:
    while outgoing.pending:
        ws.send_binary(outgoing.read())


def _tls_write_all(tls: ssl.SSLObject, data: bytes, incoming: ssl.MemoryBIO, outgoing: ssl.MemoryBIO,
                   ws: websocket.WebSocket, deadline: float, io_timeout: float) -> None:
    offset = 0
    while offset < len(data):
        try:
            offset += tls.write(data[offset:])
            _flush(outgoing, ws)
        except ssl.SSLWantWriteError:
            _flush(outgoing, ws)
        except ssl.SSLWantReadError:
            _flush(outgoing, ws)
            incoming.write(_recv_binary(ws, deadline, io_timeout))


def _drive_handshake(tls: ssl.SSLObject, incoming: ssl.MemoryBIO, outgoing: ssl.MemoryBIO,
                     ws: websocket.WebSocket, deadline: float, io_timeout: float) -> None:
    while True:
        try:
            tls.do_handshake()
            _flush(outgoing, ws)
            return
        except ssl.SSLWantWriteError:
            _flush(outgoing, ws)
        except ssl.SSLWantReadError:
            _flush(outgoing, ws)
            incoming.write(_recv_binary(ws, deadline, io_timeout))


def _vless_request(node_uuid: str) -> bytes:
    user = uuid.UUID(node_uuid).bytes
    host = TARGET_HOST.encode("ascii")
    return b"\x00" + user + b"\x00\x01" + struct.pack("!H", TARGET_PORT) + b"\x02" + bytes([len(host)]) + host
