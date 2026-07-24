r"""Interactive manual tester for the `pdf.*` functionality (Milestone 3).

This is a HUMAN-DRIVEN harness (not an automated test). It wires up the real
execution pipeline exactly as the agent will use it —

    ActionRegistry (+ PdfExecutor)
      → Dispatcher (+ AllowAllPolicy, VerificationRegistry, InMemoryAuditSink)

— then lets you type pdf commands and shows, for each one:
  * the resulting ActionStatus,
  * the bounded evidence the executor returned (page count, metadata, text, hits),
  * the verification result (SKIPPED — pdf.* is read-only, RiskLevel.NONE), and
  * the ordered audit trail emitted around the action.

All `pdf.*` actions are READ-ONLY, so nothing on disk is ever modified. Relative
paths you type are resolved inside a throwaway `sandbox/` directory; absolute
paths are used as-is, so you can point at real PDFs anywhere on the machine.

To get started with no PDF of your own, run `sample` to generate a 3-page
`sample.pdf` (with known text + metadata) inside the sandbox, then exercise the
commands against it.

RUN (from the windows-agent/ folder, using the project venv):

    & "..\.venv\Scripts\python.exe" tools\manual_pdf_test.py
    # or point at a custom sandbox:
    & "..\.venv\Scripts\python.exe" tools\manual_pdf_test.py --sandbox C:\tmp\wa

Type `help` at the prompt for the command list, `quit` to exit.

NOTE ON POLICY: this harness uses AllowAllPolicy, so every action is authorized
(the real deterministic policy arrives in M12). The point here is to exercise
PdfExecutor behaviour + bounded evidence, which is what M3 delivers.
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

import fitz  # PyMuPDF — also used to synthesise the sample PDF  # noqa: E402

from windows_agent.audit import InMemoryAuditSink  # noqa: E402
from windows_agent.contracts import Action  # noqa: E402
from windows_agent.execution import ActionRegistry, Dispatcher  # noqa: E402
from windows_agent.executors import register_pdf_executor  # noqa: E402
from windows_agent.policy import AllowAllPolicy  # noqa: E402
from windows_agent.verification import VerificationRegistry  # noqa: E402


HELP = """
Commands (paths are relative to the sandbox unless absolute):
  sample [name]            generate a 3-page sample PDF (default: sample.pdf)
  ls                       list PDFs in the sandbox
  pages <path>             pdf.page_count   — number of pages
  meta <path>              pdf.get_metadata — title/author/… + page_count
  read <path>              pdf.read_text    — whole document (bounded)
  read <path> <page>       pdf.read_text    — a single 0-based page
  read <path> <start> <end>  pdf.read_text  — inclusive 0-based page range
  search <path> <query...> pdf.search       — per-page hit counts for a query
  help                     show this help
  quit / exit              leave

Try this quick scenario:
  sample
  pages sample.pdf
  meta sample.pdf
  read sample.pdf 0
  read sample.pdf 1 2
  search sample.pdf Hello
  search sample.pdf "quick brown fox"
"""

# Text baked into the generated sample PDF (one entry per page). Chosen so that
# `search Hello` hits multiple pages and `search fox` hits exactly one.
_SAMPLE_PAGES = (
    "Invoice 2026\nAmount: 42500\nHello World - page one.",
    "Second page.\nThe quick brown fox jumps.\nHello again.",
    "Final page.\nConfidential summary.\nHello World - the end.",
)
_SAMPLE_METADATA = {"title": "Sample Report", "author": "Windows Agent"}


def _make_sample(path: Path) -> tuple[int, int]:
    """Create a small multi-page PDF with known text + metadata. Returns
    (page_count, byte_size)."""
    doc = fitz.open()
    try:
        for body in _SAMPLE_PAGES:
            page = doc.new_page()
            page.insert_text((72, 72), body, fontsize=14)
        doc.set_metadata(_SAMPLE_METADATA)
        path.parent.mkdir(parents=True, exist_ok=True)
        doc.save(str(path))
        return doc.page_count, path.stat().st_size
    finally:
        doc.close()


def _as_int(token: str) -> int | None:
    try:
        return int(token)
    except (TypeError, ValueError):
        return None


# Maps a typed command to (action_type, (target, params)).
def _build(cmd: str, args: list[str], resolve):
    if cmd == "pages":
        return "pdf.page_count", (str(resolve(args[0])), {})
    if cmd == "meta":
        return "pdf.get_metadata", (str(resolve(args[0])), {})
    if cmd == "read":
        target = str(resolve(args[0]))
        rest = args[1:]
        if not rest:
            return "pdf.read_text", (target, {})
        if len(rest) == 1:
            page = _as_int(rest[0])
            if page is None:
                raise ValueError(f"page must be an integer, got {rest[0]!r}")
            return "pdf.read_text", (target, {"page": page})
        start, end = _as_int(rest[0]), _as_int(rest[1])
        if start is None or end is None:
            raise ValueError(f"start/end must be integers, got {rest[0]!r}/{rest[1]!r}")
        return "pdf.read_text", (target, {"start_page": start, "end_page": end})
    if cmd == "search":
        if len(args) < 2:
            raise ValueError("search needs a path and a query")
        return "pdf.search", (str(resolve(args[0])), {"query": " ".join(args[1:])})
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
    if result.error is not None:
        print(f"  error       : [{result.error.code}] {result.error.message}")
    print("  audit trail :")
    for event in sink.events:
        tag = f" ({event.outcome})" if event.outcome else ""
        print(f"      - {event.event_type.value}{tag}: {event.summary}")
    print()


async def _run(sandbox: Path) -> None:
    registry = ActionRegistry()
    register_pdf_executor(registry)
    # No pdf verifiers exist (read-only actions); an empty registry yields the
    # expected SKIPPED verification, which is exactly what we want to show.
    verification = VerificationRegistry()
    audit = InMemoryAuditSink()
    dispatcher = Dispatcher(registry, AllowAllPolicy(), verification=verification, audit=audit)

    task_id = uuid.uuid4().hex[:8]
    seq = 0
    resolve = lambda p: (Path(p) if Path(p).is_absolute() else sandbox / p)

    print(f"Sandbox: {sandbox}")
    print("Manual pdf.* tester. Type 'help' for commands, 'sample' to make a test PDF, 'quit' to exit.")

    while True:
        try:
            line = input("pdf> ").strip()
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

        # Local (non-pipeline) conveniences.
        if cmd == "sample":
            name = args[0] if args else "sample.pdf"
            target = resolve(name)
            pages, size = _make_sample(target)
            print(f"  created {target} ({pages} pages, {size} bytes)")
            continue
        if cmd == "ls":
            pdfs = sorted(p.name for p in sandbox.glob("*.pdf"))
            print(f"  {sandbox}:")
            print("    " + ("  ".join(pdfs) if pdfs else "(no PDFs — run 'sample')"))
            continue

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
    parser = argparse.ArgumentParser(description="Interactive manual tester for pdf.* actions")
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
