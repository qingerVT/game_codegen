"""
Waits for a TCP port to become available.
"""

import asyncio
import socket
import time


def is_port_free(host: str, port: int) -> bool:
    """Returns True if the port is not currently in use."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex((host, port)) != 0


def find_free_port(host: str, start_port: int, count: int = 4) -> int:
    """
    Returns the first free port in [start_port, start_port+count).
    Raises RuntimeError if none are free.
    """
    for port in range(start_port, start_port + count):
        if is_port_free(host, port):
            return port
    raise RuntimeError(
        f"No free port found in range {start_port}-{start_port + count - 1}"
    )


async def wait_for_port_async(host: str, port: int, timeout_s: float = 10.0) -> bool:
    """
    Async version: polls until port accepts connections or timeout.
    Returns True if port became available, False on timeout.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=0.5
            )
            writer.close()
            await writer.wait_closed()
            return True
        except (ConnectionRefusedError, asyncio.TimeoutError, OSError):
            await asyncio.sleep(0.5)
    return False


def wait_for_port(host: str, port: int, timeout_s: float = 10.0) -> bool:
    """
    Sync version: polls until port accepts connections or timeout.
    Returns True if port became available, False on timeout.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except (ConnectionRefusedError, OSError):
            time.sleep(0.5)
    return False
