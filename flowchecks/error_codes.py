"""Error code text check: every code the backend can return has text in `en` and `vi`.

The texts live in `tripmate-contracts`, in a `translations` folder: one folder per language
(`en`, `vi`), each holding `<area>.json` files, one flat JSON object of strings whose key is
the error code. The area is the part of the code before the first dot. CI checks the contracts
repository out at the commit in `contracts-ref` and runs

    python -m flowchecks.error_codes --translations <checkout>/translations

over the codes the modules register (`scan_modules`) and the platform's own codes. It prints
each code that lacks text and exits 1 when there is any (2 when the folder cannot be read).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path

LANGUAGES = ("en", "vi")


def _texts(translations: Path, language: str, area: str) -> dict[str, object]:
    path = translations / language / f"{area}.json"
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must hold one JSON object")
    return data


def check(translations_dir: str | os.PathLike[str], codes: Iterable[str]) -> list[str]:
    """The codes with no text in `en` or no text in `vi`, each once, sorted."""
    translations = Path(translations_dir)
    cache: dict[tuple[str, str], dict[str, object]] = {}
    missing: set[str] = set()
    for code in set(codes):
        area = code.split(".", 1)[0]
        for language in LANGUAGES:
            if (language, area) not in cache:
                cache[(language, area)] = _texts(translations, language, area)
            if not isinstance(cache[(language, area)].get(code), str):
                missing.add(code)
    return sorted(missing)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--translations", required=True, help="the `translations` folder")
    args = parser.parse_args(argv)

    from app.platform.errors import PLATFORM_ERROR_CODES
    from app.platform.modules import scan_modules

    codes = scan_modules().error_codes | PLATFORM_ERROR_CODES
    try:
        missing = check(args.translations, codes)
    except (OSError, ValueError) as error:
        print(f"Error codes: {error}", file=sys.stderr)
        return 2
    for code in missing:
        print(f"Error codes: {code} has no text in both languages ({', '.join(LANGUAGES)})")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
