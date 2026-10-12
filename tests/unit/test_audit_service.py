"""
tests/unit/test_audit_service.py
=================================
Unit tests for audit event metadata sanitisation.
These tests mock the SQLAlchemy session so no DB is needed.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from uitp_api.services.audit_service import _sanitise, record
from uitp_contracts.enums import AuditEventType


@pytest.mark.unit
class TestSanitise:
    def test_clean_metadata_unchanged(self) -> None:
        data = {"project_name": "my-project", "version": 3}
        clean, redacted = _sanitise(data)
        assert clean == data
        assert redacted is False

    def test_password_key_is_stripped(self) -> None:
        data = {"username": "alice", "password": "s3cr3t"}
        clean, redacted = _sanitise(data)
        assert "password" not in (clean or {})
        assert redacted is True

    def test_token_key_is_stripped(self) -> None:
        data = {"email": "a@b.com", "token": "abc123"}
        clean, redacted = _sanitise(data)
        assert "token" not in (clean or {})
        assert redacted is True

    def test_none_returns_none(self) -> None:
        clean, redacted = _sanitise(None)
        assert clean is None
        assert redacted is False

    def test_only_sensitive_keys_stripped(self) -> None:
        data = {"api_key": "key", "safe_field": "value"}
        clean, _ = _sanitise(data)
        assert clean == {"safe_field": "value"}

    def test_authorization_header_stripped(self) -> None:
        data = {"authorization": "Bearer token123", "user_id": "abc"}
        clean, redacted = _sanitise(data)
        assert "authorization" not in (clean or {})
        assert redacted is True


@pytest.mark.unit
class TestRecord:
    @pytest.mark.asyncio
    async def test_event_is_added_to_session(self) -> None:
        mock_session = AsyncMock()
        mock_session.add = MagicMock()

        event = await record(
            mock_session,
            event_type=AuditEventType.PROJECT_CREATED,
            actor_id="user-uuid-1",
            project_id="proj-uuid-1",
            metadata={"project_name": "Test"},
        )

        mock_session.add.assert_called_once_with(event)
        assert event.event_type == "project_created"
        assert event.actor_id == "user-uuid-1"

    @pytest.mark.asyncio
    async def test_sensitive_metadata_is_redacted(self) -> None:
        mock_session = AsyncMock()
        mock_session.add = MagicMock()

        event = await record(
            mock_session,
            event_type=AuditEventType.USER_LOGIN,
            metadata={"email": "a@b.com", "password": "s3cr3t"},
        )

        assert event.metadata_redacted is True
        assert "password" not in (event.metadata_json or {})
