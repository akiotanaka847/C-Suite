"""Workflows: deterministic orchestrations of specialists + skills + RAG that
produce a structured executive artifact (board prep deck, quarterly plan,
performance review, etc.).

Workflows differ from chat:
- Inputs are structured (a typed form), not free-form
- Steps are explicit and ordered (the user sees progress)
- The output is a rendered artifact (Markdown), not a conversation turn

Each concrete workflow is a `Workflow` subclass registered in
`WORKFLOW_REGISTRY` below. The HTTP layer is in
`csuite.api.routes.workflows`.
"""
from __future__ import annotations

from csuite.workflows.annual_plan import AnnualPlanWorkflow
from csuite.workflows.base import (
    Workflow,
    WorkflowEvent,
    WorkflowMeta,
    WorkflowSection,
    WorkflowStepDef,
)
from csuite.workflows.board_prep import BoardPrepWorkflow
from csuite.workflows.candidate_outreach import CandidateOutreachWorkflow
from csuite.workflows.candidate_screen import CandidateScreenWorkflow
from csuite.workflows.churn_deep_dive import ChurnDeepDiveWorkflow
from csuite.workflows.comp_refresh import CompRefreshWorkflow
from csuite.workflows.competitive_teardown import CompetitiveTeardownWorkflow
from csuite.workflows.crisis_comms import CrisisCommsWorkflow
from csuite.workflows.department_check_in import DepartmentCheckInWorkflow
from csuite.workflows.end_of_day_digest import EndOfDayDigestWorkflow
from csuite.workflows.engagement_value_report import (
    EngagementValueReportWorkflow,
)
from csuite.workflows.exec_search_brief import ExecSearchBriefWorkflow
from csuite.workflows.executive_reflection import ExecutiveReflectionWorkflow
from csuite.workflows.executive_research import ExecutiveResearchWorkflow
from csuite.workflows.fundraising_prep import FundraisingPrepWorkflow
from csuite.workflows.gtm_launch import GTMLaunchWorkflow
from csuite.workflows.interview_coordination import InterviewCoordinationWorkflow
from csuite.workflows.investor_update import InvestorUpdateWorkflow
from csuite.workflows.ma_evaluation import MAEvaluationWorkflow
from csuite.workflows.mbr import MBRWorkflow
from csuite.workflows.morning_brief import MorningBriefWorkflow
from csuite.workflows.new_hire_onboarding import NewHireOnboardingWorkflow
from csuite.workflows.offer_approval import OfferApprovalWorkflow
from csuite.workflows.org_design import OrgDesignWorkflow
from csuite.workflows.performance_review import PerformanceReviewWorkflow
from csuite.workflows.pricing_review import PricingReviewWorkflow
from csuite.workflows.product_strategy import ProductStrategyWorkflow
from csuite.workflows.quarterly_plan import QuarterlyPlanWorkflow
from csuite.workflows.reference_check import ReferenceCheckWorkflow
from csuite.workflows.risk_register import RiskRegisterWorkflow
from csuite.workflows.role_onboarding import RoleOnboardingWorkflow

WORKFLOW_REGISTRY: dict[str, Workflow] = {
    "annual_plan": AnnualPlanWorkflow(),
    "department_check_in": DepartmentCheckInWorkflow(),
    "board_prep": BoardPrepWorkflow(),
    "candidate_outreach": CandidateOutreachWorkflow(),
    "candidate_screen": CandidateScreenWorkflow(),
    "churn_deep_dive": ChurnDeepDiveWorkflow(),
    "comp_refresh": CompRefreshWorkflow(),
    "competitive_teardown": CompetitiveTeardownWorkflow(),
    "crisis_comms": CrisisCommsWorkflow(),
    # `morning_brief` and `end_of_day_digest` are the first workflows in
    # the registry that target the principal directly (not user-invoked
    # report artifacts). The scheduler fires them on a recurring schedule
    # and dispatches the artifact via DM.
    "morning_brief": MorningBriefWorkflow(),
    "end_of_day_digest": EndOfDayDigestWorkflow(),
    # `executive_reflection` is the first workflow C-Suite runs ON ITSELF —
    # not targeting a department or the principal but its own org
    # coordination decisions. Fires ~30 minutes before the morning
    # brief, can invoke real tools (DMs, broadcasts, follow-ups).
    "executive_reflection": ExecutiveReflectionWorkflow(),
    "exec_search_brief": ExecSearchBriefWorkflow(),
    "fundraising_prep": FundraisingPrepWorkflow(),
    "gtm_launch": GTMLaunchWorkflow(),
    "interview_coordination": InterviewCoordinationWorkflow(),
    "engagement_value_report": EngagementValueReportWorkflow(),
    "investor_update": InvestorUpdateWorkflow(),
    "ma_evaluation": MAEvaluationWorkflow(),
    "mbr": MBRWorkflow(),
    # `offer_approval` is the first BUILT-IN workflow to pause on a
    # WaitForHumanEvent gate (previously only dynamic workflows did): it
    # drafts the offer package, DMs the HIRING_SIGNOFF approver, and pauses.
    # Paused runs never auto-resume — the pending_approval → extended
    # transition is the explicit `extend_offer` tool/route, which consults
    # the recorded resolution. `new_hire_onboarding` is the placed → Person
    # handoff (roster record + 30/60/90 plan + milestone reminders).
    "offer_approval": OfferApprovalWorkflow(),
    "new_hire_onboarding": NewHireOnboardingWorkflow(),
    "org_design": OrgDesignWorkflow(),
    "performance_review": PerformanceReviewWorkflow(),
    "pricing_review": PricingReviewWorkflow(),
    "product_strategy": ProductStrategyWorkflow(),
    "quarterly_plan": QuarterlyPlanWorkflow(),
    "reference_check": ReferenceCheckWorkflow(),
    "risk_register": RiskRegisterWorkflow(),
    "role_onboarding": RoleOnboardingWorkflow(),
    # `executive_research` drives the 7-specialist council in research
    # mode with web_search, then runs the Executive's tool-use loop
    # (same shape as `executive_reflection`) so findings get routed
    # via the existing outbound toolkit: DMs to heads of departments,
    # DMs to the principal, briefing alerts, watchlist additions,
    # follow-ups, workflow suggestions. Fired manually via the
    # `run_executive_research` chat tool, at end of onboarding, and on
    # a 2h cron gated by a state-hash skip-if-unchanged check.
    "executive_research": ExecutiveResearchWorkflow(),
}


def get_workflow(name: str) -> Workflow:
    """Resolve a workflow by name.

    Built-ins win on a name collision (checked first). User-created
    ("dynamic") definitions are resolved lazily from the dynamic store and
    wrapped in the generic ``DynamicWorkflow`` engine. Imports are deferred
    to avoid a workflows -> orchestrator -> workflows import cycle.
    """
    workflow = WORKFLOW_REGISTRY.get(name)
    if workflow is not None:
        return workflow

    from csuite.workflows.dynamic import DynamicWorkflow
    from csuite.workflows.dynamic_store import get_definition

    defn = get_definition(name)
    if defn is not None and defn.is_active:
        return DynamicWorkflow(defn)
    raise KeyError(f"Unknown workflow: {name}")


def list_workflows() -> list[Workflow]:
    """All runnable workflows: built-ins followed by active dynamic ones."""
    builtins = list(WORKFLOW_REGISTRY.values())

    from csuite.workflows.dynamic import DynamicWorkflow
    from csuite.workflows.dynamic_store import list_definitions

    dynamics = [DynamicWorkflow(d) for d in list_definitions(active_only=True)]
    return builtins + dynamics


__all__ = [
    "Workflow",
    "WorkflowEvent",
    "WorkflowMeta",
    "WorkflowSection",
    "WorkflowStepDef",
    "WORKFLOW_REGISTRY",
    "get_workflow",
    "list_workflows",
]
