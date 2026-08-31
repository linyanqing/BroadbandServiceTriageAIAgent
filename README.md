# Broadband Service Triage AI Agent (POC)

A production-style proof of concept for an enterprise AI agent that triages
broadband service issues: LangGraph orchestration around a ReAct-style
loop (perception -> reasoning -> tool/action -> observation -> reasoning...),
Amazon Bedrock as the model provider, five deterministic mocked enterprise
tools, LangSmith for AI-specific tracing, OpenTelemetry for enterprise
telemetry, and a human-in-the-loop boundary for the one high-risk action
(creating a fault ticket).

This is **not** a generic chatbot -- the LLM only ever chooses from a fixed,
enforced set of tools, and the actual diagnostic path is decided at runtime
from what each tool call observes, not hard-coded. See
[docs/agent-design.md](docs/agent-design.md).

## Quickstart

```bash
cd agent
uv sync
cp .env.example .env
uv run uvicorn agent.main:app --reload
```

```bash
curl -X POST localhost:8000/api/v1/triage \
  -H 'Content-Type: application/json' \
  -d '{"customer_id": "123", "message": "My broadband keeps dropping out today"}'
```

Runs entirely locally with `MOCK_MODE=true` (the default) -- no AWS account
needed. See [docs/deployment.md](docs/deployment.md) for the full local
run instructions (including the OTel Collector demo stack), the AWS ECS
Fargate deployment via Terraform, and running the test suite.

## Project layout

```
agent/    Python 3.12 / LangGraph agent + FastAPI API (uv-managed)
infra/    Terraform: ECS Fargate, ALB, IAM, Secrets Manager, CloudWatch
docs/     Architecture, agent design, observability, security, deployment
docker/   Dockerfile + local docker-compose (agent + OTel Collector demo)
```

## Documentation

- [docs/architecture.md](docs/architecture.md) -- context/logical/deployment
  diagrams, state graph, sequence diagram
- [docs/agent-design.md](docs/agent-design.md) -- state, decision policies,
  the ReAct loop, human-approval routing, the four demonstrated diagnostic
  paths
- [docs/observability.md](docs/observability.md) -- LangSmith vs
  OpenTelemetry vs CloudWatch, and why they're kept separate
- [docs/security.md](docs/security.md) -- tool authorization, prompt
  injection resistance, PII redaction, secrets
- [docs/deployment.md](docs/deployment.md) -- local dev, tests, AWS deploy,
  known limitations

## Environment variables

See [`agent/.env.example`](agent/.env.example) for the full list
(`MOCK_MODE`, `BEDROCK_MODEL_ID`, `AUTO_APPROVE_HIGH_RISK`,
`LANGCHAIN_TRACING_V2`/`LANGCHAIN_PROJECT`, `OTEL_ENABLED`/
`OTEL_EXPORTER_OTLP_ENDPOINT`). Nothing here has a hard-coded secret or
Bedrock model default.

## Test results

45/45 passing (`cd agent && uv run pytest -q`), covering dynamic tool
selection, the ReAct loop, the four diagnostic paths, tool authorization
(including a prompt-injection attempt), PII redaction, OTel/LangSmith
toggling, and the health/ready endpoints.

## Known limitations

This environment had no AWS credentials, so Bedrock, LangSmith, the
Cribl/Datadog/Splunk pipeline, and `terraform apply` were not exercised
live -- see [docs/architecture.md#known-limitations](docs/architecture.md#known-limitations)
and [docs/deployment.md#known-limitations](docs/deployment.md#known-limitations)
for exactly what was and wasn't validated, and
[docs/deployment.md](docs/deployment.md#known-limitations) for the
in-memory checkpointer caveat for multi-task deployments.
