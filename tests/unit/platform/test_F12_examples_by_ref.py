"""F12 (own tests of the code agent): `check_examples` reads a response given as a `$ref`."""

from __future__ import annotations

from typing import Any

from app.platform.contract import check_examples


def _contract(response: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "paths": {
            "/v1/places": {
                "get": {"x-stub": True, "responses": {"200": response}},
            }
        },
        "components": {
            "responses": {
                "Place": {
                    "description": "A place",
                    "content": {"application/json": {"schema": schema}},
                }
            }
        },
    }


def test_F12_ref_response_without_example_is_named() -> None:
    contract = _contract({"$ref": "#/components/responses/Place"}, {"type": "object"})

    assert check_examples(contract) == [
        "GET /v1/places: no example on the response schema of 200 application/json"
    ]


def test_F12_ref_response_with_example_passes() -> None:
    contract = _contract(
        {"$ref": "#/components/responses/Place"},
        {"type": "object", "examples": [{"name": "Da Lat"}]},
    )

    assert check_examples(contract) == []
