# Security

## Identity & secrets

- AWS resources are accessed via IAM roles only -- no embedded credentials
  anywhere in the codebase or container image. `infra/modules/iam` creates a
  separate **task execution role** (pull image, write CloudWatch logs, read
  the named Secrets Manager secrets) and **task role** (what the running
  application may call: `bedrock:InvokeModel`/`InvokeModelWithResponseStream`,
  scoped to the single configured `bedrock_model_id`, nothing broader).
- Local development uses a `.env` file (see `.env.example`, `.env` itself is
  gitignored). Deployed environments resolve `LANGCHAIN_API_KEY` from AWS
  Secrets Manager (`infra/modules/secrets`) via the ECS task definition's
  `secrets` block -- Terraform creates the secret's *name*, never its value.

## Tool authorization -- the actual enforcement boundary

The system prompts (`prompts/system_prompt.py`) tell each specialist's model
not to invent tools, but **prompt wording is not what stops it**. Three
structural mechanisms do -- the third is new with the multi-agent redesign
(see [agent-design.md](agent-design.md)):

1. **Tool binding**: a specialist's `BedrockDecisionPolicy` only ever offers
   the model the tool specs built from *its own scoped* `ToolRegistry` plus
   one `finish` control action (`models/bedrock.py::build_tool_specs_for_llm`)
   -- it has no way to name anything else in a tool call, including another
   specialist's tools.
2. **Execution-time allow-list check**: `nodes/tool_execution.py` calls
   `security/authorization.py::authorize_tool_call` for *every* tool call
   before anything runs, regardless of which policy produced it, against
   that same scoped registry. An unregistered (or out-of-scope) name never
   reaches a handler -- it comes back as an error observation, and the
   specialist's own router independently treats it as a routing signal to
   exit with `escalated_invalid_action`
   (`specialist_graph.py::make_specialist_router`,
   `nodes/specialist_exit.py`).
3. **Per-specialist registry scoping**: `tools/registry.py::ToolRegistry.subset()`
   builds a registry referencing only a specialist's own tool names
   (`SPECIALIST_TOOL_SCOPES`) -- `security/authorization.py` itself is
   unchanged and unaware scoping exists at all; the boundary is which
   registry instance a given specialist's nodes were built with. The
   supervisor's own router applies the same "never trust a name blindly"
   principle one level up, independently re-validating a dispatched
   specialist name against a fixed literal set before routing to it.

`tests/test_security.py::test_prompt_injection_in_customer_message_cannot_widen_tool_access`
runs a customer message that explicitly tries to instruct the agent to call
tools that don't exist, and asserts the tool-call set stays within the
registered eleven; `tests/test_graph.py::test_specialist_cannot_reach_another_specialists_tool_even_via_a_rogue_policy`
proves the scoping boundary specifically -- a policy attached to
`network_diagnose` naming `billing`'s own `apply_billing_credit` tool fails
exactly like an invented name would.

## Human-in-the-loop for high-risk actions

Four tools are gated: `create_fault_ticket`, `schedule_technician_visit`,
and `trigger_equipment_reset` are unconditionally `risk="high"`;
`apply_billing_credit` is *conditionally* high-risk via `ToolSpec.risk_override`
(`tools/billing.py`) -- credits under `AUTO_APPROVE_CREDIT_THRESHOLD_USD`
($10) auto-approve, larger ones don't, and a missing/malformed amount fails
safe to high-risk. Each specialist's own router (not the LLM, not the mock
policy) decides whether a call proceeds automatically or pauses at
`human_approval`, using `tools/registry.py::effective_risk(spec, tool_input)`
rather than trusting a tool's static `.risk` alone -- see
[agent-design.md](agent-design.md#why-the-graph-not-the-policy-decides-high-risk-routing).
`AUTO_APPROVE_HIGH_RISK` is an explicit, environment-level policy choice,
not something the model can set for itself, and applies uniformly across
every specialist.

## Input validation

Every tool's input is validated against its pydantic schema
(`tools/*.py`) before the handler runs -- a call missing or mistyping a
required field raises `ValidationError`, caught in `tool_execution` and
surfaced as an error observation rather than a crash. The FastAPI request
bodies (`TriageRequest`, `ApprovalRequest`) are pydantic models as well.

## PII handling & data protection

`security/redaction.py`:

- `customer_id` and `service_id` are treated as **internal correlation
  identifiers**, not direct PII, and are kept in LangSmith metadata and
  logs -- without them, a support engineer cannot correlate a LangSmith run,
  a CloudWatch log line, and a Datadog/Splunk trace back to the same
  customer request at all.
- Names, phone numbers, emails, and addresses (`_PII_KEYS`, plus regex
  matching for emails/phone-like patterns in free text) are always masked
  (`***REDACTED***`) before reaching LangSmith metadata
  (`observability/langsmith.py::build_run_config`) or the approval payload
  returned from `POST /api/v1/triage` (`main.py::_extract_result`).
- The raw `customer_message` is **never** forwarded into LangSmith metadata
  or into the redacted state summary sent to Bedrock
  (`models/bedrock.py::build_decision_messages`) -- only the already-derived
  `issue_type`/`confidence` and tool observations are, further reducing
  what any given LLM call or trace payload is exposed to.

This is a POC-level redaction implementation (a fixed key list plus two
regexes) -- a production system handling real customer data would want a
more robust PII-detection library and a documented data-retention policy
for LangSmith/Datadog/Splunk.

## What's explicitly out of scope for this POC

- Authentication/authorization on the `/api/v1/triage` endpoint itself
  (would sit at the API Gateway / ALB layer, or as FastAPI middleware, in a
  production deployment).
- Rate limiting / abuse protection.
- A persistent (non-`MemorySaver`) checkpointer -- see
  [deployment.md](deployment.md#known-limitations).
