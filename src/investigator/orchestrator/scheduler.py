"""Runs the task DAG: every task whose dependencies have finished starts, up to max_parallel at once."""

import asyncio
from collections.abc import Awaitable, Callable

from investigator.domain.plan import Task
from investigator.domain.state import InvestigationState

TaskRunner = Callable[[Task], Awaitable[None]]
TERMINAL = ("done", "failed", "skipped")


class Scheduler:
    def __init__(self, *, max_parallel: int) -> None:
        if max_parallel < 1:
            raise ValueError("max_parallel must be at least 1")
        self._max_parallel = max_parallel

    @staticmethod
    def dependencies_finished(task: Task, state: InvestigationState) -> bool:
        for dependency in task.spec.depends_on:
            other = state.tasks.get(dependency)
            if other is None or other.status not in TERMINAL:
                return False
        return True

    async def run(self, state: InvestigationState, runner: TaskRunner) -> None:
        running: dict[asyncio.Future[None], str] = {}
        while True:
            ready = [
                task
                for task in state.tasks_with_status("pending")
                if self.dependencies_finished(task, state)
            ]
            for task in ready:
                if len(running) >= self._max_parallel:
                    break
                task.status = "running"
                running[asyncio.ensure_future(runner(task))] = task.task_id
            if not running:
                for task in state.tasks_with_status("pending"):
                    task.status = "skipped"
                    task.error = "dependencies can never complete"
                return
            done, _ = await asyncio.wait(running.keys(), return_when=asyncio.FIRST_COMPLETED)
            for finished in done:
                task = state.tasks[running.pop(finished)]
                error = finished.exception()
                if error is not None:
                    task.status = "failed"
                    task.error = f"runner crashed: {error!r}"
                elif task.status == "running":
                    task.status = "failed"
                    task.error = "runner finished without setting a final status"
