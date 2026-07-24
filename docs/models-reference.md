# `agent/models.py` — Shared Type Reference

Every object that crosses a component boundary lives here. All models are `pydantic.BaseModel` with JSON-serialisable fields, because they cross a SQLite boundary (state store + audit log).

Do not redefine these locally. Need a field? Add it to `main`, don't fork it.

---

## Enums

```python
class RiskLevel(str, Enum):
    NONE         = "none"          # read-only, no side effects
    LOW          = "low"           # local, reversible (type into unsaved draft)
    MEDIUM       = "medium"        # creates state (new file)
    HIGH         = "high"          # destructive but local (overwrite, delete, bulk rename)
    CONSEQUENTIAL = "consequential" # leaves the machine (send, submit, publish, purchase)
    FORBIDDEN = "forbidden"   # never executable, regardless of confirmation

class PolicyOutcome(str, Enum):
    ALLOW   = "allow"
    CONFIRM = "confirm"
    CLARIFY = "clarify"
    DENY    = "deny"

class TaskStatus(str, Enum):
    PLANNING  = "planning"
    RUNNING   = "running"
    PAUSED    = "paused"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    AWAITING_CLARIFICATION = "awaiting_clarification"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED    = "failed"

class ActionStatus(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    PARTIAL = "partial"   # started, outcome unknown — do NOT collapse this to a bool
    SKIPPED = "skipped"
    DENIED             = "denied"              # policy blocked it; never executed
    NEEDS_CONFIRMATION = "needs_confirmation"  # held pending user confirmation
    CLARIFY            = "clarify"             # held pending user clarification
```

`RiskLevel` distinguishes HIGH from CONSEQUENTIAL because recovery treats them differently: a failed overwrite can sometimes be retried, a failed send cannot.

`ActionStatus.PARTIAL` exists so the executor can say "I clicked send but never saw confirmation." A `bool` erases exactly the case that matters most.

---

## `TaskRequest`

Produced by Dev 1 (voice), consumed by Dev 2 (planner).

```python
class TaskRequest(BaseModel):
    request_id: str            # uuid4; the correlation key for the ENTIRE task
    text: str                  # transcript
    source: Literal["speech", "text", "test"]
    confidence: float | None = None   # ASR confidence; low → clarify before planning
    received_at: datetime
```

`request_id` threads through every audit event. Generate it once at the microphone and never regenerate it.

`source` matters for the trust boundary: only `speech` and `text` are user-authored. Anything extracted from a document is data, never a `TaskRequest`.

---

## `Action`

Produced by Dev 2, consumed by Dev 3 (execute) and Dev 4 (authorize + verify).

```python
class Action(BaseModel):
    id: str                    # uuid4 — REQUIRED, joins attempt→verification→recovery
    type: str                  # must be in ACTION_VOCABULARY (below)
    target: str                # app or file the action operates on
    parameters: dict[str, Any] # type-specific; see vocabulary
    risk: RiskLevel            # planner's proposal; policy engine may raise it, never lower it
    expected_result: str       # human-readable, for the confirmation prompt
    step_index: int
```

**`id` is mandatory.** Without it the audit log is a flat list of events that can't be assembled into a per-action story — which is the whole demo.

**The LLM proposes `risk`; it does not decide it.** The policy engine may escalate. It must never accept a downgrade — that's the injection attack surface.

### `ACTION_VOCABULARY`

Allow-list. `validate_plan_schema` rejects anything not in this set.

| `type` | required `parameters` | typical risk |
|---|---|---|
| `read_document` | `path` | NONE |
| `inspect_ui` | `app` | NONE |
| `focus_application` | `app` | NONE |
| `click_element` | `locator` | LOW |
| `type_text` | `locator`, `text` | LOW |
| `update_spreadsheet` | `path`, `cell`, `value` | MEDIUM |
| `save_file` | `path`, `overwrite: bool` | MEDIUM / HIGH |
| `copy_file` | `src`, `dst` | MEDIUM |
| `move_file` | `src`, `dst` | HIGH |
| `delete_file` | `path` | HIGH |
| `submit_form` | `locator` | CONSEQUENTIAL |
| `send_message` | `recipient`, `body` | CONSEQUENTIAL |

No `run_command`. No `eval`. The absence of shell execution in the vocabulary is a safety property, not an oversight.

---

## `PolicyDecision`

Produced by Dev 4 (policy engine), consumed by Dev 2's loop.

```python
class PolicyDecision(BaseModel):
    action_id: str
    outcome: PolicyOutcome
    rule_id: str               # stable, e.g. "R-210"; appears verbatim in the audit log
    reason: str                # human-readable, spoken aloud on deny/clarify
    confirmation_token: str | None = None  # single-use; only set when outcome == CONFIRM
    decided_at: datetime
```

`rule_id` must be stable across runs so tests can assert on it and the demo log is readable.

`confirmation_token` is single-use and bound to one `action_id`. Reusing a token, or applying one to a different action, must fail `validate_confirmation`.

---

## `ActionResult`

Produced by Dev 3, consumed by Dev 4 (verify) and Dev 2 (state).

```python
class ActionResult(BaseModel):
    action_id: str
    status: ActionStatus
    evidence: dict[str, Any]   # window title, bytes written, element found — raw, unredacted
    error: str | None = None
    duration_ms: int
    completed_at: datetime
```

**`status == SUCCESS` is a claim, not a fact.** It means "the executor raised no exception." `pyautogui.write()` returns `None` whether the keystrokes landed in the spreadsheet or in a dialog that stole focus. Verification exists because of this.

`evidence` is unredacted here — redaction happens at the audit-log boundary, not at creation.

---

## `VerificationResult`

Produced by Dev 4, consumed by Dev 2's loop.

```python
class VerificationResult(BaseModel):
    action_id: str
    passed: bool | None        # None = no verifier existed. TREAT AS NOT VERIFIED.
    expected: str              # "B7 == '42500'"
    actual: str                # "B7=None, B8='42500'"
    evidence: dict[str, Any]
    reason: str
    checked_at: datetime
```

**`passed` is three-valued on purpose.**

| value | meaning | loop must |
|---|---|---|
| `True` | checked, correct | continue |
| `False` | checked, wrong | recover or stop |
| `None` | never checked | treat as failure, do not mark complete |

Consuming code: `if result.passed:` is correct. `if result.passed is not False:` is a bug — it silently promotes unverified actions to success.

`actual` should name where the value *did* land when it's findable. "B7 empty" is a failure report; "B7 empty, B8='42500'" is a diagnosis.

---

## `TaskState`

Owned by Dev 2, persisted by Dev 4's store, read on resume.

```python
class HistoryEntry(BaseModel):
    action: Action
    decision: PolicyDecision
    result: ActionResult | None
    verification: VerificationResult | None

class TaskState(BaseModel):
    request_id: str
    status: TaskStatus
    current_step: int
    plan: Plan
    history: list[HistoryEntry]
    pending_confirmation: str | None = None   # token awaiting a yes/no
    updated_at: datetime

    def attempts_for(self, action_id: str) -> int:
        return sum(1 for h in self.history if h.action.id == action_id)
```

`history` is append-only and must survive corrections — that's what stops the agent redoing completed work after the user changes their mind mid-task.

`attempts_for` is what `recover_or_stop` reads to enforce the retry cap.

---

## `AuditEvent`

Produced by everyone, written by Dev 4.

```python
class AuditEvent(BaseModel):
    timestamp: datetime
    request_id: str
    event_type: str            # see list below
    details_redacted: dict[str, Any]   # ALREADY through redact_sensitive_data()
    rule_id: str | None = None
    action_id: str | None = None
```

The field is named `details_redacted`, not `details`, so that passing raw data reads as obviously wrong at the call site.

### Event types

`transcript_received` · `plan_created` · `plan_rejected` · `policy_decision` · `confirmation_requested` · `confirmation_granted` · `confirmation_denied` · `action_attempted` · `verification_result` · `injection_detected` · `recovery_decision` · `task_paused` · `task_resumed` · `task_cancelled` · `task_completed` · `task_failed`

A denied action **must** produce an event. A block that leaves no trace is indistinguishable from a request that never happened — and the safety demo depends on showing it.

---

## `RecoveryDecision`

```python
class RecoveryDecision(BaseModel):
    action_id: str
    outcome: Literal["retry", "stop", "ask_user"]
    reason: str
    attempts_so_far: int
```

Never `retry` when `action.risk` is CONSEQUENTIAL. A failed send might have half-sent; retrying could double-send.

---

## Supporting types

```python
class Locator(BaseModel):
    strategy: Literal["accessibility", "role_name", "selector", "coordinates"]
    value: str
    app: str
    confidence: float | None = None

class Plan(BaseModel):
    request_id: str
    actions: list[Action]
    created_at: datetime
    model_id: str              # which local model produced it

class Assertion(BaseModel):
    kind: str                  # "cell_equals" | "file_exists_nonempty" | ... | "none"
    params: dict[str, Any]

class Detection(BaseModel):
    is_instruction: bool
    matched_pattern: str | None
    source: str                # which document/page the text came from
    excerpt_redacted: str
```

`Locator.strategy` is ordered by preference. `coordinates` is the fallback of last resort — when `confidence` is low the executor should fail safely rather than click blind.

`Detection.source` records where injected text came from. That text is logged as data and never authorises anything.

---

## Rules that apply to every model here

1. **JSON-serialisable only.** No file handles, no live UI references, no callables — these cross a SQLite boundary.
2. **Timestamps are UTC `datetime`**, serialised ISO-8601.
3. **`request_id` threads everything.** One per spoken request, generated at the microphone.
4. **`action_id` threads one action** across attempt, verification, and recovery.
5. **Additive changes are cheap, renames are not.** Prefer `new_field: X | None = None`.
6. **The LLM fills `Action` and `Plan`. Nothing else.** `PolicyDecision`, `VerificationResult`, `AuditEvent`, and `RecoveryDecision` are produced only by deterministic code.
