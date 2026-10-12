"""
tests/security/conftest.py
==========================
Fixtures for security regression tests.
Reuses the database and client fixtures from integration tests.
"""
from __future__ import annotations

import os

# Import fixtures from integration conftest
from tests.integration.conftest import (
    client,
    db_session,
    test_engine,
)

__all__ = ["client", "db_session", "test_engine"]
