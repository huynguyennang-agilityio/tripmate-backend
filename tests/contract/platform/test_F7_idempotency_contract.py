"""F7 Idempotency keys, level C: what the contract documents (epic-11 spec, section 2.4, last
bullet of "How it is built") and the platform's new error codes.

An operation that declares `Depends(idempotent)` takes the required header `Idempotency-Key` (a
UUID) and documents `400` and `409` as `ErrorResponse`; an operation that does not declare it
documents neither the header nor a `409`. The probe module is that of
`tests/integration/platform/f7_support.py`; the contract is only generated, so no service is used.
The interface is in tests/INTERFACE.md, section "F7: Idempotency keys".
"""

from __future__ import annotations

from typing import Any

from tests.integration.platform.f7_support import write_probe
from tests.support import build_app

ERROR_REF = {"$ref": "#/components/schemas/ErrorResponse"}
DECLARING = ("post", "records"), ("put", "records"), ("post", "mine"), ("post", "text")


def _resolve(contract: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    ref = item.get("$ref")
    if ref is None:
        return item
    node: Any = contract
    for part in ref.removeprefix("#/").split("/"):
        node = node[part]
    return dict(node)


def _key_parameters(contract: dict[str, Any], operation: dict[str, Any]) -> list[dict[str, Any]]:
    parameters = [_resolve(contract, p) for p in operation.get("parameters", [])]
    return [p for p in parameters if str(p.get("name", "")).lower() == "idempotency-key"]


def _contract(set_settings, module_folder) -> tuple[dict[str, Any], Any]:
    set_settings()
    probe = write_probe(module_folder, "f7probe_unused")
    return build_app().openapi(), probe


def test_F7_extra_operation_that_declares_it_documents_the_key_header_and_400_and_409(
    set_settings, module_folder
) -> None:
    contract, probe = _contract(set_settings, module_folder)

    for method, route in DECLARING:
        operation = contract["paths"][probe.path(route)][method]
        what = f"{method.upper()} {probe.path(route)}"
        keys = _key_parameters(contract, operation)
        assert len(keys) == 1, f"{what}: Idempotency-Key parameters {keys}"
        key = keys[0]
        assert key["name"] == "Idempotency-Key", what
        assert key["in"] == "header", what
        assert key["required"] is True, what
        assert key["schema"].get("type") == "string", f"{what}: {key['schema']}"
        assert key["schema"].get("format") == "uuid", f"{what}: {key['schema']}"
        for status in ("400", "409"):
            response = operation["responses"].get(status)
            assert response is not None, f"{what}: no {status}"
            schema = response["content"]["application/json"]["schema"]
            assert schema == ERROR_REF, f"{what} {status}: {schema}"


def test_F7_extra_operation_that_does_not_declare_it_documents_no_key_header_and_no_409(
    set_settings, module_folder
) -> None:
    contract, probe = _contract(set_settings, module_folder)
    operation = contract["paths"][probe.path("plain")]["post"]

    assert _key_parameters(contract, operation) == []
    assert "409" not in operation["responses"]
    # The health check declares nothing either.
    health = contract["paths"]["/v1/health"]["get"]
    assert _key_parameters(contract, health) == []


def test_F7_extra_the_three_idempotency_error_codes_are_platform_error_codes() -> None:
    # The `error-codes` CI job checks the text of every code the platform returns.
    from app.platform.errors import PLATFORM_ERROR_CODES

    for code in (
        "request.idempotency_key_required",
        "request.idempotency_mismatch",
        "request.idempotency_in_progress",
    ):
        assert code in PLATFORM_ERROR_CODES, code
