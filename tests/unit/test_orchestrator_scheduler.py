import asyncio
from datetime import UTC, datetime

from investigator.domain.incident import IncidentReport
from investigator.domain.plan import Task, TaskSpec
from investigator.domain.state import InvestigationState
from investigator.orchestrator.scheduler import Scheduler


def _state(*tasks: tuple[str, list[str]]) -> InvestigationState:
    incident = IncidentReport(
        incident_id="X",
        title="t",
        severity="SEV3",
        reported_at=datetime(2026, 1, 1, tzinfo=UTC),
        reporter="r",
        summary="s",
    )
    state = InvestigationState(run_id="r", incident=incident, services=[])
    for task_id, deps in tasks:
        spec = TaskSpec(task_id=task_id, agent="observability", objective=task_id, depends_on=deps)
        state.tasks[task_id] = Task(task_id=task_id, round=1, origin="plan", spec=spec)
    return state


class Recorder:
    def __init__(
        self,
        fail: set[str] | None = None,
        crash: set[str] | None = None,
        silent: set[str] | None = None,
    ) -> None:
        self.started: list[str] = []
        self.finished: list[str] = []
        self.active = 0
        self.max_active = 0
        self._fail = fail or set()
        self._crash = crash or set()
        self._silent = silent or set()

    async def __call__(self, task: Task) -> None:
        self.started.append(task.task_id)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0.02)
        self.active -= 1
        self.finished.append(task.task_id)
        if task.task_id in self._crash:
            raise RuntimeError("boom")
        if task.task_id not in self._silent:
            task.status = "failed" if task.task_id in self._fail else "done"


async def test_parallel_up_to_limit() -> None:
    state = _state(("A", []), ("B", []), ("C", []))
    recorder = Recorder()
    await Scheduler(max_parallel=2).run(state, recorder)
    assert recorder.max_active == 2
    assert all(task.status == "done" for task in state.tasks.values())


async def test_dependencies_order_execution() -> None:
    state = _state(("A", []), ("B", ["A"]), ("C", ["B"]))
    recorder = Recorder()
    await Scheduler(max_parallel=4).run(state, recorder)
    assert recorder.started == ["A", "B", "C"]
    assert recorder.finished.index("A") < recorder.started.index("B")


async def test_failed_dependency_still_runs_dependent() -> None:
    state = _state(("A", []), ("B", ["A"]))
    await Scheduler(max_parallel=2).run(state, Recorder(fail={"A"}))
    assert state.tasks["A"].status == "failed" and state.tasks["B"].status == "done"


async def test_unknown_dependency_and_cycle_are_skipped() -> None:
    state = _state(("A", ["ghost"]), ("B", ["C"]), ("C", ["B"]), ("D", []))
    await Scheduler(max_parallel=2).run(state, Recorder())
    assert [state.tasks[t].status for t in "ABCD"] == ["skipped", "skipped", "skipped", "done"]
    assert state.tasks["A"].error == "dependencies can never complete"


async def test_runner_crash_and_missing_status_mark_failed() -> None:
    state = _state(("A", []), ("B", []))
    await Scheduler(max_parallel=2).run(state, Recorder(crash={"A"}, silent={"B"}))
    assert state.tasks["A"].status == "failed" and "boom" in (state.tasks["A"].error or "")
    assert state.tasks["B"].status == "failed"
