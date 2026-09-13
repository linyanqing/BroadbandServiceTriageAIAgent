# Agent Design

## Why a hybrid multi-agent system, not a free-form chatbot

LangGraph owns the *structure* at two levels: a top-level supervisor that
picks which specialist investigates, and each specialist's own bounded
ReAct loop over its own scoped tools. The decision policy plugged into a
node owns the *reasoning* -- the supervisor's `OrchestratorPolicy` decides
which specialist to dispatch, and each specialist's own `DecisionPolicy`
decides its next tool call. Neither can route the graph anywhere it doesn't
already allow, and neither can call anything outside its own scoped tool
registry -- see [security.md](security.md) for how that's enforced.

## Why multi-agent, and how specialists share one implementation

The original design was a single flat ReAct loop over five tools, all
scoped to network diagnostics. It's now a **supervisor pattern**: a
top-level orchestrator dispatches one specialist agent per turn --
**network_diagnose**, **billing**, **line_testing**, or
**equipment_reset** -- each with its own tools, and loops back to the
supervisor after each one, so a single investigation can chain more than
one specialist (e.g. a technical fault *and* a billing dispute raised in
the same message).

Every specialist shares one generic subgraph shape
(`specialist_graph.py::build_specialist_subgraph`) -- `agent_decision ->
route -> tool_execution -> observation -> (loop back, or human_approval ->
loop back) -> specialist_exit` -- reusing the *exact same* node factories
the original single-agent design already had
(`nodes/decision.py`, `nodes/tool_execution.py`, `nodes/observation.py`,
`nodes/approval.py`). The only genuinely new code per specialist is its own
small `DecisionPolicy` (`models/mock_specialists.py`, or `models/bedrock.py`
reused unmodified with a different scoped registry/resolutions/prompt) and
its scoped `ToolRegistry` (`tools/registry.py`'s `SPECIALIST_TOOL_SCOPES`).

## Topology

```
perception -> context_gathering -> supervisor
                                       |
                    (make_supervisor_router: conditional edge)
                                       |
   +---------------+---------------+---------------+---------------+
   v               v               v               v               v
 network_        billing_agent  line_testing_  equipment_reset_  final_response
 diagnose_agent  (subgraph)     agent          agent (subgraph)
 (subgraph)                     (subgraph)
   |               |               |               |
   +---------------+-------+-------+---------------+
                           v
                       supervisor  (loop back)
```

- **`context_gathering`** (`nodes/context_gathering.py`) always runs
  `get_customer` then `get_broadband_service` before any specialist is
  picked -- deliberately a **plain node, not a ReAct subgraph**: the
  sequence is fixed and unconditional, with no possible approval branch and
  no policy decision needed even in principle.
- **`supervisor`** (`nodes/supervisor.py`) wraps an `OrchestratorPolicy`
  (`models/orchestrator_policy.py`) that reads `specialist_results`/
  `issue_type`/`billing_flagged` and either dispatches one specialist or
  finishes.
- **Each specialist** is a `CompiledStateGraph` added as a node on the top
  graph, compiled **without its own checkpointer** -- the parent's
  `MemorySaver` (`graph.py`) governs persistence/interrupts across the
  whole investigation, including `interrupt()`s raised from inside a
  specialist.
- **Single dispatch per supervisor turn**, looping back rather than
  fanning out to all four in parallel -- this is what lets the four
  originally-documented diagnostic paths (below) resolve via
  `network_diagnose` alone, unaffected by the other three specialists
  existing, while still supporting a chained, multi-specialist
  investigation through the loop.

## State (`agent/src/agent/state.py`)

Two decision levels share one state object. `current_action` is
specialist-local (never read at the top level); `orchestrator_action` is
the supervisor's own in-progress decision. `specialist_history` and
`specialist_results` (both `Annotated[list, operator.add]`) accumulate
across the whole investigation, so the supervisor can see what's already
run and `final_response` can ground its answer in the last specialist's
actual result. `observations`/`tool_calls`/`tool_results` stay **global**
across every specialist -- deliberately: it lets a later specialist see an
earlier one's observations for free, and lets `nodes/response.py`'s tool-
result lookups extend to new tools without new `TriageResponse` fields.

`iteration_count` (a specialist's own tool-call budget, bounded by
`config.max_iterations`) and the transient approval flags
(`approved`/`approval_rejected`/`requires_human_approval`) are reset to a
clean slate in `specialist_exit` every time a specialist finishes, so the
*next* dispatched specialist never inherits stale state from the last one.
`specialist_dispatch_count` is the supervisor-level analogue, bounded by
`config.max_specialist_dispatches`.

## Tool-authorization scoping

One shared master `ToolRegistry` (`tools/registry.py::build_default_registry`)
stays the single source of truth for every tool's schema/risk/handler.
Scoping to a specialist is a **registry subset view**, not new logic in
`security/authorization.py`:

- `ToolRegistry.subset(names)` builds a registry referencing the *same*
  `ToolSpec` objects for only the given names.
- `SPECIALIST_TOOL_SCOPES` maps each specialist (plus `"core"`, for
  `context_gathering`) to its allowed tool names.
- `graph.py::build_graph` builds one scoped registry per specialist and
  passes it into that specialist's node factories. `authorize_tool_call`
  is completely untouched -- it transparently enforces the boundary
  because a scoped registry simply doesn't contain another specialist's
  tools. `BedrockDecisionPolicy` needs no changes either: it already only
  ever binds `registry.all()`, so a scoped registry restricts what the
  model can even name.
- The supervisor's router (`graph.py::make_supervisor_router`)
  independently re-validates the dispatched specialist name against a
  fixed literal set before using it to pick an edge, and refuses to
  dispatch the same specialist twice -- the same defensive pattern
  `make_router`/`specialist_exit` already apply to an unregistered tool
  name, one level up.

`tests/test_graph.py::test_specialist_cannot_reach_another_specialists_tool_even_via_a_rogue_policy`
proves this structurally, the same way
`tests/test_security.py::test_authorize_unknown_tool_rejected` proves the
original single-registry boundary.

## Each specialist's decision policy

- `MockNetworkDiagnosePolicy` (`mock_policy.py`), `MockBillingPolicy`,
  `MockLineTestingPolicy`, `MockEquipmentResetPolicy`
  (`mock_specialists.py`) -- small rule-based policies used whenever
  `MOCK_MODE=true` (the default). None of these is a fixed call sequence:
  every branch reads only `state["tool_results"]` (via the shared helpers
  in `models/policy.py`) and reacts to what's actually there.
- `BedrockDecisionPolicy` (`bedrock.py`) -- the **same class**, reused
  unmodified, for all four specialists; what differs per instance is its
  scoped registry, its `resolutions` list (so its `finish` tool only ever
  offers that specialist's own valid outcomes), and its system prompt
  (`prompts/system_prompt.py` -- one shared `_BASE_RULES` block covering
  tool-allow-list/prompt-injection/no-repeat-calls/grounding, plus a short
  specialist-specific completion-rules tail).
- The **orchestrator** has its own policy pair one level up:
  `MockOrchestratorPolicy` (`mock_orchestrator.py`) and
  `BedrockOrchestratorPolicy` (`bedrock_orchestrator.py`, binding a
  synthetic `dispatch` tool naming exactly the four specialists, plus
  `finish`).

All policies at both levels produce identical output shapes for their
level, so the rest of the graph is completely agnostic to which one is
running.

## Why the graph, not the policy, decides high-risk routing

`create_fault_ticket`, `schedule_technician_visit`, and
`trigger_equipment_reset` are unconditionally high-risk.
`apply_billing_credit` is **conditionally** high-risk: `ToolSpec.risk_override`
(a `Callable[[dict], RiskLevel]`) lets a tool's effective risk depend on its
actual call arguments -- here, credits under
`tools/billing.py::AUTO_APPROVE_CREDIT_THRESHOLD_USD` ($10) auto-approve,
larger ones require a human. A missing/malformed `amount` fails safe to
`"high"`. `tools/registry.py::effective_risk(spec, tool_input)` is what both
`nodes/decision.py` (sets `requires_human_approval`) and
`specialist_graph.py::make_specialist_router` (routes to `human_approval`
vs. `tool_execution`) actually call -- neither the decision policy nor the
router trusts a tool's static `.risk` alone. This keeps the approval
boundary a structural property of the graph that no prompt change or model
behavior can bypass, now generalized to a threshold rather than just a
fixed classification.

`human_approval` (`nodes/approval.py`) calls LangGraph's `interrupt()`,
which genuinely suspends the graph -- correctly, even from inside a nested
specialist subgraph (see "Interrupt replay and idempotency" below). The API
layer surfaces the interrupt payload as `{"status": "awaiting_approval",
"approval": {...}}` and resumes later via `POST
/api/v1/triage/{request_id}/approve`, which calls
`graph.invoke(Command(resume={"approved": ...}), config=thread_config)` on
the same `thread_id` (a `MemorySaver` checkpointer keys state by
`thread_id == request_id`).

## Interrupt replay and idempotency

Empirically confirmed while building this multi-agent version, and worth
understanding before adding a fifth specialist: **once any specialist
subgraph actually executes within a single `graph.invoke()` call --
whether or not a `human_approval` interrupt is involved -- whatever plain
nodes (`perception`, `context_gathering`, `supervisor`) or already-completed
specialist subgraphs ran earlier in that same call get re-invoked for
real.** This isn't about `interrupt()`/resume specifically (a completely
uninterrupted single-specialist investigation shows it too) -- the trigger
is a nested subgraph actually running, full stop; a graph with no subgraphs
at all (or an unexecuted one) doesn't exhibit it. Root cause not fully
pinned down (suspected LangGraph checkpoint-boundary/task-tracking
interaction with nested subgraphs), but the trigger condition itself is
solidly reproduced (see the commit history / PR description for the
isolated repro cases).

Three structural guards neutralize this, one per kind of state a replay can
disturb:

1. **Tool handler calls** (`nodes/tool_execution.py::_already_executed`):
   before calling a tool's real handler, checks whether `state["tool_results"]`
   already has a successful entry for the exact same `(tool_name,
   tool_input)`, and reuses that cached output instead of calling the
   handler again. This is what actually stops a real backend integration
   (billing, equipment reset, ...) from being called twice -- the mock
   handlers here happen to be pure functions, so a duplicate call would be
   harmless anyway, but a live integration would not be.
   `tests/test_graph.py::test_tool_handler_never_executes_twice_across_a_resumed_interrupt`
   pins this down with an instrumented handler.
2. **List accumulation itself** (`state.py::dedup_add`): every
   `Annotated[list, ...]` field that accumulates across an investigation
   (`observations`, `tool_calls`, `tool_results`, `specialist_history`,
   `specialist_results`) uses a dedup-aware reducer instead of plain
   `operator.add` -- an entry already present (by value equality) is never
   appended a second time. This is safe specifically because none of these
   lists can have a *legitimate* duplicate: guard 1 above already makes a
   replayed tool call return byte-identical output, and the supervisor's
   router structurally refuses to dispatch the same specialist twice, so
   any repeated entry is unambiguously a replay artifact. This is also
   what makes guard 3 below actually work.
3. **Decision calls themselves** (`models/decision_cache.py`, used by
   `nodes/decision.py`'s `agent_decision` and `nodes/supervisor.py`'s
   `supervisor`): before calling `policy.decide()`, computes a hash of the
   input state that decision reads (customer/service id, observations,
   tool results, or -- one level up -- issue type, specialist history and
   results) and checks `state["decision_cache"]` /
   `state["orchestrator_decision_cache"]` for an entry under that key,
   reusing it instead of re-invoking the policy. Guard 1 alone stops a
   handler from double-firing, but says nothing about the *decision* that
   led to it: a deterministic mock policy recomputes the same answer for
   the same input regardless, but a live LLM re-asked the exact same
   question in a second, independent call is not guaranteed to answer it
   the same way twice. Confirmed live before this guard existed: in a
   chained network_diagnose -> billing investigation with two approvals,
   after the billing approval resumed, a replayed supervisor decision led
   the live orchestrator to dispatch a *third*, unintended specialist
   (`equipment_reset`) -- and because `final_response` reads
   `specialist_results[-1]`, the customer-facing result silently ended up
   describing that unintended check instead of the correct billing
   resolution, purely as an artifact of how many times a replay happened
   to re-ask the orchestrator. Guard 2 is a prerequisite for this guard,
   not a separate concern: without dedup, a replayed upstream node would
   grow `specialist_history`/`tool_results` before the downstream decision
   node re-runs, changing its cache key on every replay and defeating the
   cache entirely -- this was tried and confirmed not to work on its own.
   `tests/test_decision_cache.py` proves both cache layers reuse a prior
   answer rather than re-invoking a (simulated-flaky) policy for identical
   state; re-run against the live deployment after this fix landed, the
   same chained scenario now consistently reaches the correct final
   resolution across repeated runs (see
   [architecture.md#known-limitations](architecture.md#known-limitations)).

## The demonstrated diagnostic paths

Driven by fixtures in `agent/src/agent/tools/*.py`, not by branching in the
API layer. The original four (still exercised via `network_diagnose` alone,
byte-for-byte the same resolutions/status/ticket_id as before this
redesign):

| customer_id | path |
|---|---|
| `123` (default) | fault found -> `create_fault_ticket` (pauses for approval unless `AUTO_APPROVE_HIGH_RISK=true`) |
| `456` | known outage -> stops immediately, **never** calls diagnostics |
| `789` | healthy diagnostics -> troubleshooting guidance, no ticket |
| `123` + reject approval | escalated instead of ticket creation |

Three more, one per new specialist:

| customer_id | path |
|---|---|
| `111` | overdue balance -> `apply_billing_credit` (pauses for approval when the credit is >= $10) |
| `222` | line fault detected -> `schedule_technician_visit` (pauses for approval) |
| `333` | stale equipment -> `trigger_equipment_reset` (pauses for approval; also the second worked example, after equipment reset, of a rejected approval) |

And a chained one, proving multi-specialist dispatch is real and not just
a single dispatch dressed up as multi-agent: a message combining a
technical complaint with a billing one (e.g. customer `111`, "my broadband
keeps dropping out and I think I've been overcharged") dispatches
`network_diagnose` first (perception's keyword priority puts technical
signals ahead of billing ones for the *primary* `issue_type`), then chains
into `billing` via the independent `billing_flagged` signal once
`network_diagnose` completes.

`tests/test_graph.py::test_three_distinct_diagnostic_paths_are_possible`
and `test_chained_investigation_dispatches_network_diagnose_then_billing`
assert these.

## final_response is templated, not another LLM call

`nodes/response.py` fills a small set of fixed templates using only values
read back out of `state["tool_results"]` (region, diagnosis, ticket_id,
overdue amount, credit/visit/reset id, ...) and the **resolution itself**
read back out of `specialist_results[-1]` -- never trusted straight from
`orchestrator_action`, even if a policy set one there. This is a deliberate
simplification for the POC: it guarantees the customer-facing text can
never state a fact -- or a resolution -- that no specialist actually
observed, at the cost of natural-language variety. A production version
could still use an LLM call here as long as it's constrained to the same
evidence and audited the same way.

## Extension points

- New enterprise tool: add a `tools/<name>.py` with a pydantic input schema
  (set `model_config = ConfigDict(title="<tool_name>")` so Bedrock's
  function-calling name matches the registry key) and a
  `register_<name>_tool(registry)` function, add it to the tuple in
  `tools/registry.py::build_default_registry`, and add it to the relevant
  specialist's entry in `SPECIALIST_TOOL_SCOPES`. Give it `risk_override`
  instead of a fixed `risk` if whether it needs approval depends on its
  arguments (see `apply_billing_credit`).
- New resolution/response template: add a case to `_TEMPLATES` in
  `nodes/response.py`, add the literal to `state.py`'s `Resolution`, and
  teach the relevant specialist's `DecisionPolicy` (and, for Bedrock, its
  `resolutions` list in `graph.py::_SPECIALIST_BEDROCK_CONFIG`) to emit it.
- New specialist: add its tools (above), a mock policy in
  `mock_specialists.py`, an entry in `graph.py::_SPECIALIST_BEDROCK_CONFIG`
  and `_SPECIALIST_NAMES`, a completion-rules prompt tail in
  `prompts/system_prompt.py`, and teach `MockOrchestratorPolicy` /
  `SUPERVISOR_PROMPT` when to dispatch to it. Everything else (the ReAct
  loop, the approval boundary, the tool-scoping enforcement) comes for free
  from `specialist_graph.py::build_specialist_subgraph`.
