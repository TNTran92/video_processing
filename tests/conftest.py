"""Shared fixtures for the test suite (spec §7)."""

import pytest
from pathlib import Path

from config import AppConfig
# from config import ServerConfig, PathsConfig, ...  (import per need)

# pseudocode — the concrete shapes land with the implementation tasks
# (see the implementation plan; these fixtures are the shared contract tests
# rely on).


@pytest.fixture
def cfg() -> AppConfig:
    """A valid full AppConfig with test-lean values:
    base_url="http://localhost:11434", prefixes under tmp_path,
    max_frames=10, fps=1, ttl=3, retries=1, budget=1s.
    """
    # pseudocode: build directly from the Pydantic models (not from a file)
    # so tests don't depend on a YAML file on disk.
    ...


@pytest.fixture
def video_dir(tmp_path: Path) -> Path:
    """A directory standing in for the host prefix; contains
    fixtures/test_video.mp4 copied in."""
    ...


@pytest.fixture
def fake_llm():
    """Factory for a scripted LLMClient stand-in (see test_pipeline.py).
    Records every chat() call; returns canned strings per script index.
    """
    # pseudocode
    ...
