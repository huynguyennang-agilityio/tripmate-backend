"""F4 Error format and error codes: cases F4-6 and F4-7 (epic-11 spec, section 2.3).

The error code text check reads a `translations` folder laid out as in tripmate-contracts
(`translations/<lang>/<area>.json`, one flat JSON object of strings per file, the key is the
code) and the set of registered codes. Every folder here is built under `tmp_path`; nothing
uses the network. The interface is in tests/INTERFACE.md, section "F4: Errors".
"""

from __future__ import annotations

import json
from pathlib import Path

from tests.unit.flow.repo import flowcheck

NO_VI = "f4probe.no_vietnamese_text"
NO_VI_FILE = "f4nofile.no_vietnamese_file"
BOTH = "f4probe.has_both_texts"

INIT_SOURCE = f"""\
def register(registry):
    registry.add_error_codes({NO_VI!r}, {NO_VI_FILE!r}, {BOTH!r})
"""


def _write(translations: Path, lang: str, area: str, texts: dict[str, str]) -> None:
    folder = translations / lang
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{area}.json").write_text(json.dumps(texts, ensure_ascii=False), encoding="utf-8")


def test_F4_6_registered_code_without_vietnamese_text_fails_and_is_named(
    tmp_path, set_settings, module_folder
) -> None:
    set_settings()
    module_folder({"__init__.py": INIT_SOURCE})
    from app.platform.modules import scan_modules

    codes = scan_modules().error_codes
    assert {NO_VI, NO_VI_FILE, BOTH} <= set(codes), f"registered: {sorted(codes)}"

    translations = tmp_path / "translations"
    _write(
        translations,
        "en",
        "f4probe",
        {NO_VI: "Has no Vietnamese text", BOTH: "Has both texts"},
    )
    _write(translations, "vi", "f4probe", {BOTH: "Có cả hai bản dịch"})
    _write(translations, "en", "f4nofile", {NO_VI_FILE: "No Vietnamese file"})
    # No translations/vi/f4nofile.json at all.

    missing = flowcheck("error_codes").check(translations, codes)

    assert NO_VI in missing, f"check returned {missing}"
    assert NO_VI_FILE in missing, f"check returned {missing}"
    assert BOTH not in missing, f"check returned {missing}"


def test_F4_7_registered_codes_with_text_in_both_languages_pass(tmp_path) -> None:
    translations = tmp_path / "translations"
    _write(translations, "en", "request", {"request.invalid": "The request is not valid"})
    _write(translations, "vi", "request", {"request.invalid": "Yêu cầu không hợp lệ"})
    _write(
        translations,
        "en",
        "f4probe",
        {BOTH: "Has both texts", "f4probe.not_registered": "Extra text"},
    )
    _write(translations, "vi", "f4probe", {BOTH: "Có cả hai bản dịch"})

    missing = flowcheck("error_codes").check(translations, {"request.invalid", BOTH})

    assert list(missing) == [], f"check returned {missing}"
