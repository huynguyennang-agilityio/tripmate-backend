"""What the worker and the scheduler share until card F9 gives them their work."""

from __future__ import annotations

import asyncio
import contextlib
import signal


async def wait_until_stopped() -> None:
    """Return when the process gets SIGTERM or SIGINT."""
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        # Signal handlers are not available on Windows; Ctrl+C still stops the process there.
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)
    await stop.wait()
