"""The scheduler process: `python -m app.scheduler`.

Checks the settings, scans the modules, and keeps running. Card F9 makes it
wake every minute, run the registered due-work queries, and enqueue what is due.
"""

from __future__ import annotations

import asyncio

from app.platform.config import load_settings_or_exit
from app.platform.modules import scan_modules
from app.platform.process import wait_until_stopped


def main() -> None:
    load_settings_or_exit()
    # The same scan as the API; card F9 makes this process run the registered due-work queries.
    scan_modules()
    asyncio.run(wait_until_stopped())


if __name__ == "__main__":
    main()
