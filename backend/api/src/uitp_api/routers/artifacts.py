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


@router.get("", response_model=list[EvidenceArtifactSchema])
async def list_run_artifacts(
    run_id: str,
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
    )
    return [EvidenceArtifactSchema.model_validate(a) for a in artifacts]


@router.get("/{artifact_id}")
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
        media_type = artifact.content_type or "application/octet-stream"
        filename = f"{artifact.id}.bin"
        return Response(
            content=content,
            media_type=media_type,
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Content-Length": str(len(content)),
            },
        )

    # Return structured metadata with base64 encoded inline content
    inline_b64 = base64.b64encode(content).decode("ascii")
    schema = EvidenceArtifactSchema.model_validate(artifact)
    return ArtifactDownloadResponse(
        artifact=schema,
        inline_content=inline_b64,
    )
