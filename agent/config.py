"""Shared configuration for the verification/audit slice.

DB_PATH is resolved relative to the repo root, not the current working
directory -- so `python -m agent.audit_view ...` finds the same database
whether it's run from the repo root, from inside agent/, or from
anywhere else. Override with the AGENT_DB environment variable to point
at a different file (e.g. a demo copy, or a test fixture).
"""

import os
from pathlib import Path

# agent/config.py -> agent/ -> repo root. Two .parent calls, not one --
# this file lives inside the agent/ package, so the repo root is its
# grandparent, not its parent.
_REPO_ROOT = Path(__file__).resolve().parent.parent

DB_PATH = os.environ.get("AGENT_DB", str(_REPO_ROOT / "agent_store.db"))
