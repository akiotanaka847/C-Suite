"""Background scheduler that fires due `scheduled_actions` rows through the Executive."""
from csuite.scheduler.runner import run_scheduler

__all__ = ["run_scheduler"]
