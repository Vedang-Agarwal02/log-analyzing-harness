"""Names the agent run that is currently calling a tool.

The tool executor sets it before each tool call. The fault injector reads it, so rules
like "fail the first N calls" are counted per caller, independent of parallel interleaving.
"""

from contextvars import ContextVar

CURRENT_CALLER: ContextVar[str] = ContextVar("current_caller", default="anonymous")
