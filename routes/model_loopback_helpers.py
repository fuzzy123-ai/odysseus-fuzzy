# routes/model_loopback_helpers.py
import os
import socket
from urllib.parse import urlparse, urlunparse
from typing import Callable


# Loopback hosts a user might type for a local model server (LM Studio,
# llama.cpp, vLLM, ...). Inside Docker these point at the container, not the
# host the server actually runs on.
_ANY_BIND_HOSTS = {"0.0.0.0", "::"}
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", *_ANY_BIND_HOSTS}


def _docker_host_gateway_reachable() -> bool:
    """True when host.docker.internal is reachable from this container."""
    in_container = os.path.exists("/.dockerenv")
    if not in_container:
        try:
            with open("/proc/1/cgroup", encoding="utf-8") as fh:
                in_container = any(
                    token in fh.read() for token in ("docker", "containerd", "kubepods")
                )
        except OSError:
            in_container = False
    if not in_container:
        return False
    try:
        socket.getaddrinfo("host.docker.internal", None)
        return True
    except OSError:
        return False


def _container_loopback_reachable(base_url: str, timeout: float = 0.2) -> bool:
    """True when the requested loopback host:port is reachable in-container."""
    try:
        parsed = urlparse(base_url)
    except Exception:
        return False
    host = (parsed.hostname or "").lower()
    port = parsed.port
    if host not in _LOOPBACK_HOSTS or not port:
        return False
    probe_host = "::1" if host == "::1" else "127.0.0.1"
    family = socket.AF_INET6 if probe_host == "::1" else socket.AF_INET
    try:
        with socket.socket(family, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect((probe_host, port))
        return True
    except OSError:
        return False


def rewrite_loopback_for_docker(
    base_url: str,
    *,
    container_local: bool = False,
    docker_host_gateway_reachable: Callable[[], bool] = _docker_host_gateway_reachable,
    container_loopback_reachable: Callable[[str], bool] = _container_loopback_reachable,
) -> str:
    """Rewrite a loopback model-endpoint URL to host.docker.internal when needed."""
    try:
        parsed = urlparse(base_url)
    except Exception:
        return base_url
    host = (parsed.hostname or "").lower()
    if host not in _LOOPBACK_HOSTS:
        return base_url
    if container_local:
        if host in _ANY_BIND_HOSTS:
            netloc = "127.0.0.1" + (f":{parsed.port}" if parsed.port else "")
            return urlunparse(parsed._replace(netloc=netloc))
        return base_url
    if host in _ANY_BIND_HOSTS and not docker_host_gateway_reachable():
        netloc = "127.0.0.1" + (f":{parsed.port}" if parsed.port else "")
        return urlunparse(parsed._replace(netloc=netloc))
    if container_loopback_reachable(base_url):
        return base_url
    if not docker_host_gateway_reachable():
        return base_url
    netloc = "host.docker.internal" + (f":{parsed.port}" if parsed.port else "")
    return urlunparse(parsed._replace(netloc=netloc))
