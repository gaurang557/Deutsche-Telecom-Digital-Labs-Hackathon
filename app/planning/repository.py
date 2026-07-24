import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from app.config import DB_PATH
from app.schemas import (
    ActionPlan,
    ActionResult,
    ExecutionStatus,
    PlanExecutionResponse,
    TaskDetail,
    TaskEvent,
    TaskRequest,
    TaskSummary,
)


class PlanRepository:
    """Durable plan, lifecycle, and execution-result storage."""

    def __init__(self, db_path: Path = DB_PATH) -> None:
        self._db_path = Path(db_path)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._db_path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS voice_desk_tasks (
                    plan_id TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL,
                    request_text TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    plan_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    results_json TEXT NOT NULL DEFAULT '[]',
                    execution_claimed INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS voice_desk_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    plan_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    message TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )

    def save(self, plan: ActionPlan, request: TaskRequest | None = None) -> None:
        now = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO voice_desk_tasks
                (plan_id, request_id, request_text, summary, plan_json, status,
                 results_json, execution_claimed, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, '[]', 0, ?, ?)
                """,
                (
                    str(plan.plan_id),
                    str(plan.request_id),
                    request.text if request else plan.summary,
                    plan.summary,
                    plan.model_dump_json(),
                    ExecutionStatus.PLANNED,
                    now,
                    now,
                ),
            )
        self.log_event(plan.plan_id, "plan_created", "Plan prepared and ready for review")

    def get(self, plan_id: UUID) -> ActionPlan | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT plan_json FROM voice_desk_tasks WHERE plan_id = ?",
                (str(plan_id),),
            ).fetchone()
        return ActionPlan.model_validate_json(row["plan_json"]) if row else None

    def claim_execution(self, plan_id: UUID) -> bool:
        """Ensure a plan cannot be executed twice by repeated UI submissions."""
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE voice_desk_tasks
                SET execution_claimed = 1, status = ?, updated_at = ?
                WHERE plan_id = ? AND execution_claimed = 0
                """,
                (
                    ExecutionStatus.RUNNING,
                    datetime.now(UTC).isoformat(),
                    str(plan_id),
                ),
            )
            claimed = cursor.rowcount == 1
        if claimed:
            self.log_event(plan_id, "execution_started", "Execution started")
        return claimed

    def status(self, plan_id: UUID) -> ExecutionStatus | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT status FROM voice_desk_tasks WHERE plan_id = ?",
                (str(plan_id),),
            ).fetchone()
        return ExecutionStatus(row["status"]) if row else None

    def control(self, plan_id: UUID, intent: str) -> ExecutionStatus | None:
        current = self.status(plan_id)
        if current is None:
            return None
        transitions = {
            ("pause", ExecutionStatus.RUNNING): ExecutionStatus.PAUSED,
            ("resume", ExecutionStatus.PAUSED): ExecutionStatus.RUNNING,
        }
        if intent == "cancel" and current in {
            ExecutionStatus.PLANNED,
            ExecutionStatus.RUNNING,
            ExecutionStatus.PAUSED,
        }:
            target = ExecutionStatus.CANCELLED
        else:
            target = transitions.get((intent, current))
        if target is None:
            raise ValueError(f"Cannot {intent} a task that is {current}")
        self._set_status(plan_id, target)
        self.log_event(
            plan_id,
            f"task_{target}",
            {
                ExecutionStatus.PAUSED: "Task paused by the user",
                ExecutionStatus.RUNNING: "Task resumed by the user",
                ExecutionStatus.CANCELLED: "Task cancelled by the user",
            }[target],
        )
        return target

    def complete(self, response: PlanExecutionResponse) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE voice_desk_tasks
                SET status = ?, results_json = ?, updated_at = ?
                WHERE plan_id = ?
                """,
                (
                    response.status,
                    json.dumps([result.model_dump(mode="json") for result in response.results]),
                    datetime.now(UTC).isoformat(),
                    str(response.plan_id),
                ),
            )
        self.log_event(
            response.plan_id,
            f"execution_{response.status}",
            f"Execution finished with status: {response.status}",
        )

    def _set_status(self, plan_id: UUID, value: ExecutionStatus) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE voice_desk_tasks SET status = ?, updated_at = ? WHERE plan_id = ?",
                (value, datetime.now(UTC).isoformat(), str(plan_id)),
            )

    def list(self, limit: int = 20) -> list[TaskSummary]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT plan_id, request_id, request_text, summary, status,
                       created_at, updated_at
                FROM voice_desk_tasks ORDER BY updated_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [TaskSummary.model_validate(dict(row)) for row in rows]

    def detail(self, plan_id: UUID) -> TaskDetail | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM voice_desk_tasks WHERE plan_id = ?",
                (str(plan_id),),
            ).fetchone()
        if not row:
            return None
        with self._connect() as connection:
            event_rows = connection.execute(
                """
                SELECT event_type, message, created_at FROM voice_desk_events
                WHERE plan_id = ? ORDER BY id ASC
                """,
                (str(plan_id),),
            ).fetchall()
        return TaskDetail(
            plan_id=row["plan_id"],
            request_id=row["request_id"],
            request_text=row["request_text"],
            summary=row["summary"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            plan=ActionPlan.model_validate_json(row["plan_json"]),
            results=[
                ActionResult.model_validate(item)
                for item in json.loads(row["results_json"])
            ],
            events=[TaskEvent.model_validate(dict(item)) for item in event_rows],
        )

    def log_event(self, plan_id: UUID, event_type: str, message: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO voice_desk_events
                (plan_id, event_type, message, created_at) VALUES (?, ?, ?, ?)
                """,
                (
                    str(plan_id),
                    event_type,
                    message,
                    datetime.now(UTC).isoformat(),
                ),
            )
