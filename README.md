# Broadband Service Triage AI Agent (POC)

A production-style proof of concept for an enterprise AI agent that triages
broadband service issues: LangGraph **multi-agent supervisor pattern** --
a top-level orchestrator dispatches one specialist per turn (network
diagnostics, billing, line testing, equipment reset), each running its own
bounded ReAct loop (perception -> reasoning -> tool/action -> observation ->
reasoning...) over its own scoped subset of eleven deterministic mocked
enterprise tools -- Amazon Bedrock as the model provider at both the
orchestrator and specialist levels, LangSmith for AI-specific tracing,
OpenTelemetry for enterprise telemetry, and a human-in-the-loop boundary
for every high-risk action (creating a fault ticket, applying a billing
credit above $10, scheduling a technician visit, resetting equipment).

This is **not** a generic chatbot -- at every level, the LLM only ever
chooses from a fixed, enforced set of options (which specialist to dispatch;
which of that specialist's own tools to call), and the actual diagnostic
path is decided at runtime from what's actually observed, not hard-coded.
See [docs/agent-design.md](docs/agent-design.md).

## Quickstart (local, mock mode -- no AWS account needed)

```bash
cd agent
uv sync
cp .env.example .env          # defaults already work: MOCK_MODE=true
uv run uvicorn agent.main:app --reload
```

```bash
curl -X POST localhost:8000/api/v1/triage \
  -H 'Content-Type: application/json' \
  -d '{"customer_id": "123", "message": "My broadband keeps dropping out today"}'
# -> {"status": "awaiting_approval", "approval": {"tool": "create_fault_ticket", ...}, ...}

curl -X POST localhost:8000/api/v1/triage/<request_id>/approve \
  -H 'Content-Type: application/json' -d '{"approved": true}'
# -> {"status": "resolved", "resolution": "fault_ticket_created", "ticket_id": "INC-...", ...}
```

With `MOCK_MODE=true` every decision step (the supervisor's dispatch choice,
and each specialist's own tool choice) uses a small deterministic policy
(`agent/src/agent/models/mock_orchestrator.py`,
`mock_policy.py`/`mock_specialists.py`) -- a stand-in for the LLM at both
levels that still drives the *real* LangGraph multi-agent graph, the real
per-specialist scoped tool registries, and the real authorization/approval
boundaries. Nothing is stubbed except the model calls and the
AI-observability exporters, so all seven diagnostic paths are fully
reproducible offline.

Try `customer_id: "456"` (known outage -- resolves immediately, no approval)
and `"789"` (healthy diagnostics -- guidance instead of a ticket) for the
original network-diagnostics paths; `"111"` (overdue billing -- credit
applied), `"222"` (line fault -- technician visit scheduled), and `"333"`
(stale equipment -- remote reset) for the three new specialists, each also
pausing for approval. Set `AUTO_APPROVE_HIGH_RISK=true` to skip every
approval pause for a faster end-to-end demo.

## Running against Amazon Bedrock

Setting `MOCK_MODE=false` swaps the mock policy for `BedrockDecisionPolicy`
(`agent/src/agent/models/bedrock.py`), which calls Bedrock through
`ChatBedrockConverse` with the tool specs built from the registry plus a
`finish` control action. Everything else is unchanged -- the five enterprise
tools stay deterministic in-process mocks in this POC, so no carrier backend
is required.

**1. AWS prerequisites (macOS setup)**

Install the AWS CLI v2 (needed for `aws configure`/`aws sso login` and the
`list-foundation-models`/`list-inference-profiles` calls in step 2):

```bash
brew install awscli
aws --version   # aws-cli/2.x
```

Get credentials onto the machine, via whichever your org uses:

- **IAM Identity Center / SSO (typical for a company AWS org)**:

  ```bash
  aws configure sso
  # SSO start URL and region come from your AWS admin/IT
  # follow the browser prompt to authenticate, then name the profile, e.g. "bedrock-dev"

  export AWS_PROFILE=bedrock-dev      # add to ~/.zshrc to persist across shells
  aws sso login                        # re-run whenever the session expires (usually ~8-12h)
  ```

  Tip: if this is the AWS account/profile you use most, `aws configure sso`
  can name the profile `default` instead -- then no `AWS_PROFILE` export is
  needed in any shell (see the "Making a profile the default" note below).

- **Long-lived IAM user access keys** (simpler for a personal/sandbox
  account, less safe for a shared one -- prefer SSO where it's available):

  ```bash
  aws configure --profile bedrock-dev
  # AWS Access Key ID / Secret Access Key: from IAM > Users > <you> > Security credentials
  # Default region: the region you'll enable Bedrock model access in

  export AWS_PROFILE=bedrock-dev     # add to ~/.zshrc to persist
  ```

Either way, boto3 (used under `ChatBedrockConverse`) picks this up
automatically from `~/.aws/credentials` / `~/.aws/config` -- the app itself
never reads or stores a key. Verify the identity before moving on:

```bash
aws sts get-caller-identity
```

**Making a profile the default** (optional, but saves exporting
`AWS_PROFILE` in every new shell): for an SSO profile, copy its
`sso_session`/`sso_account_id`/`sso_role_name`/`region` lines from
`~/.aws/config`'s `[profile <name>]` block into a `[default]` block in the
same file, and remove any static `aws_access_key_id`/`aws_secret_access_key`
pair under `[default]` in `~/.aws/credentials` (static keys there take
priority over SSO config for the same profile name and will shadow it).
`aws sts get-caller-identity` with no `--profile` flag confirms which one
actually wins.

Next, request **model access** -- this is a one-time, per-account,
per-region toggle separate from IAM permissions, and Bedrock calls fail with
`AccessDeniedException` until it's granted:

1. Open the [Bedrock console](https://console.aws.amazon.com/bedrock/) and
   switch to the region you configured above (top-right region selector).
2. Left sidebar -> **Model access** (or **Model catalog** in newer console
   layouts) -> **Modify model access** / **Enable specific models**.
3. Check the Anthropic model(s) you plan to use, submit, and wait for status
   to flip to **Access granted** (usually immediate; occasionally a few
   minutes).

Finally, attach an IAM policy to the identity from `aws configure`/`aws sso
configure` above (your account's IAM user, SSO permission set, or role) so
it's allowed to actually invoke the model:

```json
{
  "Effect": "Allow",
  "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
  "Resource": "arn:aws:bedrock:<region>::foundation-model/<model-id>"
}
```

If you use a cross-region inference profile, the policy needs the
inference-profile ARN *and* the underlying foundation-model ARNs in each
region the profile can route to. (`infra/modules/iam` grants the
foundation-model form for the deployed task role.) In an IAM Identity
Center setup this policy usually goes on the **permission set** (which your
AWS admin manages); for a personal account with an IAM user, attach it as an
inline or customer-managed policy directly on that user.

**Verify the setup** -- confirm your CLI identity can list Bedrock models
before touching the app:

```bash
aws bedrock list-foundation-models --region ap-southeast-2
```

If this returns a JSON list without an `AccessDeniedException`, IAM and
model access are both correctly configured.

**2. Pick a model ID**

There is no default model in this repo -- `BEDROCK_MODEL_ID` is required when
`MOCK_MODE=false`, and startup fails fast with a clear error if it's missing
(`agent/src/agent/config.py`). The model must support tool/function calling
via the Converse API. List what your account can actually invoke:

```bash
aws bedrock list-foundation-models --region <region> \
  --by-output-modality TEXT \
  --query 'modelSummaries[?contains(modelId, `anthropic`)].modelId'

aws bedrock list-inference-profiles --region <region> \
  --query 'inferenceProfileSummaries[].inferenceProfileId'
```

Bedrock model IDs are the Anthropic model name with an `anthropic.` prefix
(e.g. `anthropic.claude-opus-5`, `anthropic.claude-sonnet-5`); cross-region
inference profiles add a geo prefix (`us.`, `eu.`, `apac.`, `au.`, ...). Use
whichever of those two forms the commands above return for your account and
region -- **many current models are on-demand only through an inference
profile**; calling the bare foundation-model ID directly returns
`ValidationException: ... isn't supported. Retry your request with the ID or
ARN of an inference profile`. Confirmed working in `ap-southeast-2`:
`au.anthropic.claude-haiku-4-5-20251001-v1:0` (the bare
`anthropic.claude-haiku-4-5-20251001-v1:0` form fails with exactly that
error) -- always verify with the two commands above rather than assuming a
model ID from documentation still resolves the same way.

**3. Configure and run**

```bash
cd agent
cp .env.example .env
```

```ini
MOCK_MODE=false
BEDROCK_MODEL_ID=<id from step 2>
AWS_REGION=ap-southeast-2          # must match where model access was granted
```

```bash
uv run uvicorn agent.main:app --reload
```

**4. Verify**

```bash
curl localhost:8000/ready
# -> {"status": "ok", "mock_mode": false, "environment": "local"}

curl -X POST localhost:8000/api/v1/triage \
  -H 'Content-Type: application/json' \
  -d '{"customer_id": "123", "message": "My broadband keeps dropping out today"}'
```

The tool sequence is now chosen by the model at runtime rather than by the
mock policy, so the path may differ run to run -- but the model can still only
name tools in the registry: anything else is rejected as
`escalated_invalid_action` (see [docs/security.md](docs/security.md)).

**Optional -- LangSmith tracing.** Set `LANGCHAIN_TRACING_V2=true`,
`LANGCHAIN_PROJECT=...`, and `LANGCHAIN_API_KEY=...` (keep the key out of
version control; in AWS it comes from Secrets Manager) to send the reasoning
trace to LangSmith. It is independent of `MOCK_MODE`.

**If every request gets `403 Forbidden` from `api.smith.langchain.com`**
even with a valid, unrevoked Personal Access Token on an active plan: your
account may be hosted on a non-default **regional** LangSmith deployment
(e.g. APAC) -- a key only authenticates against the region it was issued
in, never the default US host, and the failure looks identical everywhere
(reads, writes, CLI, raw HTTP) regardless of key type or freshness. Confirm
with `curl -H "x-api-key: $KEY" https://apac.api.smith.langchain.com/api/v1/sessions?limit=1`
(swap the subdomain for your region), then set `LANGCHAIN_ENDPOINT` to that
host (`langsmith_endpoint` in Terraform -- see
[Deploying to AWS](#deploying-to-aws-ecs-fargate) below).

**Troubleshooting**

| Symptom | Cause |
| --- | --- |
| `BEDROCK_MODEL_ID must be set when MOCK_MODE=false` | `.env` still has the default (unset) model ID |
| `AccessDeniedException` | Model access not granted in this region, or the IAM policy doesn't cover the model ARN |
| `ValidationException` on invoke | Model doesn't support tool use, or must be called via an inference profile ID |
| `NoCredentialsError` / expired token | No credentials on the default boto3 chain -- `aws sso login` or export a profile |
| Works locally, fails on ECS | Task role scoped to a different `BEDROCK_MODEL_ID` than the container's env var |
| `UnrecognizedClientException: security token invalid` | Credentials resolved to a different, stale identity than expected -- run `aws sts get-caller-identity` with no `--profile` flag to see which one actually wins (see "Making a profile the default" above) |
| `AccessDeniedException: ... not authorized to perform: bedrock:InvokeModel on resource: ...:inference-profile/...` | The IAM policy only grants the foundation-model ARN form, not the inference-profile ARN + its underlying per-region model ARNs -- `infra/modules/iam` now derives both automatically via the `aws_bedrock_inference_profile` data source; a hand-written policy needs the same three ARNs (see step 1 above) |

## Deploying to AWS (ECS Fargate)

`infra/` provisions the full stack via Terraform: ECR, Secrets Manager, IAM,
an internet-facing ALB, and an ECS Fargate service running the agent
container + an ADOT Collector sidecar. Validated end-to-end (including the
IAM inference-profile fix above) against a live account in `ap-southeast-2`
using its **default VPC** (no NAT gateway) -- costs roughly $35-45/month
while running (ALB + Fargate + CloudWatch/Secrets/ECR; skipping a NAT
gateway saves ~$32/month). Tear down any time with `terraform destroy`.

**0. A container runtime.** If `docker` isn't installed (check `docker
version`), the lightest option on macOS is Colima rather than the full
Docker Desktop app:

```bash
brew install colima docker docker-buildx
colima start
# if buildx isn't picked up (a leftover Docker Desktop symlink can shadow it):
ls -la ~/.docker/cli-plugins/docker-buildx   # if "broken symbolic link":
rm ~/.docker/cli-plugins/docker-buildx
ln -s "$(brew --prefix docker-buildx)/bin/docker-buildx" ~/.docker/cli-plugins/docker-buildx
```

**1. Configure `dev.tfvars`:**

```bash
cd infra
cp environments/dev.tfvars.example environments/dev.tfvars   # gitignored, never commit
```

Fill in `vpc_id`/`public_subnet_ids` (`aws ec2 describe-vpcs`,
`describe-subnets`), the validated `bedrock_model_id` from the Bedrock
section above, and `container_image` (see step 3). **If your VPC has no
private subnets with a NAT gateway** (true of every default VPC), reuse the
public subnets for `private_subnet_ids` too and set `assign_public_ip =
true` -- otherwise Fargate tasks have no route to ECR/Bedrock/CloudWatch and
never start. There's no real Cribl endpoint in a personal account; leave
`cribl_otlp_endpoint` as an unreachable placeholder -- the ADOT sidecar just
logs export failures, harmlessly (CloudWatch logging is a separate,
unaffected path -- see [docs/observability.md](docs/observability.md)).

**2. Bootstrap the ECR repo first** (the ECS service needs an image to
exist at the tag it references, so create just the repo before anything
else):

```bash
terraform init
terraform apply -target=module.ecr -var-file=environments/dev.tfvars
terraform output ecr_repository_url
```

**3. Build (for `linux/amd64`, Fargate's default) and push:**

```bash
cd ..   # repo root -- the Dockerfile's build context
aws ecr get-login-password --region <region> | \
  docker login --username AWS --password-stdin <account>.dkr.ecr.<region>.amazonaws.com
docker buildx build --platform linux/amd64 -f docker/Dockerfile \
  -t <account>.dkr.ecr.<region>.amazonaws.com/broadband-triage-agent:latest --load .
docker push <account>.dkr.ecr.<region>.amazonaws.com/broadband-triage-agent:latest
```

Now set `container_image` in `dev.tfvars` to that same URI.

**4. Full apply:**

```bash
cd infra
terraform apply -var-file=environments/dev.tfvars
```

If this is the *first* ECS deployment ever in the account, the initial
apply can fail with `AccessDenied ... AssumeRole ... AWSServiceRoleForECS`
-- a one-time service-linked role that ECS normally creates for itself but
occasionally races with; run `aws iam create-service-linked-role
--aws-service-name ecs.amazonaws.com` (harmless no-op if it already exists)
and re-run `terraform apply`.

**5. Populate the LangSmith secret** (Terraform only creates the empty
secret container -- see `infra/modules/secrets`). The ECS task fails to
start (`ResourceInitializationError: ... can't find the specified secret
value`) until this has *some* version, even an empty one:

```bash
aws secretsmanager put-secret-value \
  --secret-id broadband-triage-agent/dev/langchain-api-key \
  --secret-string '<your LangSmith key, or "" to leave tracing effectively off>' \
  --region <region>
```

Get a key at [smith.langchain.com](https://smith.langchain.com) → Settings
→ API Keys. **Never paste a real key into a chat/terminal command another
party can read back** -- run this command yourself in your own shell. If
every request then gets `403 Forbidden`, see the regional-endpoint note
above -- also set `langsmith_endpoint` in `dev.tfvars` and re-apply.

**6. Verify:**

```bash
curl http://$(terraform output -raw alb_dns_name)/health
curl http://$(terraform output -raw alb_dns_name)/ready
curl -X POST http://$(terraform output -raw alb_dns_name)/api/v1/triage \
  -H 'Content-Type: application/json' \
  -d '{"customer_id": "123", "message": "My broadband keeps dropping out today"}'
```

If the task doesn't reach `RUNNING`, `aws logs tail
/ecs/broadband-triage-agent-dev --region <region> --since 5m` shows the
real exception (the API only ever returns a generic 500 to callers).

See [docs/deployment.md](docs/deployment.md) for the OTel Collector demo
stack, the API Gateway alternative, and the test suite.

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
- [docs/agent-design.md](docs/agent-design.md) -- the multi-agent supervisor
  pattern, state, decision policies, human-approval routing, the seven
  demonstrated diagnostic paths
- [docs/observability.md](docs/observability.md) -- LangSmith vs
  OpenTelemetry vs CloudWatch, and why they're kept separate
- [docs/security.md](docs/security.md) -- tool authorization, prompt
  injection resistance, PII redaction, secrets
- [docs/deployment.md](docs/deployment.md) -- local dev, tests, AWS deploy,
  CI/CD, known limitations
- [docs/developer-workflow.md](docs/developer-workflow.md) -- start-to-finish
  walkthrough: an IDE change -> local test -> PR/CI -> merge/CD -> live on
  ECS -> observing it (CloudWatch/LangSmith)

## Environment variables

See [`agent/.env.example`](agent/.env.example) for the full list. The ones
that decide how the agent runs:

| Variable | Default | Effect |
| --- | --- | --- |
| `MOCK_MODE` | `true` | `true` = deterministic local policy, no AWS. `false` = live Bedrock |
| `BEDROCK_MODEL_ID` | *(unset)* | Required when `MOCK_MODE=false`; no hard-coded default |
| `AWS_REGION` | `ap-southeast-2` | Region for the Bedrock call |
| `AUTO_APPROVE_HIGH_RISK` | `false` | `true` skips the human-approval pause on every high-risk action across all four specialists |
| `MAX_ITERATIONS` | `8` | Per-specialist ReAct loop ceiling |
| `LANGCHAIN_TRACING_V2` / `LANGCHAIN_PROJECT` | `false` / `broadband-triage-agent` | LangSmith tracing |
| `LANGCHAIN_ENDPOINT` | *(unset -- SDK default)* | Only set for a non-default-region LangSmith account (see above) |
| `OTEL_ENABLED` / `OTEL_EXPORTER_OTLP_ENDPOINT` | `false` / `http://localhost:4318` | OTLP export to the collector |

Nothing here has a hard-coded secret or Bedrock model default.

## Test results

80/80 passing (`cd agent && uv run pytest -q`), covering dynamic tool
selection, the ReAct loop within each specialist, the supervisor's dispatch
logic (including a chained multi-specialist investigation), all seven
diagnostic paths (the original four, plus one per new specialist), the
approval boundary (five gated tools, four unconditional and one threshold-
based, both approve and reject), tool authorization -- including a
prompt-injection attempt and a specialist-to-specialist tool-scoping
attempt -- PII redaction, OTel/LangSmith toggling, and the health/ready
endpoints.

## Known limitations

Since validated live end-to-end against a real AWS account (`ap-southeast-2`,
default VPC, `au.anthropic.claude-haiku-4-5-20251001-v1:0`): live Bedrock
calls, `terraform apply`'s full ECS Fargate stack (ALB, IAM, ECS, Secrets
Manager, CloudWatch), the human-approval pause/resume flow over the public
ALB, and LangSmith tracing (traces confirmed landing via the `langsmith`
CLI -- see the regional-endpoint note above for the `403 Forbidden` this
took a while to root-cause).

Still not exercised against anything real:

- **Cribl/Datadog/Splunk** -- no real Cribl instance in a personal account;
  the ADOT sidecar's OTLP export just fails against a placeholder endpoint
  (harmless -- CloudWatch logging is a separate path, see
  [docs/observability.md](docs/observability.md)).

See [docs/architecture.md#known-limitations](docs/architecture.md#known-limitations)
and [docs/deployment.md#known-limitations](docs/deployment.md#known-limitations)
for further detail, and [docs/deployment.md](docs/deployment.md#known-limitations)
for the in-memory checkpointer caveat for multi-task deployments (still
applicable -- this deployment ran `desired_count = 1`).
