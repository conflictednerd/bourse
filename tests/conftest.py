"""Shared test fixtures.

The isolation below applies to every test module: without it a test would read
and write the real portfolios in ``~/.bourse``.
"""

import pytest


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Point bourse at a temporary directory for the duration of each test."""
    monkeypatch.setenv("BOURSE_HOME", str(tmp_path))
    monkeypatch.delenv("BOURSE_PORTFOLIO", raising=False)
    return tmp_path
