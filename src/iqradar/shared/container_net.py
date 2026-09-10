"""Rewrite gateway URLs so Docker Desktop containers can reach the WSL host.

Under Docker Desktop for WSL 2, ``172.17.0.1`` is the Docker VM bridge (not
the WSL host) and loopback is the container itself. The live WSL eth0 address
is what containers can actually dial.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

_CONTAINER_UNREACHABLE_HOSTS = {"172.17.0.1", "localhost", "127.0.0.1", "0.0.0.0"}


def wsl_eth0_ip() -> str | None:
    """Return the WSL host's eth0 IPv4 address, or None when unknown."""
    try:
        output = subprocess.run(
            ["ip", "-4", "-o", "addr", "show", "eth0"],
            capture_output=True, text=True, timeout=5,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"inet\s+(\d+\.\d+\.\d+\.\d+)/", output)
    return match.group(1) if match else None


def _is_docker_desktop() -> bool:
    """True when the Docker daemon is Docker Desktop."""
    try:
        output = subprocess.run(
            ["docker", "info", "--format", "{{.OperatingSystem}}"],
            capture_output=True, text=True, timeout=10,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return "Docker Desktop" in output


def _rewrite_unreachable_host(url: str, host_ip: str) -> str:
    match = re.match(r"(https?://)([^/:]+)(.*)", url)
    if not match:
        return url
    scheme, host, rest = match.groups()
    if host in _CONTAINER_UNREACHABLE_HOSTS:
        return f"{scheme}{host_ip}{rest}"
    return url


def container_reachable_url(url: str) -> str:
    """Rewrite ``url`` to the WSL eth0 IP when the host is unreachable from
    Docker Desktop containers. In-memory only; does not modify files."""
    if not url or not _is_docker_desktop():
        return url
    host_ip = wsl_eth0_ip()
    if not host_ip:
        return url
    return _rewrite_unreachable_host(url, host_ip)


def sync_container_gateway_url(env_file: Path) -> None:
    """Best-effort rewrite of ``OPENAI_BASE_URL`` in ``env_file``.

    Persistence can fail on a read-only mount; ``container_reachable_url``
    still applies at runtime.
    """
    if not env_file.is_file() or not _is_docker_desktop():
        return
    host_ip = wsl_eth0_ip()
    if not host_ip:
        return
    lines = env_file.read_text(encoding="utf-8").splitlines()
    updated: list[str] = []
    changed = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("OPENAI_BASE_URL="):
            value = stripped.split("=", 1)[1]
            new_value = _rewrite_unreachable_host(value, host_ip)
            if new_value != value:
                changed = True
                line = f"OPENAI_BASE_URL={new_value}"
        updated.append(line)
    if changed:
        try:
            env_file.write_text("\n".join(updated) + "\n", encoding="utf-8")
        except OSError:
            return
