"""Print a request's audit trail as an aligned table for the demo projector.

    python -m agent.audit_view <request_id>
    python -m agent.audit_view <request_id> --tail

Columns are FIXED width, not sized to the data. That's deliberate: with
--tail, new rows keep arriving after the table has already been printed,
so widths can't be computed from "all the data" the way a one-shot
table normally would be -- a later, wider value would misalign every
row printed before it. Fixed widths mean every row lines up with every
other row for the whole session, at the cost of truncating unusually
long values (marked with an ellipsis).
"""

import argparse
import sys
import time

from agent import store
from agent.config import DB_PATH
from agent.models import AuditEvent

POLL_INTERVAL_SECONDS = 1.0

ACTION_ID_WIDTH = 14
EVENT_TYPE_WIDTH = 24
RULE_ID_WIDTH = 10
SUMMARY_MAX_LEN = 60


def _truncate(text: str, width: int) -> str:
    if len(text) <= width:
        return text
    # width - 1 leaves room for the ellipsis itself, so the truncated
    # string still fits inside `width` characters.
    return text[: width - 1] + "…"


def _pad(text: str, width: int) -> str:
    return _truncate(text, width).ljust(width)


def _summarize_details(details: dict) -> str:
    """One line, "key=value, key=value" -- never raw JSON on the projector."""
    if not details:
        return ""
    summary = ", ".join(f"{key}={value}" for key, value in details.items())
    return _truncate(summary, SUMMARY_MAX_LEN)


def _format_header() -> str:
    return (
        _pad("action_id", ACTION_ID_WIDTH)
        + "  "
        + _pad("event_type", EVENT_TYPE_WIDTH)
        + "  "
        + _pad("rule_id", RULE_ID_WIDTH)
        + "  "
        + "details"
    )


def _format_separator() -> str:
    return (
        "-" * ACTION_ID_WIDTH
        + "  "
        + "-" * EVENT_TYPE_WIDTH
        + "  "
        + "-" * RULE_ID_WIDTH
        + "  "
        + "-" * SUMMARY_MAX_LEN
    )


def _format_row(event: AuditEvent) -> str:
    return (
        _pad(event.action_id or "", ACTION_ID_WIDTH)
        + "  "
        + _pad(event.event_type, EVENT_TYPE_WIDTH)
        + "  "
        + _pad(event.rule_id or "", RULE_ID_WIDTH)
        + "  "
        + _summarize_details(event.details_redacted)
    )


def _print_trail(request_id: str) -> None:
    events = store.get_audit_trail(request_id)
    print(_format_header())
    print(_format_separator())
    for event in events:
        print(_format_row(event))


def _tail_trail(request_id: str) -> None:
    # flush=True on every line: stdout is fully buffered (not line-buffered)
    # once it's not a terminal -- piped to a file, or through some
    # projector-mirroring tool. Without an explicit flush, rows sit in
    # that buffer and only appear when it fills or the process exits
    # cleanly. A --tail session usually ends by being killed, not by
    # exiting cleanly, so unflushed rows would simply never show up.
    print(_format_header(), flush=True)
    print(_format_separator(), flush=True)
    printed = 0
    try:
        while True:
            events = store.get_audit_trail(request_id)
            for event in events[printed:]:
                print(_format_row(event), flush=True)
            printed = len(events)
            time.sleep(POLL_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        pass


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Print a request's audit trail as a table.")
    parser.add_argument("request_id")
    parser.add_argument(
        "--db-path", default=DB_PATH, help="SQLite file to read (default: %(default)s)"
    )
    parser.add_argument(
        "--tail", action="store_true", help="keep polling and print new events as they arrive"
    )
    args = parser.parse_args(argv)

    store.connect(args.db_path)

    if args.tail:
        _tail_trail(args.request_id)
    else:
        _print_trail(args.request_id)


if __name__ == "__main__":
    main(sys.argv[1:])
