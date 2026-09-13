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
needed) and `"789"` (healthy diagnostics, guidance instead of a ticket) for
the other network-diagnostics paths, or `"111"`/`"222"`/`"333"` to dispatch
the billing/line-testing/equipment-reset specialists instead -- see
[agent-design.md](agent-design.md#the-demonstrated-diagnostic-paths) for
the full table. Set `AUTO_APPROVE_HIGH_RISK=true` to skip every approval
pause for a faster end-to-end demo.

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
uv run pytest -q      # 80 tests: multi-agent graph dynamism (supervisor
                       # dispatch + each specialist's ReAct loop), per-
                       # specialist tool scoping, the seven diagnostic
                       # paths, security, redaction, OTel/LangSmith
                       # toggles, health endpoints
uv run ruff check .
```

## AWS deployment (ECS Fargate)

```
Internet -> ALB -> ECS Fargate service
                     ├── agent container   (this repo, docker/Dockerfile)
                     └── adot-collector sidecar -> Cribl -> Datadog/Splunk
```

Validated end-to-end against a live account (`ap-southeast-2`, default VPC,
no NAT gateway) -- see [README.md#deploying-to-aws-ecs-fargate](../README.md#deploying-to-aws-ecs-fargate)
for the exact, tested command sequence including the ECR bootstrap and the
default-VPC (`assign_public_ip`) workaround. Summary:

0. **Bootstrap the ECR repo first** (`terraform apply -target=module.ecr`)
   -- the ECS service needs an image at the referenced tag to exist before
   the rest of the stack can come up.

1. **Build and push the image** (for `linux/amd64`, Fargate's default
   platform):

   ```bash
   aws ecr get-login-password --region <region> | \
     docker login --username AWS --password-stdin <account>.dkr.ecr.<region>.amazonaws.com
   docker buildx build --platform linux/amd64 -f docker/Dockerfile \
     -t <account>.dkr.ecr.<region>.amazonaws.com/broadband-triage-agent:latest --load .
   docker push <account>.dkr.ecr.<region>.amazonaws.com/broadband-triage-agent:latest
   ```

2. **Provision the rest of the infrastructure**:

   ```bash
   cd infra
   cp environments/dev.tfvars.example environments/dev.tfvars   # fill in real values, never commit
   terraform init
   terraform plan  -var-file=environments/dev.tfvars
   terraform apply -var-file=environments/dev.tfvars
   ```

   `infra/` provisions: an ECR repository, an ECS cluster, task definition
   (agent + ADOT collector sidecar), Fargate service, ALB + target group +
   listener, the task execution/task IAM roles (least-privilege, see
   [security.md](security.md) -- the task role's Bedrock statement is
   derived via the `aws_bedrock_inference_profile` data source so it covers
   both the profile ARN and its underlying per-region model ARNs), a
   Secrets Manager placeholder for the LangSmith API key, a CloudWatch log
   group, and two CloudWatch alarms (ALB 5xx, ECS CPU).

   If this is the account's first-ever ECS deployment, the apply can fail
   once with `AccessDenied ... AssumeRole ... AWSServiceRoleForECS` (a
   service-linked role ECS normally self-creates but occasionally races
   with) -- `aws iam create-service-linked-role --aws-service-name
   ecs.amazonaws.com` (harmless if it already exists) and re-apply.

3. **Populate the LangSmith secret** (Terraform only creates the name, never
   a value -- the ECS task fails to start with `ResourceInitializationError`
   until this has *some* version, even an empty string):

   ```bash
   aws secretsmanager put-secret-value \
     --secret-id broadband-triage-agent/dev/langchain-api-key \
     --secret-string '<your LangSmith API key, or "" if leaving tracing off>' \
     --region <region>
   ```

   **If tracing then returns `403 Forbidden` on every request**: your
   LangSmith account may be on a non-default regional deployment (e.g.
   APAC) -- a key only authenticates against the region it was issued in.
   Confirm with `curl -H "x-api-key: $KEY"
   https://apac.api.smith.langchain.com/api/v1/sessions?limit=1` (swap the
   subdomain), then set `langsmith_endpoint` in `dev.tfvars` to that host
   and re-apply. See
   [architecture.md#known-limitations](architecture.md#known-limitations)
   for how this was root-caused.

4. **Verify**: `curl http://$(terraform output -raw alb_dns_name)/health` --
   and if the task doesn't reach `RUNNING`, `aws logs tail
   /ecs/broadband-triage-agent-dev --region <region> --since 5m` has the
   real exception (the API only ever returns a generic 500 to callers).

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
- **Default VPC / no NAT gateway**: the tested deployment reused the
  account's default VPC's public subnets for `private_subnet_ids` with
  `assign_public_ip = true`, since there was no NAT gateway available. A
  real private-subnet-with-NAT topology (`assign_public_ip = false`, the
  module's default) is untested here, though it's a standard pattern the
  module supports.
- **Cribl not exercised against a real endpoint**: no real Cribl instance
  in a personal account; see
  [architecture.md#known-limitations](architecture.md#known-limitations).
  (LangSmith tracing, by contrast, is now confirmed working end-to-end --
  see the regional-endpoint note in step 3 above.)
