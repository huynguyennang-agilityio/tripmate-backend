"""F10 settings of the code agent: the three optional settings are checked when given."""

from __future__ import annotations

import pytest

from app.platform.config import SettingsError, load_settings, parse_otlp_headers

BASE = {
    "DATABASE_URL": "postgresql+asyncpg://tripmate:pw@127.0.0.1:5432/tripmate",
    "REDIS_URL": "redis://127.0.0.1:6379/0",
    "RELEASE_VERSION": "1.0.0",
}
SECRET = "f10-secret-value"  # noqa: S105 (a made-up value)


def test_F10_settings_are_optional() -> None:
    settings = load_settings(BASE)
    assert settings.sentry_dsn is None
    assert settings.otel_exporter_otlp_endpoint is None
    assert settings.otel_exporter_otlp_headers is None


def test_F10_settings_valid_values_are_accepted() -> None:
    settings = load_settings(
        {
            **BASE,
            "SENTRY_DSN": "https://abc123@o1.ingest.sentry.io/5",
            "OTEL_EXPORTER_OTLP_ENDPOINT": "https://otlp.example.net/otlp",
            "OTEL_EXPORTER_OTLP_HEADERS": "Authorization=Basic%20abc,x-team=tm",
        }
    )
    assert settings.otel_exporter_otlp_endpoint == "https://otlp.example.net/otlp"
    assert parse_otlp_headers("Authorization=Basic%20abc,x-team=tm") == {
        "Authorization": "Basic abc",
        "x-team": "tm",
    }


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("SENTRY_DSN", SECRET),
        ("OTEL_EXPORTER_OTLP_ENDPOINT", f"ftp://{SECRET}"),
        ("OTEL_EXPORTER_OTLP_ENDPOINT", f"http://host:{SECRET}"),
        ("OTEL_EXPORTER_OTLP_HEADERS", SECRET),
        ("OTEL_EXPORTER_OTLP_HEADERS", f"a=b,{SECRET}"),
    ],
)
def test_F10_settings_malformed_value_names_the_setting_and_not_the_value(
    name: str, value: str
) -> None:
    with pytest.raises(SettingsError) as raised:
        load_settings({**BASE, name: value})
    assert name in str(raised.value)
    assert SECRET not in str(raised.value)
