"""windows_agent — Voice-Controlled Computer Use Agent (Windows-first).

Milestone 0 establishes the execution *contract*: shared Pydantic schemas, an
async BaseExecutor, an ActionRegistry, and a Dispatcher — plus mock executors
and tests. No desktop automation, policy engine, LLM, or voice yet; those slot
into later milestones through reserved extension points in the Dispatcher.
"""

__version__ = "0.0.0"
