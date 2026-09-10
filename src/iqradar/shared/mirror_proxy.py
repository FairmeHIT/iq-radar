"""Tiny host-side HTTP proxy that rewrites apt mirrors to a CN mirror.

Why: from this network, upstream package CDNs (deb.debian.org /
archive.ubuntu.com, both Fastly) crawl at ~80 KB/s for large files, which
hangs harbor's in-container agent setup (``apt-get update`` alone exceeds the
360 s default timeout). The same files download from TUNA at ~17 MB/s.

Harbor's setup commands cannot be modified, and per-suite apt ``sources``
mounts cannot cover mixed-suite eval batches. But apt accepts an HTTP proxy
via a drop-in ``/etc/apt/apt.conf.d`` file, and the proxy can rewrite the
upstream host — suite-agnostic and safe for any Debian/Ubuntu base image.

The proxy binds the docker0 gateway (172.17.0.1) so containers on any docker
bridge network can reach it while the LAN cannot. Only known apt mirror hosts
are proxied (anything else gets 403), and upstream fetches always go to the
TUNA mirror over plain HTTP.
"""

from __future__ import annotations

import http.client
import logging
import re
import socket
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

#: Upstream mirror every known apt host is rewritten to.
UPSTREAM_HOST = "mirrors.tuna.tsinghua.edu.cn"
#: apt mirror hosts we rewrite (path stays identical on the upstream).
REWRITE_HOSTS = frozenset(
    {
        "deb.debian.org",
        "security.debian.org",
        "archive.ubuntu.com",
        "security.ubuntu.com",
        "mirrors.tuna.tsinghua.edu.cn",
    }
)
#: Fallback bind when docker0 cannot be detected (docker's usual default).
MIRROR_PROXY_FALLBACK_HOST = "172.17.0.1"
MIRROR_PROXY_PORT = 3142
_UPSTREAM_TIMEOUT_SEC = 60.0
_COPY_CHUNK = 64 * 1024
#: Headers worth forwarding from the upstream response.
_FORWARD_HEADERS = (
    "Content-Type",
    "Content-Length",
    "Last-Modified",
    "ETag",
    "Accept-Ranges",
    "Content-Range",
)

_started = threading.Event()
_gateway_ip: str | None = None


def docker_gateway_ip() -> str | None:
    """Gateway IP of the default docker bridge (cached; ``None`` if unknown).

    Containers on any docker bridge network can reach host-local addresses,
    and the docker0 gateway is the stable, LAN-invisible one to bind.
    """
    global _gateway_ip
    if _gateway_ip is not None:
        return _gateway_ip or None
    try:
        result = subprocess.run(
            ["ip", "-4", "-o", "addr", "show", "dev", "docker0"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        _gateway_ip = ""
        return None
    match = re.search(r"inet (\d+\.\d+\.\d+\.\d+)", result.stdout)
    _gateway_ip = match.group(1) if match else ""
    return _gateway_ip or None


def mirror_proxy_host() -> str:
    """Address the proxy binds/probes on (docker0 gateway or fallback)."""
    return docker_gateway_ip() or MIRROR_PROXY_FALLBACK_HOST


def upstream_target(host: str) -> str | None:
    """Rewritten upstream host for a request Host header (``None``=reject)."""
    normalized = (host or "").split(":", 1)[0].strip().lower()
    if normalized in REWRITE_HOSTS:
        return UPSTREAM_HOST
    return None


class _MirrorProxyHandler(BaseHTTPRequestHandler):
    # HTTP/1.0 (default): responses close the connection, so streaming a body
    # without chunked encoding is always well-formed.
    protocol_version = "HTTP/1.0"

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass  # stay quiet per request; errors surface via status codes

    def do_GET(self) -> None:  # noqa: N802 (http.server naming)
        self._proxy("GET")

    def do_HEAD(self) -> None:  # noqa: N802
        self._proxy("HEAD")

    def _proxy(self, method: str) -> None:
        host = (self.headers.get("Host") or "").split(":", 1)[0].strip().lower()
        # Forward-proxy clients (curl -x, apt Acquire::*::Proxy) send an
        # ABSOLUTE URI as the request target; origin-form is a plain path.
        request_target = self.path or "/"
        if request_target.startswith(("http://", "https://")):
            parts = urlsplit(request_target)
            request_target = parts.path or "/"
            if parts.query:
                request_target += f"?{parts.query}"
            host = host or (parts.hostname or "")
        target = upstream_target(host)
        if target is None:
            self.send_error(403, "host not proxied")
            return
        # TUNA rejects User-Agent-less requests with 403 (anti-bot), so always
        # send an apt-style UA — apt's own UA header is not worth forwarding.
        request_headers = {
            "Host": target,
            "User-Agent": "apt/2.9 Debian; iqradar-mirror-proxy",
            "Accept": "*/*",
        }
        range_header = self.headers.get("Range")
        if range_header:
            request_headers["Range"] = range_header
        try:
            conn = http.client.HTTPConnection(
                target, timeout=_UPSTREAM_TIMEOUT_SEC
            )
            try:
                conn.request(method, request_target, headers=request_headers)
                response = conn.getresponse()
                self.send_response_only(response.status, response.reason)
                for header in _FORWARD_HEADERS:
                    value = response.getheader(header)
                    if value is not None:
                        self.send_header(header, value)
                self.end_headers()
                if method != "HEAD":
                    while True:
                        chunk = response.read(_COPY_CHUNK)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
            finally:
                conn.close()
        except (OSError, http.client.HTTPException) as error:
            logger.warning("mirror proxy upstream error for %s: %s", self.path, error)
            try:
                self.send_error(502, "upstream fetch failed")
            except OSError:
                pass  # client already gone


def mirror_proxy_alive(
    host: str | None = None, port: int = MIRROR_PROXY_PORT, timeout: float = 0.3
) -> bool:
    """True when the mirror proxy is accepting connections on host:port."""
    target = host or mirror_proxy_host()
    try:
        with socket.create_connection((target, port), timeout=timeout):
            return True
    except OSError:
        return False


def start_mirror_proxy(
    host: str | None = None, port: int = MIRROR_PROXY_PORT
) -> bool:
    """Start the mirror proxy on a daemon thread (idempotent, best-effort).

    Binds the docker0 gateway by default so containers on any bridge network
    can reach it while the LAN cannot. Returns True when a listener is (or
    was already) running; failures are logged and swallowed, and evals then
    fall back to the slower direct mirrors.
    """
    bind_host = host or mirror_proxy_host()
    if _started.is_set() and mirror_proxy_alive(bind_host, port):
        return True
    try:
        server = ThreadingHTTPServer((bind_host, port), _MirrorProxyHandler)
    except OSError as error:
        # Gateway missing (docker not up yet) or the port is taken by
        # something else — retry on loopback so at least local probes work.
        logger.warning("mirror proxy bind %s:%s failed: %s", bind_host, port, error)
        try:
            server = ThreadingHTTPServer(("127.0.0.1", port), _MirrorProxyHandler)
        except OSError as fallback_error:
            logger.warning(
                "mirror proxy loopback bind failed too: %s", fallback_error
            )
            return False
    server.daemon_threads = True
    thread = threading.Thread(
        target=server.serve_forever,
        name="iqradar-mirror-proxy",
        daemon=True,
    )
    thread.start()
    _started.set()
    logger.info("mirror proxy listening on %s", server.server_address)
    return True
