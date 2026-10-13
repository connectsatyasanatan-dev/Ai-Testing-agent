"""Web application fixtures for browser adapter tests."""
from tests.fixtures.web_app.app import (
    create_web_test_app,
    EphemeralWebServer,
    run_test_web_server,
)

__all__ = ["create_web_test_app", "EphemeralWebServer", "run_test_web_server"]
