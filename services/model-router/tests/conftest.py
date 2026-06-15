"""Shared fixtures for the router tests: the imported app module and a
FastAPI TestClient over it. No network — handlers are monkeypatched per test.

`main` is imported lazily inside the fixtures (not at collection time) so it
does not pre-empt test_routing.py, which sets tier env vars before its own
import of the module."""

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))


@pytest.fixture
def router():
    """The imported router app module (for monkeypatching its module globals)."""
    import main

    return main


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    import main

    return TestClient(main.app)
