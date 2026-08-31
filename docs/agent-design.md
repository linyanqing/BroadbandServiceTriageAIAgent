# Agent Design

## Why a hybrid agent, not a free-form chatbot

LangGraph owns the *structure*: which nodes exist, which transitions are
legal, and where execution must pause for a human. The decision policy
plugged into the `agent_decision` node owns the *reasoning*: what the next
action should be, given what's been observed so far. The policy can never
route the graph anywhere the graph doesn't already allow, and it can never
call anything outside the tool registry -- see
[security.md](security.md) for how that's enforced.

## State (`agent/src/agent/state.py`)

`AgentState` is a `TypedDict`. Three fields accumulate across the ReAct loop
via `Annotated[list, operator.add]`: `observations`, `tool_calls`,
`tool_results` -- every pass through `agent_decision` sees the full history,
not just the last result. Everything else (`current_action`, `service_id`,
`ticket_id`, `status`, ...) is overwritten each step.

## The ReAct loop

```
perception -> agent_decision -> (tool_execution -> observation ->) agent_decision -> ... -> final_response
```

`agent_decision` is called by a `DecisionPolicy`
(`agent/src/agent/models/policy.py`):

- `MockDecisionPolicy` (`mock_policy.py`) -- a small rule-based policy used
  whenever `MOCK_MODE=true` (the default). It is **not** a fixed call
  sequence: every branch reads only `state["tool_results"]` and reacts to
  what's actually there. It exists so the agent, its tests, and the demo
  scenarios all run without AWS credentials.
- `BedrockDecisionPolicy` (`bedrock.py`) -- binds the registry's tool specs
  plus a `finish` control action to `ChatBedrockConverse` and parses
  `response.tool_calls` back into the same `CurrentAction` shape. Used when
  `MOCK_MODE=false`. Its parsing logic (`parse_decision` /
  `_action_from_tool_call`) is unit-tested independently of a live model
  call (`tests/test_security.py`).

Both policies produce identical output shapes, so the rest of the graph
(routing, tool execution, observation, response) is completely agnostic to
which one is running.

## Why the graph, not the policy, decides high-risk routing

`create_fault_ticket` is the one high-risk tool. The routing function in
`graph.py` (`make_router`), not the decision policy, decides whether a
requested call to it goes straight to `tool_execution` or detours through
`human_approval` -- based on the tool's registered `risk` level and
`state["approved"]`/`config.auto_approve_high_risk`. This keeps the
approval boundary a structural property of the graph that no prompt change
or model behavior can bypass.

`human_approval` (`nodes/approval.py`) calls LangGraph's `interrupt()`,
which genuinely suspends the graph. The API layer surfaces the interrupt
payload as `{"status": "awaiting_approval", "approval": {...}}` and resumes
later via `POST /api/v1/triage/{request_id}/approve`, which calls
`graph.invoke(Command(resume={"approved": ...}), config=thread_config)` on
the same `thread_id` (a `MemorySaver` checkpointer keys state by
`thread_id == request_id`).

## The three (really four) demonstrated diagnostic paths

Driven by fixtures in `agent/src/agent/tools/*.py`, not by branching in the
API layer:

| customer_id | path |
|---|---|
| `123` (default) | fault found -> `create_fault_ticket` (pauses for approval unless `AUTO_APPROVE_HIGH_RISK=true`) |
| `456` | known outage -> stops immediately, **never** calls diagnostics |
| `789` | healthy diagnostics -> troubleshooting guidance, no ticket |
| `123` + reject approval | escalated instead of ticket creation |

`tests/test_graph.py::test_three_distinct_diagnostic_paths_are_possible`
asserts these produce three distinct tool-call sequences from the same
policy code.

## final_response is templated, not another LLM call

`nodes/response.py` fills a small set of fixed templates using only values
read back out of `state["tool_results"]` (region, diagnosis, ticket_id).
This is a deliberate simplification for the POC: it guarantees the
customer-facing text can never state a fact that wasn't actually observed,
at the cost of natural-language variety. A production version could still
use an LLM call here as long as it's constrained to the same evidence and
audited the same way.

## Extension points

- New enterprise tool: add a `tools/<name>.py` with a pydantic input schema
  (set `model_config = ConfigDict(title="<tool_name>")` so Bedrock's
  function-calling name matches the registry key) and a
  `register_<name>_tool(registry)` function, then add it to the tuple in
  `tools/registry.py::build_default_registry`.
- New resolution/response template: add a case to `_TEMPLATES` in
  `nodes/response.py` and teach the relevant `DecisionPolicy` to emit it.
