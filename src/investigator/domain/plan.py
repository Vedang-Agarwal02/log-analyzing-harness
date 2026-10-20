"""Planner output (task specs) and the coordinator's runtime task records."""

from typing import Literal

from pydantic import BaseModel, Field

from investigator.domain.common import UtcDatetime

TaskStatus = Literal["pending", "running", "done", "failed", "skipped"]
TaskOrigin = Literal["plan", "followup", "fallback"]


class TaskSpec(BaseModel):
    task_id: str = Field(description="Unique id within this plan, e.g. 'T1'.")
    agent: str = Field(description="Specialist name from the specialist catalog.")
    objective: str = Field(description="Precise objective for the specialist.")
    services: list[str] = Field(default_factory=list, description="Exact catalog names in scope.")
    window_start: UtcDatetime | None = None
    window_end: UtcDatetime | None = None
    depends_on: list[str] = Field(
        default_factory=list,
        description="Task ids whose results this task needs. Keep empty if independent.",
    )


class PlanDraft(BaseModel):
    action: Literal["investigate", "conclude"]
    rationale: str
    tasks: list[TaskSpec] = Field(default_factory=list)


class Task(BaseModel):
    task_id: str
    round: int
    origin: TaskOrigin
    spec: TaskSpec
    status: TaskStatus = "pending"
    attempts: int = 0
    error: str | None = None
