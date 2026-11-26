"""Prints every error code the backend can return, one per line, sorted.

`python -m app.platform.error_codes` prints the codes the modules register (`scan_modules`) and
the platform's own (`PLATFORM_ERROR_CODES`). CI gives the list to `flowchecks.error_codes`,
which checks that each code has text in both languages.
"""

from __future__ import annotations

from app.platform.errors import PLATFORM_ERROR_CODES
from app.platform.modules import scan_modules


def all_error_codes() -> list[str]:
    return sorted(scan_modules().error_codes | PLATFORM_ERROR_CODES)


def main() -> None:
    for code in all_error_codes():
        print(code)


if __name__ == "__main__":
    main()
