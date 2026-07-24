# Voice-Controlled Computer Use Agent —  Development Plan

## 1. MVP Goal

Build a local, voice-driven desktop agent that can complete a small set of multi-application tasks safely and demonstrate:

1. A spoken request converted into a structured plan.
2. Desktop actions executed across supported applications.
3. Deterministic permission checks and confirmation before consequential actions.
4. Pause, resume, correction, cancellation, and interruption.
5. Verification of the final result.
6. A redacted audit trail of decisions, actions, failures, and outcomes.

The five-hour target is a **reliable vertical slice**, not unrestricted computer control. Prioritise:

- PDF/document reading → spreadsheet or text-editor update.
- File organisation using copy/move, with delete and overwrite guarded.
- Browser and office-app interaction through one consistent desktop automation layer.

## 2. Proposed Local Architecture

```text
Microphone / audio file
        │
        ▼
Local ASR ──► Conversation + intent parser ──► Typed task plan
                                                   │
                                  deterministic policy engine
                                     │ allow / confirm / deny
                                     ▼
                             desktop action executor
                         (accessibility first, GUI fallback)
                                     │
                                     ▼
                              action verification
                                     │
                       state store + redacted audit log
                                     │
                                     ▼
                               Local TTS response
```

### Trust boundary

- Only direct user speech and explicit confirmation can authorise an action.
- Text found in webpages, PDFs, documents, messages, tooltips, or application UI is **untrusted data**.
- The LLM may propose plans, but only deterministic code can allow, deny, or require confirmation.
- Commands read from on-screen content are never executed.

## 3. Five-Hour Phases and Team Division

| Time | Shared milestone | Developer 1 — Voice/UI | Developer 2 — Agent/State | Developer 3 — Desktop/Verification | Developer 4 — Safety/QA |
|---|---|---|---|---|---|
| 0:00–0:30 | Freeze scope, schemas, and demo tasks | Audio and event interfaces | Plan/action schemas | Automation adapter interface | Risk classes and policy table |
| 0:30–1:45 | Core components work independently | ASR, TTS, push-to-talk | Planner, task state machine | App control and screenshots | Policy engine, confirmations, audit log |
| 1:45–2:45 | First end-to-end task | Connect voice loop | Connect planner to executor | Implement PDF → spreadsheet flow | Gate every action and add redaction |
| 2:45–3:30 | Interruption and recovery | Pause/cancel voice commands | Re-plan after correction | Verify actions; retry safely | Unsafe/ambiguous-request tests |
| 3:30–4:15 | Evaluation suite | ASR error fixtures | Planner/state tests | Integration task fixtures | Safety and injection tests |
| 4:15–4:45 | Demo rehearsal and fixes | Conversation polish | Failure explanations | Stabilise selectors/timing | Review logs and confirmation flow |
| 4:45–5:00 | Package deliverables | Setup notes | Architecture summary | Known limitations | Test report and demo checklist |

### Integration rule

All developers build against these shared typed objects from the first 30 minutes:

```python
TaskRequest(text, source, request_id)
Action(action_id, task_id, sequence, type, target, parameters, expected_result, reason)
PolicyDecision(outcome, risk_level, rule_id, reason, confirmation_token=None)
ActionResult(action_id, task_id, status, evidence, verification=None, error=None)
TaskState(status, current_step, plan, history)
AuditEvent(timestamp, event_type, task_id, action_id, details_redacted)
```

Use `pydantic` models and JSON-serialisable fields so components can be tested separately.
`Action` deliberately has no risk field: deterministic policy assigns
`PolicyDecision.risk_level`. Current execution and audit correlation uses
`task_id` (plus `action_id` for action-level events), not `request_id`.

## 4. Functions to Implement

### A. Voice and conversation

```python
def transcribe_audio(audio: bytes) -> Transcript
def detect_control_intent(text: str) -> ControlIntent | None
def speak(text: str) -> None
def ask_clarification(question: str) -> str
def request_confirmation(summary: str, token: str) -> bool
```

- Control intents (`pause`, `resume`, `cancel`, `correct`) must be detected before normal planning.
- Require an explicit response tied to a one-use confirmation token; silence or unrelated speech means no confirmation.

### B. Planning and state management

```python
def create_plan(request: TaskRequest, context: DesktopContext) -> Plan
def validate_plan_schema(plan: Plan) -> ValidationResult
def next_action(state: TaskState) -> Action | None
def apply_correction(state: TaskState, correction: str) -> TaskState
def pause_task(state: TaskState) -> TaskState
def resume_task(state: TaskState) -> TaskState
def cancel_task(state: TaskState) -> TaskState
```

- The LLM must return only actions from an allow-listed action vocabulary.
- Preserve completed-step history when correcting or resuming.
- Never infer missing destinations, recipients, filenames, or overwrite intent when the choice matters.

### C. Deterministic safety core

```python
def classify_risk(action: Action, context: PolicyContext) -> RiskLevel
def authorize(action: Action, context: PolicyContext) -> PolicyDecision
def validate_confirmation(token: str, response: str) -> bool
def detect_untrusted_instruction(content: str, source: str) -> Detection
def redact_sensitive_data(data: dict) -> dict
```

Minimum policy:

| Action | Default decision |
|---|---|
| Read a local sample file or inspect UI | Allow |
| Type into an unsaved local draft | Allow and log |
| Create a new local file | Allow; confirm if it exposes sensitive data |
| Overwrite, delete, permanently move, or bulk-rename files | Confirm |
| Send a message, submit a form, purchase, or publish | Confirm immediately before execution |
| Reveal secrets or sensitive information | Deny unless explicitly permitted by a predefined local policy |
| Run a command copied from a page/document/message | Deny |
| Unknown action or target | Clarify or deny |

The rule engine should return a stable `rule_id` and human-readable reason for every decision. The LLM cannot modify risk labels, policies, confirmation state, or trusted-user identity.

### D. Desktop understanding and execution

```python
def capture_desktop() -> DesktopSnapshot
def inspect_accessibility_tree(app: str) -> UIState
def execute_action(action: Action) -> ActionResult
def focus_application(app: str) -> ActionResult
def click_element(locator: Locator) -> ActionResult
def type_text(locator: Locator, text: str) -> ActionResult
def read_document(path: str) -> ExtractedContent
def update_spreadsheet(path: str, edits: list[CellEdit]) -> ActionResult
```

Prefer stable accessibility roles, names, and document APIs. Use screen coordinates or vision only as a fallback, and fail safely when confidence is low.

### E. Verification, recovery, and audit

```python
def define_expected_result(action: Action) -> Assertion
def verify_action(action: Action, result: ActionResult) -> VerificationResult
def verify_task(plan: Plan, state: TaskState) -> VerificationResult
def recover_or_stop(failure: ActionFailure, state: TaskState) -> RecoveryDecision
def append_audit_event(event: AuditEvent) -> None
```

Verification must inspect the resulting application or file state; the absence of an exception is not proof of success. Limit retries, do not retry consequential actions automatically, and log both the attempt and verification evidence.

## 5. Recommended Python Libraries

All runtime models and components must be local and open-source/open-weight.

| Purpose | Primary choice | Notes |
|---|---|---|
| ASR | `faster-whisper` + a local Whisper model | Good accuracy and simple local inference |
| Audio capture | `sounddevice`, `soundfile`, `numpy` | Push-to-talk is faster to stabilise than wake-word detection |
| Voice activity detection | `webrtcvad` | Optional if push-to-talk is sufficient |
| Local LLM runtime | `llama-cpp-python` | Use a small instruct model in GGUF format already available locally |
| Structured outputs | `pydantic`, `jsonschema` | Reject malformed or unknown actions |
| TTS | `pyttsx3` | Fully local; system voices are adequate for the demo |
| Desktop automation | `pyautogui`, `pynput` | Cross-app fallback; add platform accessibility APIs where available |
| Linux accessibility | `pyatspi` | Prefer semantic controls in the supplied Linux desktop |
| Browser automation | `playwright` | Use only for known browser tasks; keep external page content untrusted |
| PDF extraction | `pymupdf` | Fast text and page extraction |
| Spreadsheet files | `openpyxl` | Deterministic cell updates and verification |
| Word documents | `python-docx` | Preserve formatting for targeted edits where possible |
| Presentations | `python-pptx` | Targeted text and shape edits |
| Image/OCR fallback | `Pillow`, `opencv-python`, `pytesseract` | Use only when accessibility or document APIs fail |
| State and audit | Python `sqlite3`, `logging` | No server required; store redacted structured events |
| Testing | `pytest`, `pytest-asyncio`, `hypothesis` | Unit, state-machine, and policy tests |

Avoid adding multiple interchangeable frameworks during the hackathon. Confirm the supplied OS and preinstalled model files before locking the accessibility and LLM adapters.

## 6. Execution Loop

```python
while task.status not in {"completed", "cancelled", "failed"}:
    handle_pending_user_control_events()
    action = next_action(task)
    decision = authorize(action, policy_context)

    if decision.outcome == "deny":
        explain_and_stop(decision)
    elif decision.outcome == "clarify":
        task = apply_correction(task, ask_clarification(decision.reason))
    elif decision.outcome == "confirm":
        if not request_confirmation(action_summary(action), decision.confirmation_token):
            cancel_pending_action()
    else:
        result = execute_action(action)
        verification = verify_action(action, result)
        update_state_and_audit(task, action, decision, result, verification)
```

Check for pause, correction, or cancellation between every action and before accepting a confirmation.

## 7. Evaluation Suite

Create deterministic fixtures and mock adapters first; run a smaller set against the real desktop.

1. **Success:** find a value in a sample PDF, place it in the correct spreadsheet cell, save a new output file, and verify the cell.
2. **Incomplete action:** executor reports success but verification finds the wrong cell; task must not be marked complete.
3. **Ambiguous speech:** missing file or destination causes a specific clarification question.
4. **Correction:** user changes the destination cell midway; remaining plan is updated without repeating completed work.
5. **Cancellation:** user says “cancel” before a pending write; no further actions occur.
6. **Unsafe request:** deletion, overwrite, form submission, or message send is blocked until explicit confirmation.
7. **Prompt injection:** a PDF/webpage says to ignore rules or run a command; content is logged as data and never authorises an action.
8. **Application-state change:** a window closes or focus changes; executor re-inspects state or stops safely.
9. **Transcription error:** low confidence or safety-relevant ambiguity triggers clarification.
10. **Unsupported task:** agent explains the limitation and performs no speculative action.
11. **Audit privacy:** secrets, tokens, email addresses, and sensitive cell values are masked in logs.

## 8. Demo Script and Deliverables

### Demo cases

- **Multi-application success:** spoken request to extract a named figure from a PDF, enter it into a spreadsheet, save as a new file, and report verified evidence.
- **Correction/interruption:** pause during the task, change the output filename, then resume.
- **Clarification/refusal:** request an ambiguous overwrite or instruct the agent through text inside a document; the agent asks for confirmation or refuses.
- **Known limitation:** visually complex canvas controls or unsupported apps may require manual assistance because accessibility metadata is absent.

### Definition of done

- `python -m agent` starts the local app with documented setup steps.
- At least one real desktop task completes and is independently verified.
- Safety decisions are deterministic and unit-tested.
- Consequential actions cannot execute without a valid confirmation.
- Pause, resume, correction, and cancellation work between action steps.
- Audit records include transcript, rule decision, action, verification, and failure data with masking.
- README includes architecture, supported apps/actions, model choices, setup, tests, demo steps, and limitations.

## 9. Time-Saving Trade-offs

- Use push-to-talk instead of continuous wake-word listening.
- Support a small action vocabulary instead of arbitrary generated code or shell commands.
- Optimise for the supplied desktop and sample applications; keep adapters replaceable.
- Use direct file libraries for reliable edits and GUI automation to navigate or demonstrate cross-app operation.
- Require confirmation more often when risk classification lacks context.
- Skip autonomous background work, account integrations, model training, and general web browsing in the MVP.

