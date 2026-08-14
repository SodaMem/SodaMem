"""Force api.deepseek.com → known A record when system DNS is broken.

Enable with env ``SODAMEM_FORCE_DEEPSEEK_DNS=1``.
Optional override: ``SODAMEM_DEEPSEEK_IP=x.x.x.x``.
"""
from __future__ import annotations

import os
import socket

if os.environ.get("SODAMEM_FORCE_DEEPSEEK_DNS", "").strip().lower() in {
    "1",
    "true",
    "yes",
}:
    _IP = os.environ.get("SODAMEM_DEEPSEEK_IP", "119.188.220.215").strip() or "119.188.220.215"
    _HOSTS = {"api.deepseek.com"}
    _orig = socket.getaddrinfo

    def _patched(host, port, family=0, type=0, proto=0, flags=0):
        if isinstance(host, str) and host.lower().rstrip(".") in _HOSTS:
            return [
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    proto or socket.IPPROTO_TCP,
                    "",
                    (_IP, int(port) if port not in (None, "") else 443),
                )
            ]
        return _orig(host, port, family, type, proto, flags)

    socket.getaddrinfo = _patched  # type: ignore[assignment]
