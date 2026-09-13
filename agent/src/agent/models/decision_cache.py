"""Shared decision-cache-key helper, used at every LLM decision call site
(the orchestrator's supervisor node, and each specialist's agent_decision
node) to guard against a confirmed LangGraph replay behavior: once any
specialist subgraph executes within a graph.invoke() call, earlier plain
nodes/specialists in the same investigation can be re-invoked for real --
see docs/agent-design.md's "Interrupt replay and idempotency".

For a deterministic mock policy a replayed decide() call harmlessly
recomputes the same answer. For a live LLM-backed policy it is not
guaranteed to -- a model re-asked the exact same question in a second,
independent call can answer differently, which is a real correctness
problem here (confirmed live: an unintended specialist dispatch silently
overwrote the correct final resolution). This is the decision-level
analogue of nodes/tool_execution.py's _already_executed guard: the same
(decision point, input state) pair must produce the SAME action every time
within one investigation, not just usually.
"""

from __future__ import annotations

import hashlib
import json


def compute_cache_key(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()
