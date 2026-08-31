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

The system prompt (`prompts/system_prompt.py`) tells the model not to invent
tools, but **prompt wording is not what stops it**. Two structural
mechanisms do:

1. **Tool binding**: `BedrockDecisionPolicy` only ever offers the model the
   tool specs built from `ToolRegistry` plus one `finish` control action
   (`models/bedrock.py::build_tool_specs_for_llm`) -- it has no way to name
   anything else in a tool call.
2. **Execution-time allow-list check**: `nodes/tool_execution.py` calls
   `security/authorization.py::authorize_tool_call` for *every* tool call
   before anything runs, regardless of which policy produced it. An
   unregistered name never reaches a handler -- it comes back as an error
   observation, and the graph's router independently treats an unregistered
   tool name as a routing signal to escalate (`graph.py::make_router`,
   `nodes/response.py`'s `escalated_invalid_action` resolution).

`tests/test_security.py::test_prompt_injection_in_customer_message_cannot_widen_tool_access`
runs a customer message that explicitly tries to instruct the agent to call
tools that don't exist, and asserts the tool-call set stays within the
registered five.

## Human-in-the-loop for high-risk actions

`create_fault_ticket` is registered with `risk="high"`
(`tools/fault.py`). The graph's router (not the LLM, not the mock policy)
decides whether a call to it proceeds automatically or pauses at
`human_approval` -- see [agent-design.md](agent-design.md#why-the-graph-not-the-policy-decides-high-risk-routing).
`AUTO_APPROVE_HIGH_RISK` is an explicit, environment-level policy choice,
not something the model can set for itself.

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
