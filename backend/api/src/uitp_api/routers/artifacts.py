"""
uitp_api.routers.artifacts
==========================
API endpoints for listing and retrieving test evidence artifacts (P1-06B).

Endpoints:
  - GET /api/v1/runs/{run_id}/artifacts
      List valid metadata for artifacts linked to an authorized run.
  - GET /api/v1/runs/{run_id}/artifacts/{artifact_id}
      Retrieve metadata and inline base64 content, or direct binary download when ?download=true.
"""
from __future__ import annotations

import base64
import logging

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from uitp_api.database import get_async_session
from uitp_api.middleware.auth import get_current_user
from uitp_api.models.user import User
from uitp_api.services import evidence_service
from uitp_contracts.evidence import ArtifactDownloadResponse, EvidenceArtifactSchema

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/runs/{run_id}/artifacts", tags=["artifacts"])


from datetime import UTC, datetime

# Safe extensions mapping to avoid extension/content-type confusion
SAFE_EXT_MAP = {
    "screenshot": ".png",
    "response_log": ".json",
    "request_log": ".json",
    "trace": ".zip",
    "har": ".har",
    "console_log": ".txt",
}

MAX_INLINE_PREVIEW_BYTES = 5 * 1024 * 1024  # 5MB preview limit


@router.get("", response_model=list[EvidenceArtifactSchema])
async def list_run_artifacts(
    run_id: str,
    include_expired: bool = Query(default=False, description="Include expired artifacts in listing"),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> list[EvidenceArtifactSchema]:
    """List all valid artifacts associated with a run.

    Enforces project membership authorization and masks cross-project runs with 404.
    """
    artifacts = await evidence_service.list_artifacts_for_run(
        session,
        run_id=run_id,
        actor_id=current_user.id,
        include_expired=include_expired,
    )
    now = datetime.now(UTC)
    results: list[EvidenceArtifactSchema] = []
    for a in artifacts:
        is_exp = False
        if a.retention_until is not None:
            ret_until = a.retention_until
            if ret_until.tzinfo is None:
                ret_until = ret_until.replace(tzinfo=UTC)
            is_exp = ret_until < now
        schema = EvidenceArtifactSchema.model_validate(a).model_copy(update={"is_expired": is_exp})
        results.append(schema)
    return results


@router.get("/{artifact_id}", response_model=None)
async def get_run_artifact(
    run_id: str,
    artifact_id: str,
    download: bool = Query(default=False, description="Stream raw binary payload if true"),
    request: Request = None,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> Response | ArtifactDownloadResponse:
    """Retrieve an authorized artifact by ID.

    Returns:
      - Binary Response when download=true or Accept is application/octet-stream.
      - ArtifactDownloadResponse containing safe metadata and base64 inline content otherwise.
    """
    artifact, content = await evidence_service.get_artifact(
        session,
        run_id=run_id,
        artifact_id=artifact_id,
        actor_id=current_user.id,
    )

    accept_header = request.headers.get("accept", "") if request else ""
    wants_raw = download or ("application/octet-stream" in accept_header)

    if wants_raw:
        ext = SAFE_EXT_MAP.get(str(artifact.type), ".bin")
        filename = f"{artifact.id}{ext}"

        media_type = (artifact.content_type or "application/octet-stream").lower()
        # Prevent active content execution: block HTML, SVG, XML from executing in origin
        if media_type in ("text/html", "image/svg+xml", "text/xml", "application/xhtml+xml"):
            media_type = "application/octet-stream"

        return Response(
            content=content,
            media_type=media_type,
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Content-Length": str(len(content)),
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "default-src 'none'; sandbox",
                "X-Frame-Options": "DENY",
                "Cache-Control": "private, no-cache, no-store, must-revalidate",
            },
        )

    # Return structured metadata with base64 encoded inline content if within preview bounds
    inline_b64: str | None = None
    if len(content) <= MAX_INLINE_PREVIEW_BYTES:
        inline_b64 = base64.b64encode(content).decode("ascii")

    now = datetime.now(UTC)
    is_exp = False
    if artifact.retention_until is not None:
        ret_until = artifact.retention_until
        if ret_until.tzinfo is None:
            ret_until = ret_until.replace(tzinfo=UTC)
        is_exp = ret_until < now

    schema = EvidenceArtifactSchema.model_validate(artifact).model_copy(update={"is_expired": is_exp})
    return ArtifactDownloadResponse(
        artifact=schema,
        inline_content=inline_b64,
    )
