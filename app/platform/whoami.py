"""`GET /v1/platform/whoami`: the checkpoint route of the access check (EPIC-11 spec, section 2.2).

Declared `signed_in`; answers with the caller's user ID and session ID, or with the errors of
section 2.3 (`auth.not_signed_in`, `auth.token_expired`) the check returns.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.platform.access import Caller, signed_in

router = APIRouter()


@router.get("/v1/platform/whoami")
async def whoami(caller: Annotated[Caller, Depends(signed_in)]) -> dict[str, str]:
    return {"userId": str(caller.user_id), "sessionId": str(caller.session_id)}
