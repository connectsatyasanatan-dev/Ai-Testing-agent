"""
Root conftest.py — shared pytest fixtures and configuration.

Fixtures that are expensive to set up (database connections, running
servers) should be placed in conftest.py files in the relevant
subdirectory (integration/, e2e/) so they do not slow down unit tests.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
src_dirs = [
    ROOT / "backend" / "api" / "src",
    ROOT / "backend" / "packages" / "contracts" / "src",
    ROOT / "backend" / "packages" / "evidence" / "src",
    ROOT / "backend" / "packages" / "policy" / "src",
    ROOT / "backend" / "packages" / "adapters" / "base" / "src",
    ROOT / "backend" / "packages" / "adapters" / "http_api" / "src",
    ROOT / "backend" / "packages" / "orchestrator" / "src",
]
for p in src_dirs:
    if p.exists() and str(p) not in sys.path:
        sys.path.insert(0, str(p))

try:
    import pytest
except ImportError:
    class _MockMarker:
        def __getattr__(self, name: str):
            return lambda *args, **kwargs: (lambda fn: fn) if args and callable(args[0]) else (lambda fn: fn)

    class _MockPytest:
        mark = _MockMarker()

        @staticmethod
        def fixture(*args, **kwargs):
            return lambda fn: fn

        @staticmethod
        def raises(exc):
            class _RaisesContext:
                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc_val, tb):
                    if exc_type is None:
                        raise AssertionError(f"Expected exception {exc.__name__} was not raised")
                    return issubclass(exc_type, exc)

            return _RaisesContext()

    sys.modules["pytest"] = _MockPytest()
