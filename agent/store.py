"""SQLite-backed state store and append-only audit log.

Two tables:
    tasks         -- one row per request_id, upserted as the task progresses.
    audit_events  -- append-only. Rows are only ever inserted, never
                     updated or deleted -- this module exposes no
                     function that could do either.

Call connect(db_path) once at startup before using anything else here.
"""

import hashlib
import hmac
import json
import sqlite3
import sys
import threading
from datetime import datetime

from pydantic import TypeAdapter

from agent.models import AuditEvent, HistoryEntry, Plan, TaskState
from agent.redaction import redact_sensitive_data

_HISTORY_ADAPTER = TypeAdapter(list[HistoryEntry])

_connection: sqlite3.Connection | None = None

# Guards every read and write below, WITHIN this process. sqlite3, when
# opened with check_same_thread=False, will let more than one thread use
# the same connection object -- but it does NOT make that safe on its
# own. This lock is what actually serialises access across the voice
# thread and the executor thread; check_same_thread=False just turns off
# sqlite3's refusal to try.
#
# It does NOT help across processes -- the FastAPI process and the agent
# loop process each have their own lock in their own memory, so from
# SQLite's point of view they're just two independent writers. That's
# what the WAL + busy_timeout pragmas below are for: WAL lets readers
# and a writer proceed concurrently instead of blocking on each other,
# and busy_timeout makes a writer that shows up mid-write retry for a
# while instead of failing instantly with "database is locked".
_lock = threading.Lock()


def connect(db_path: str) -> None:
    """Open (or create) the database at db_path and its tables.

    Safe to call again with a different path -- e.g. tests call this
    with a fresh temp file or ":memory:" so each test gets an isolated
    database instead of sharing one on disk.
    """
    global _connection
    with _lock:
        if _connection is not None:
            _connection.close()
        _connection = sqlite3.connect(db_path, check_same_thread=False)
        # WAL: readers don't block the writer and the writer doesn't
        # block readers -- needed now that the FastAPI process and the
        # agent loop process both hold open connections to this file.
        # (No-op on ":memory:" databases -- there's no file to write a
        # WAL alongside, so sqlite keeps them in "memory" mode regardless.)
        _connection.execute("PRAGMA journal_mode=WAL")
        # If a write still can't get the lock immediately (WAL allows
        # concurrent reads, but only one writer at a time), retry for up
        # to 5s instead of raising "database is locked" right away.
        _connection.execute("PRAGMA busy_timeout=5000")
        _connection.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                request_id                 TEXT PRIMARY KEY,
                status                     TEXT NOT NULL,
                current_step               INTEGER NOT NULL,
                plan_json                  TEXT NOT NULL,
                history_json                TEXT NOT NULL,
                pending_confirmation_hash   TEXT,
                updated_at                  TEXT NOT NULL
            )
            """
        )
        _connection.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_events (
                timestamp     TEXT NOT NULL,
                request_id    TEXT NOT NULL,
                action_id     TEXT,
                event_type    TEXT NOT NULL,
                rule_id       TEXT,
                details_json  TEXT NOT NULL
            )
            """
        )
        _connection.commit()


def _require_connection() -> sqlite3.Connection:
    if _connection is None:
        raise RuntimeError("agent.store.connect(db_path) must be called before using the store")
    return _connection


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def save_state(state: TaskState) -> None:
    """Insert the task's state, or overwrite it if request_id already exists.

    state.pending_confirmation (the live confirmation token) is never
    written to disk -- it's an authorisation credential, and a state
    table you can read with `sqlite3 foo.db` is exactly the kind of
    place it shouldn't sit in plaintext. Only its SHA-256 hash is
    stored, under pending_confirmation_hash.

    If the caller already set state.pending_confirmation_hash, that's
    trusted as-is. Otherwise, if there's a live pending_confirmation
    token, the hash is derived from it here.
    """
    if state.pending_confirmation_hash is not None:
        pending_hash = state.pending_confirmation_hash
    elif state.pending_confirmation is not None:
        pending_hash = _hash_token(state.pending_confirmation)
    else:
        pending_hash = None

    conn = _require_connection()
    with _lock:
        conn.execute(
            """
            INSERT INTO tasks
                (request_id, status, current_step, plan_json, history_json,
                 pending_confirmation_hash, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(request_id) DO UPDATE SET
                status = excluded.status,
                current_step = excluded.current_step,
                plan_json = excluded.plan_json,
                history_json = excluded.history_json,
                pending_confirmation_hash = excluded.pending_confirmation_hash,
                updated_at = excluded.updated_at
            """,
            (
                state.request_id,
                state.status.value,
                state.current_step,
                state.plan.model_dump_json(),
                _HISTORY_ADAPTER.dump_json(state.history).decode("utf-8"),
                pending_hash,
                state.updated_at.isoformat(),
            ),
        )
        conn.commit()


def load_state(request_id: str) -> TaskState | None:
    """Return the saved TaskState for request_id, or None if there isn't one.

    NOTE: the returned state's `pending_confirmation` (the plaintext
    token) is always None, even if a confirmation was pending when it
    was saved -- only the hash was ever persisted, and a hash can't be
    turned back into the token it came from. `pending_confirmation_hash`
    IS restored, though, which is what matches_pending() actually checks
    against -- so a resumed state can still validate an incoming
    confirmation correctly, it just never has the plaintext to leak.
    """
    conn = _require_connection()
    with _lock:
        row = conn.execute(
            """
            SELECT status, current_step, plan_json, history_json, pending_confirmation_hash, updated_at
            FROM tasks WHERE request_id = ?
            """,
            (request_id,),
        ).fetchone()
    if row is None:
        return None

    status, current_step, plan_json, history_json, pending_confirmation_hash, updated_at = row
    return TaskState(
        request_id=request_id,
        status=status,
        current_step=current_step,
        plan=Plan.model_validate_json(plan_json),
        history=_HISTORY_ADAPTER.validate_json(history_json),
        pending_confirmation=None,
        pending_confirmation_hash=pending_confirmation_hash,
        updated_at=datetime.fromisoformat(updated_at),
    )


def matches_pending(state: TaskState, token: str) -> bool:
    """Check whether `token` is the confirmation `state` is waiting on.

    Compares against state.pending_confirmation_hash, not
    state.pending_confirmation -- so this works identically whether
    `state` is live, in-memory, just-created, or was just reloaded from
    disk via load_state (which restores the hash but never the
    plaintext). hmac.compare_digest gives a constant-time comparison, so
    validating a confirmation can't be used as a timing oracle to guess
    the token character by character.

    Returns False when there's nothing pending -- there's nothing for
    any token to match against.
    """
    if state.pending_confirmation_hash is None:
        return False
    return hmac.compare_digest(_hash_token(token), state.pending_confirmation_hash)


def append_audit_event(event: AuditEvent) -> None:
    """Append one audit event. There is no update or delete for this table.

    Redacts event.details_redacted again here as a second line of
    defence, even though every caller should already have redacted its
    data before constructing the AuditEvent (that's what the field name
    is for).

    busy_timeout (see connect()) means a lock conflict with the other
    process normally resolves itself by retrying, quietly, inside
    sqlite3 -- but if it's still locked after 5s, or any other backend
    error happens, this catches it rather than crashing the caller's
    whole request. It does NOT re-raise, so the event is genuinely
    dropped -- but it's dropped LOUDLY, to stderr, so a drop is visible
    during testing/demo instead of just silently never appearing in the
    audit trail with no trace of why.
    """
    safe_details = redact_sensitive_data(event.details_redacted)
    conn = _require_connection()
    try:
        with _lock:
            conn.execute(
                """
                INSERT INTO audit_events
                    (timestamp, request_id, action_id, event_type, rule_id, details_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    event.timestamp.isoformat(),
                    event.request_id,
                    event.action_id,
                    event.event_type,
                    event.rule_id,
                    json.dumps(safe_details),
                ),
            )
            conn.commit()
    except sqlite3.Error as exc:
        print(
            f"[agent.store] DROPPED audit event: request_id={event.request_id} "
            f"event_type={event.event_type} action_id={event.action_id} error={exc!r}",
            file=sys.stderr,
        )


def get_audit_trail(request_id: str) -> list[AuditEvent]:
    """Return every audit event for request_id, oldest first.

    Ordered by SQLite's implicit rowid rather than an explicit id
    column. This relies on two things holding: the table is append-only
    (true -- this module has no update/delete for it), and the database
    is never VACUUMed (VACUUM is documented to renumber rowids on tables
    that don't declare an explicit INTEGER PRIMARY KEY, which would
    silently reorder history). Don't run VACUUM on this database.
    """
    conn = _require_connection()
    with _lock:
        rows = conn.execute(
            """
            SELECT timestamp, request_id, action_id, event_type, rule_id, details_json
            FROM audit_events
            WHERE request_id = ?
            ORDER BY rowid ASC
            """,
            (request_id,),
        ).fetchall()
    return [
        AuditEvent(
            timestamp=datetime.fromisoformat(row[0]),
            request_id=row[1],
            action_id=row[2],
            event_type=row[3],
            rule_id=row[4],
            details_redacted=json.loads(row[5]),
        )
        for row in rows
    ]
