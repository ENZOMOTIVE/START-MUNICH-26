"""Sandbox egress guard: this process may only open sockets to loopback and its database.

Installed at process start by the API and the worker when APP_MODE=sandbox, so it also holds
after a restart. It is a second layer; the first is that sandbox adapters use an in-memory
transport and that sandbox config rejects live URLs.
"""

import socket
from urllib.parse import urlsplit

LOOPBACK = frozenset({"localhost", "127.0.0.1", "::1"})
_installed: dict | None = None
blocked: list[str] = []


class SandboxNetworkBlocked(OSError):
    pass


def _refuse(target) -> None:
    blocked.append(str(target))
    raise SandboxNetworkBlocked(f"sandbox blocked outbound network access to {target}")


def install(database_url: str) -> None:
    """Idempotent. Allows loopback, unix sockets and the database host only."""
    global _installed
    if _installed is not None:
        return
    real_connect, real_connect_ex, real_gai = (
        socket.socket.connect,
        socket.socket.connect_ex,
        socket.getaddrinfo,
    )
    names = set(LOOPBACK)
    db_host = urlsplit(database_url.replace("+psycopg", "")).hostname
    if db_host:
        names.add(db_host)
    addresses = set(names)

    def allowed(address) -> bool:
        return not isinstance(address, tuple) or address[0] in addresses

    def getaddrinfo(host, *args, **kwargs):
        if host not in names:
            _refuse(host)
        result = real_gai(host, *args, **kwargs)
        addresses.update(entry[4][0] for entry in result)
        return result

    def connect(self, address):
        if not allowed(address):
            _refuse(address)
        return real_connect(self, address)

    def connect_ex(self, address):
        if not allowed(address):
            _refuse(address)
        return real_connect_ex(self, address)

    socket.getaddrinfo = getaddrinfo
    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
    _installed = {"connect": real_connect, "connect_ex": real_connect_ex, "gai": real_gai}


def uninstall() -> None:
    """For tests only."""
    global _installed
    if _installed is None:
        return
    socket.socket.connect = _installed["connect"]
    socket.socket.connect_ex = _installed["connect_ex"]
    socket.getaddrinfo = _installed["gai"]
    _installed = None
