"""Dev_Router: /api/v1/dev/* endpoints (development only).

Mounted only when MATCHLAYER_ENVIRONMENT=development (main.py).
"""

from __future__ import annotations

from fastapi import APIRouter

from matchlayer_api.auth.schemas import LastResetLinkResponse
from matchlayer_api.dev.reset_links import DEV_RESET_LINK_STORE

router = APIRouter(prefix="/api/v1/dev", tags=["dev"])


@router.get("/last-reset-link", response_model=LastResetLinkResponse)
async def last_reset_link() -> LastResetLinkResponse:
    record = DEV_RESET_LINK_STORE.latest()
    if record is None:
        return LastResetLinkResponse(link=None, created_at=None)
    return LastResetLinkResponse(link=record.link, created_at=record.created_at)
