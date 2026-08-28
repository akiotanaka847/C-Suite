from __future__ import annotations

import os

import pytest

# Required env vars for Settings() — set here so individual test modules
# don't each have to remember. Real values come from .env in dev/prod.
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-test-not-used")
os.environ.setdefault("EXEC_EMAIL_ADDRESS", "ceo.test@example.com")


@pytest.fixture(autouse=True)
def reset_active_gateway():
    """Ensure the module-level MCP gateway singleton is cleared between tests."""
    from openexecutive.orchestrator.mcp_gateway import set_active_gateway
    set_active_gateway(None)
    yield
    set_active_gateway(None)
