# Deployment

## Local development

No AWS infrastructure required.

```bash
cd agent
uv sync
cp .env.example .env   # defaults already work: MOCK_MODE=true
uv run uvicorn agent.main:app --reload
```

```bash
curl localhost:8000/health
curl localhost:8000/ready

curl -X POST localhost:8000/api/v1/triage \
  -H 'Content-Type: application/json' \
  -d '{"customer_id": "123", "message": "My broadband keeps dropping out today"}'
# -> {"status": "awaiting_approval", "approval": {"tool": "create_fault_ticket", ...}, ...}

curl -X POST localhost:8000/api/v1/triage/<request_id>/approve \
  -H 'Content-Type: application/json' -d '{"approved": true}'
# -> {"status": "resolved", "resolution": "fault_ticket_created", "ticket_id": "INC-...", ...}
```

Try `customer_id: "456"` (known outage, resolves immediately, no approval
needed) and `"789"` (healthy diagnostics, guidance instead of a ticket) to
see the other diagnostic paths. Set `AUTO_APPROVE_HIGH_RISK=true` to skip
the approval pause for a faster end-to-end demo of the fault-ticket path.

### With the local OTel Collector pipeline

```bash
docker compose -f docker/docker-compose.yml up --build
```

Runs the agent with `OTEL_ENABLED=true` against a local
`opentelemetry-collector-contrib` container that logs everything it
receives (`docker/otel-collector-config.yaml`) -- demonstrates the
OTLP pipeline without a real Cribl/Datadog/Splunk endpoint.

## Running tests

```bash
cd agent
uv run pytest -q      # 45 tests: graph dynamism, tool authorization,
                       # scenarios A-D, security, redaction, OTel/LangSmith
                       # toggles, health endpoints
uv run ruff check .
```

## AWS deployment (ECS Fargate)

```
Internet -> ALB -> ECS Fargate service
                     ├── agent container   (this repo, docker/Dockerfile)
                     └── adot-collector sidecar -> Cribl -> Datadog/Splunk
```

1. **Build and push the image**:

   ```bash
   aws ecr get-login-password --region <region> | \
     docker login --username AWS --password-stdin <account>.dkr.ecr.<region>.amazonaws.com
   docker build -f docker/Dockerfile -t <account>.dkr.ecr.<region>.amazonaws.com/broadband-triage-agent:latest .
   docker push <account>.dkr.ecr.<region>.amazonaws.com/broadband-triage-agent:latest
   ```

2. **Provision infrastructure**:

   ```bash
   cd infra
   cp environments/dev.tfvars.example environments/dev.tfvars   # fill in real values, never commit
   terraform init
   terraform plan  -var-file=environments/dev.tfvars
   terraform apply -var-file=environments/dev.tfvars
   ```

   `infra/` provisions: an ECS cluster, task definition (agent + ADOT
   collector sidecar), Fargate service, ALB + target group + listener, the
   task execution/task IAM roles (least-privilege, see
   [security.md](security.md)), a Secrets Manager placeholder for the
   LangSmith API key, a CloudWatch log group, and two CloudWatch alarms
   (ALB 5xx, ECS CPU).

3. **Populate the LangSmith secret** (Terraform only creates the name):

   ```bash
   aws secretsmanager put-secret-value \
     --secret-id broadband-triage-agent/dev/langchain-api-key \
     --secret-string '<your LangSmith API key>'
   ```

4. **Verify**: `curl http://$(terraform output -raw alb_dns_name)/health`

### API Gateway alternative

The spec allows API Gateway as the external entry point. This POC's
Terraform uses a plain ALB in front of ECS Fargate -- the more common,
simpler pattern for this shape of service. Fronting the ALB with API
Gateway (HTTP API + VPC Link) is a drop-in addition if request-level
throttling, API keys, or usage plans become a requirement; it wasn't built
here to avoid adding infrastructure the POC doesn't need.

## Known limitations

- **Checkpointer**: the graph uses LangGraph's in-memory `MemorySaver`
  (`graph.py`), so a paused (`awaiting_approval`) investigation is lost if
  the process restarts, and won't be shared across multiple ECS tasks. A
  production deployment with `desired_count > 1` needs a durable,
  shared checkpointer (e.g. a Postgres- or DynamoDB-backed one) before the
  human-approval pause/resume flow is safe to run behind a load balancer
  with more than one task.
- **Not applied**: `infra/` was validated with `terraform validate` and
  `terraform fmt` in this environment (no AWS credentials/VPC were
  available), not `terraform apply`'d against a real account.
- **Bedrock/LangSmith/Cribl not exercised live**: see
  [architecture.md#known-limitations](architecture.md#known-limitations).
