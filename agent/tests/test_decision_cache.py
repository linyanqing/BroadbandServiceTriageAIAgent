"""Tests for the decision-level cache (models/decision_cache.py) that
guards nodes/decision.py's agent_decision and nodes/supervisor.py's
supervisor against a confirmed LangGraph replay behavior re-invoking a
policy's decide() call for a state it already decided on -- see
docs/agent-design.md's "Interrupt replay and idempotency".

These are node-level unit tests rather than full graph.invoke() runs: the
replay itself is a LangGraph engine behavior, not something these tests
trigger directly. What's tested is the actual guarantee that behavior
requires: for the SAME state (as LangGraph would feed a replayed node,
having already merged the prior call's cache entry back in via the
Annotated dict-union reducer), a node's returned action must not change
even if the underlying policy would answer differently -- exactly what a
live LLM re-asked the same question is not guaranteed to do.
"""

from agent.models.orchestrator_policy import OrchestratorPolicy
from agent.models.policy import DecisionPolicy
from agent.nodes.decision import make_agent_decision_node
from agent.nodes.supervisor import make_supervisor_node
from agent.tools.registry import SPECIALIST_TOOL_SCOPES, build_default_registry


class _FlakyPolicy(DecisionPolicy):
    """Simulates a live LLM that answers a repeated, identical question
    differently each time -- the exact failure mode decision_cache exists
    to neutralize."""

    def __init__(self) -> None:
        self.call_count = 0

    def decide(self, state):
        self.call_count += 1
        if self.call_count == 1:
            return {
                "type": "complete",
                "resolution": "healthy_no_action",
                "diagnostic_summary": "first answer",
            }
        return {
            "type": "complete",
            "resolution": "escalated_error",
            "diagnostic_summary": "different second answer -- must never be seen",
        }


class _FlakyOrchestratorPolicy(OrchestratorPolicy):
    def __init__(self) -> None:
        self.call_count = 0

    def decide(self, state):
        self.call_count += 1
        if self.call_count == 1:
            return {"type": "complete"}
        return {
            "type": "dispatch",
            "specialist": "equipment_reset",
            "reasoning": "unintended extra dispatch -- must never be seen",
        }


def test_specialist_decision_cache_shields_against_a_flaky_policy_on_replay():
    registry = build_default_registry().subset(SPECIALIST_TOOL_SCOPES["network_diagnose"])
    policy = _FlakyPolicy()
    node = make_agent_decision_node(policy, registry, "network_diagnose")

    state = {
        "customer_id": "123",
        "service_id": "BB-123",
        "observations": [],
        "tool_results": [],
        "iteration_count": 0,
    }
    first = node(state)
    assert policy.call_count == 1
    assert first["current_action"]["resolution"] == "healthy_no_action"

    # Simulate what LangGraph feeds a replayed node: the exact same input
    # state, plus the decision_cache entry the first call's Annotated
    # dict-union reducer would already have merged back in.
    replayed_state = {**state, "decision_cache": first["decision_cache"]}
    second = node(replayed_state)

    assert policy.call_count == 1, "a replay must not re-invoke the policy for identical state"
    assert second["current_action"] == first["current_action"]


def test_specialist_decision_cache_still_calls_policy_when_state_actually_changes():
    registry = build_default_registry().subset(SPECIALIST_TOOL_SCOPES["network_diagnose"])
    policy = _FlakyPolicy()
    node = make_agent_decision_node(policy, registry, "network_diagnose")

    state = {
        "customer_id": "123",
        "service_id": "BB-123",
        "observations": [],
        "tool_results": [],
        "iteration_count": 0,
    }
    first = node(state)
    assert policy.call_count == 1

    # Genuinely different state (a new tool result arrived) -- must be a
    # fresh decision, not served from cache.
    changed_state = {
        **state,
        "decision_cache": first["decision_cache"],
        "tool_results": [{"tool": "check_outage", "input": {}, "output": {}, "error": None}],
    }
    second = node(changed_state)
    assert policy.call_count == 2
    assert second["current_action"]["resolution"] == "escalated_error"


def test_orchestrator_decision_cache_shields_against_a_flaky_policy_on_replay():
    policy = _FlakyOrchestratorPolicy()
    node = make_supervisor_node(policy)

    state = {
        "customer_id": "111",
        "issue_type": "billing_dispute",
        "confidence": 0.85,
        "billing_flagged": True,
        "specialist_history": ["network_diagnose", "billing"],
        "specialist_results": [
            {
                "specialist": "billing",
                "resolution": "billing_hold_resolved",
                "diagnostic_summary": "applied a credit",
            }
        ],
    }
    first = node(state)
    assert policy.call_count == 1
    assert first["orchestrator_action"]["type"] == "complete"

    replayed_state = {**state, "orchestrator_decision_cache": first["orchestrator_decision_cache"]}
    second = node(replayed_state)

    assert policy.call_count == 1, "a replay must not re-invoke the orchestrator policy either"
    assert second["orchestrator_action"] == first["orchestrator_action"]
    # This is the exact live bug this guard fixes: without it, the second,
    # inconsistent answer would dispatch an unintended specialist instead
    # of finishing on the correct, already-decided resolution.
    assert second["orchestrator_action"]["type"] != "dispatch"
