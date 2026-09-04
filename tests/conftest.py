"""Explicit test-process initialization shared by backend tests."""

from core.utils.dsl import initialize_dsl_catalog


def pytest_sessionstart() -> None:
    """Initialize the immutable DSL catalog before request models are built."""
    initialize_dsl_catalog()
