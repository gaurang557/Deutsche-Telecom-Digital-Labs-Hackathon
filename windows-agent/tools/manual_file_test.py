r"""Interactive manual tester for the `file.*` functionality (Milestone 2).

This is a HUMAN-DRIVEN harness (not an automated test). It wires up the real
execution pipeline exactly as the agent will use it —

    ActionRegistry (+ FileExecutor)
      → Dispatcher (+ AllowAllPolicy, VerificationRegistry, InMemoryAuditSink)

— then lets you type file commands and shows, for each one:
  * the resulting ActionStatus,
  * the bounded evidence the executor returned,
  * the INDEPENDENT verification result (re-observed state), and
  * the ordered audit trail emitted around the action.

Everything runs against a throwaway `sandbox/` directory so you cannot harm real
files. Relative paths you type are resolved inside that sandbox; absolute paths
are used as-is (use with care).

RUN (from the windows-agent/ folder, using the project venv):

    & "..\.venv\Scripts\python.exe" tools\manual_file_test.py
    # or point at a custom sandbox:
    & "..\.venv\Scripts\python.exe" tools\manual_file_test.py --sandbox C:\tmp\wa

Type `help` at the prompt for the command list, `quit` to exit.

NOTE ON POLICY: this harness uses AllowAllPolicy, so every action is authorized
(the real deterministic policy + confirmation gating arrives in M12). The point
here is to exercise executor + verification behaviour, which is what M2 delivers.
"""

from __future__ import annotations

import argparse
import asyncio
import shlex
import sys
import uuid
from pathlib import Path

# Make the module importable when run as a plain script (sys.path[0] is tools/).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from windows_agent.audit import InMemoryAuditSink  # noqa: E402
from windows_agent.contracts import Action  # noqa: E402
from windows_agent.execution import ActionRegistry, Dispatcher  # noqa: E402
from windows_agent.executors import register_file_executor  # noqa: E402
from windows_agent.policy import AllowAllPolicy  # noqa: E402
from windows_agent.verification import VerificationRegistry, register_file_verifiers  # noqa: E402


HELP = """
Commands (paths are relative to the sandbox unless absolute):
  ls [dir]                 list a directory (default: sandbox root)
  exists <path>            check if a path exists
  read <path>              read a text file (bounded)
  write <path> <text...>   create/write a text file (no overwrite)
  writeover <path> <text.> write a text file, overwriting if present
  copy <src> <dst>         copy a file
  move <src> <dst>         move/rename a file
  mkdir <path>             create a directory (parents allowed)
  delete <path>            delete a file (consequential)
  help                     show this help
  quit / exit              leave

Try this quick scenario:
  write hello.txt Hello world
  read hello.txt
  copy hello.txt copy.txt
  move copy.txt sub/moved.txt        (will fail: parent 'sub' missing)
  mkdir sub
  move copy.txt sub/moved.txt
  ls sub
  delete sub/moved.txt
"""

# Maps a typed command to (action_type, builder(args, resolve) -> (target, params)).
def _build(cmd: str, args: list[str], resolve):
    if cmd == "ls":
        return "file.list", (str(resolve(args[0])) if args else str(resolve(".")), {})
    if cmd == "exists":
        return "file.exists", (str(resolve(args[0])), {})
    if cmd == "read":
        return "file.read_text", (str(resolve(args[0])), {})
    if cmd in ("write", "writeover"):
        path, text = args[0], " ".join(args[1:])
        return "file.write_text", (str(resolve(path)), {"content": text, "overwrite": cmd == "writeover"})
    if cmd == "copy":
        return "file.copy", (str(resolve(args[0])), {"destination": str(resolve(args[1]))})
    if cmd == "move":
        return "file.move", (str(resolve(args[0])), {"destination": str(resolve(args[1]))})
    if cmd == "mkdir":
        return "file.mkdir", (str(resolve(args[0])), {"parents": True})
    if cmd == "delete":
        return "file.delete", (str(resolve(args[0])), {})
    raise ValueError(f"Unknown command: {cmd!r} (type 'help')")


def _print_result(result, sink: InMemoryAuditSink) -> None:
    print(f"\n  status      : {result.status.value}")
    if result.evidence:
        print(f"  evidence    : {result.evidence}")
    if result.verification is not None:
        v = result.verification
        print(f"  verification: {v.status.value} ({v.method})")
        if v.message:
            print(f"                {v.message}")
        if v.expected is not None or v.observed is not None:
            print(f"                expected={v.expected!r} observed={v.observed!r}")
    if result.error is not None:
        print(f"  error       : [{result.error.code}] {result.error.message}")
    print("  audit trail :")
    for event in sink.events:
        tag = f" ({event.outcome})" if event.outcome else ""
        print(f"      - {event.event_type.value}{tag}: {event.summary}")
    print()


async def _run(sandbox: Path) -> None:
    registry = ActionRegistry()
    register_file_executor(registry)
    verification = VerificationRegistry()
    register_file_verifiers(verification)
    audit = InMemoryAuditSink()
    dispatcher = Dispatcher(registry, AllowAllPolicy(), verification=verification, audit=audit)

    task_id = uuid.uuid4().hex[:8]
    seq = 0
    resolve = lambda p: (Path(p) if Path(p).is_absolute() else sandbox / p)

    print(f"Sandbox: {sandbox}")
    print("Manual file.* tester. Type 'help' for commands, 'quit' to exit.")

    while True:
        try:
            line = input("file> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if line in ("quit", "exit"):
            break
        if line == "help":
            print(HELP)
            continue

        try:
            parts = shlex.split(line)
        except ValueError as exc:  # e.g. unbalanced quotes
            print(f"  parse error: {exc}")
            continue
        cmd, args = parts[0], parts[1:]

        try:
            action_type, (target, params) = _build(cmd, args, resolve)
        except (ValueError, IndexError) as exc:
            print(f"  {exc}")
            continue

        action = Action(
            action_id=uuid.uuid4().hex[:8],
            task_id=task_id,
            sequence=seq,
            type=action_type,
            target=target,
            parameters=params,
            reason=f"manual: {line}",
        )
        seq += 1

        audit.events.clear()  # show only this action's events
        result = await dispatcher.dispatch(action)
        _print_result(result, audit)


def main() -> None:
    parser = argparse.ArgumentParser(description="Interactive manual tester for file.* actions")
    parser.add_argument(
        "--sandbox",
        default=str(Path.cwd() / "sandbox"),
        help="Directory to operate in (created if missing). Default: ./sandbox",
    )
    ns = parser.parse_args()
    sandbox = Path(ns.sandbox).resolve()
    sandbox.mkdir(parents=True, exist_ok=True)
    asyncio.run(_run(sandbox))


if __name__ == "__main__":
    main()
