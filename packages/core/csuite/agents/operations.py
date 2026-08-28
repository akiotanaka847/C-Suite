from csuite.agents.base import BaseAgent
from csuite.config import get_settings


class OperationsAgent(BaseAgent):
    name = "coo"
    domain = "operations"

    @property
    def model(self) -> str:  # type: ignore[override]
        return get_settings().default_model

    def get_system_prompt(self) -> str:
        from csuite.prompts.domain_prompts import COO_PROMPT

        return COO_PROMPT
