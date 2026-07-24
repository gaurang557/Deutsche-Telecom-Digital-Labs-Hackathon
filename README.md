# Voice-Controlled Desktop Agent

## For teammates: writing to the audit log

There is exactly one function to call: `agent.store.log(...)`. It
redacts your `details` dict automatically and never raises — a failed
write is dropped, not crashed on, and the drop is printed to stderr so
you'll see it during testing. Call `agent.store.connect(agent.config.DB_PATH)`
once at startup before your first `log()` call.

```python
from agent import store
from agent.config import DB_PATH

store.connect(DB_PATH)
```

**Pass `action_id=` whenever the event concerns one specific action** —
an attempt, its verification, a recovery decision. That's the only
thing that joins those events together in the audit trail later. Leave
it out for task-level events (nothing happened to one particular action
yet).

### Dev 1 — voice / UI

```python
store.log(request_id, "transcript_received", {"text": transcript, "source": "speech"})
store.log(request_id, "task_paused", {"reason": "user interrupted"})
store.log(request_id, "task_resumed", {})
store.log(request_id, "task_cancelled", {"reason": "user said stop"})
```

### Dev 2 — planner / state machine

```python
store.log(request_id, "plan_created", {"step_count": len(plan.actions), "model_id": plan.model_id})
store.log(request_id, "correction_applied", {"what_changed": "reordered steps 2 and 3"})
store.log(request_id, "task_completed", {"final_status": "success"})
store.log(request_id, "task_failed", {"reason": "policy denied a required step"})
```

### Dev 3 — desktop / execution

```python
# action_id ties this attempt to its later verification -- always pass it.
store.log(request_id, "action_attempted", {"status": result.status, "evidence": result.evidence},
          action_id=action.id)
store.log(request_id, "verification_result", {"passed": verification.passed, "actual": verification.actual},
          action_id=action.id)
```

### Dev 4 — safety / verification / audit (this slice)

```python
store.log(request_id, "policy_decision", {"outcome": decision.outcome, "reason": decision.reason},
          action_id=action.id, rule_id=decision.rule_id)
store.log(request_id, "confirmation_requested", {"expected_result": action.expected_result},
          action_id=action.id, rule_id=decision.rule_id)
store.log(request_id, "confirmation_granted", {}, action_id=action.id)
store.log(request_id, "confirmation_denied", {"reason": "user said no"}, action_id=action.id)
store.log(request_id, "injection_detected", {"source": detection.source, "excerpt": detection.excerpt_redacted},
          rule_id="R-INJECT-01")
```

### Watching a live run

```
python -m agent.audit_view <request_id>          # one-shot dump
python -m agent.audit_view <request_id> --tail   # keep polling and print new rows
```
