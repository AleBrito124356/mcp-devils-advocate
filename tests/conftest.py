"""Shared pytest setup: make the repo and this folder importable, rewind factories."""

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
for path in (HERE.parent, HERE):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import factories  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_factories():
    factories.reset()
    yield
