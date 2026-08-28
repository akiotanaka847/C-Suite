from __future__ import annotations

import os

import pytest

# Required env vars for Settings() — set here so individual test modules
# don't each have to remember. Real values come from .env in dev/prod.
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-test-not-used")
os.environ.setdefault("EXEC_EMAIL_ADDRESS", "ceo.test@example.com")

# Aislar la suite del .env del desarrollador. Settings.model_config apunta a
# <repo>/.env, así que en cuanto alguien sigue el Quick Start (`cp .env.example
# .env`) sus valores se cuelan en los tests: ENABLE_WEB_SEARCH=false y las
# variables de Honcho hacían fallar cinco tests que asumen los defaults del
# código. CI no lo detectaba porque allí no existe .env. Desactivarlo aquí hace
# que `make test` dé el mismo resultado en local y en CI.
from csuite.config import Settings  # noqa: E402

Settings.model_config["env_file"] = None


@pytest.fixture(autouse=True)
def reset_active_gateway():
    """Ensure the module-level MCP gateway singleton is cleared between tests."""
    from csuite.orchestrator.mcp_gateway import set_active_gateway
    set_active_gateway(None)
    yield
    set_active_gateway(None)
