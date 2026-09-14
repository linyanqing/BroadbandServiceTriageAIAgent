# Developer Workflow

End-to-end walkthrough: making a code change in your IDE, getting it tested
and merged, watching it deploy, and observing it live. Each step links to
the doc that covers it in depth -- this page is the map, not a duplicate.

```
IDE edit -> local test (MOCK_MODE=true) -> [optional: local test vs live Bedrock]
  -> push branch -> PR -> ci.yml (lint+test) -> merge to main
  -> deploy.yml (test -> build -> push -> redeploy -> wait-stable)
  -> live on ECS -> observe via CloudWatch / LangSmith / (OTel -> Cribl, not live)
```

## 1. Make the change locally

Edit under `agent/src/agent/` in your IDE as normal -- nothing special
about the setup (`uv`-managed, see the repo root `README.md#quickstart`).

## 2. Fast local loop -- no AWS needed

```bash
cd agent
uv run pytest -q                          # 83 tests, ~8s, MOCK_MODE=true by default
uv run ruff check .
uv run ruff format --check .
uv run uvicorn agent.main:app --reload    # auto-reloads on save
```

```bash
curl -X POST localhost:8000/api/v1/triage -H 'Content-Type: application/json' \
  -d '{"customer_id": "123", "message": "My broadband keeps dropping out today"}'
```

This runs the full multi-agent graph (supervisor + all four specialists)
against the deterministic mock policies -- see
[agent-design.md](agent-design.md) for what "mock" does and doesn't stub
out. This is the loop for the vast majority of changes: pure logic, new
tools, new tests, prompt wording.

## 3. Optional -- run locally against live Bedrock

Only needed when a change actually touches `models/bedrock.py`,
`models/bedrock_orchestrator.py`, or prompt wording you want to verify
against a real model's behavior (not just the mock's).

```bash
aws sso login                               # if the SSO session has expired
```

```bash
# agent/.env: MOCK_MODE=false, BEDROCK_MODEL_ID=<a valid inference-profile id>
uv run uvicorn agent.main:app --reload
```

See [README.md#running-against-amazon-bedrock](../README.md#running-against-amazon-bedrock)
for AWS prerequisites (SSO profile, model access, IAM) and the
inference-profile-ID gotcha. This talks to the real model but still runs
entirely on your machine -- nothing is deployed yet.

## 4. Push a branch, open a PR

```bash
git checkout -b my-change
git commit -am "..."
git push -u origin my-change
gh pr create
```

**`.github/workflows/ci.yml`** runs automatically: `ruff check`,
`ruff format --check`, `pytest` (`MOCK_MODE=true`, no AWS credentials
needed or used). This is the gate before merge -- nothing deploys yet, and
a PR branch cannot reach AWS even if it tried (see step 6's OIDC note).

```bash
gh pr checks <number>          # or watch it on github.com
```

## 5. Merge -> automatic deploy

Merging the PR to `main` triggers **`.github/workflows/deploy.yml`**:

1. **`test`** -- re-runs the exact same lint+test job, against the actual
   merge commit (not just the PR branch tip) -- catches anything a
   different merge strategy or a stale PR branch could have missed.
2. **`deploy`** (only if `test` passes) -- builds the image
   (`docker/Dockerfile`), pushes it to ECR tagged both `latest` and the
   commit SHA, force-redeploys the ECS service, and waits for it to
   stabilize (`aws ecs wait services-stable`).

```bash
gh run list --workflow=deploy.yml --limit 3
gh run view <run-id>                 # or watch it on github.com/.../actions
```

**Authentication is OIDC, not stored keys**: GitHub Actions assumes an IAM
role (`infra/modules/github_oidc`) via a short-lived token per run. The
role's trust policy accepts only this exact repo on pushes to `main`
(never a PR branch, never a fork), and its permissions are scoped to push
access on only the `broadband-triage-agent` ECR repo and update access on
only the `broadband-triage-agent-dev` ECS service -- nothing broader in
the account. See `docs/deployment.md#cicd-github-actions` for the full
detail, including a real gotcha already hit and fixed: GitHub's OIDC `sub`
claim includes immutable numeric owner/repo IDs
(`repo:owner@123/repo@456:ref:...`), not the plain `owner/repo` form most
examples show -- the trust policy uses `StringLike` with wildcards on just
those two ID segments to handle it.

**The pipeline never runs Terraform.** This repo's Terraform state is
local-only (`infra/terraform.tfstate`, gitignored) -- a CI runner has no
access to it. Infra changes (a new variable, a new module, a changed IAM
policy) stay a manual, local step:

```bash
cd infra
terraform plan  -var-file=environments/dev.tfvars
terraform apply -var-file=environments/dev.tfvars
```

## 6. Confirm it's live

```bash
curl http://$(terraform -chdir=infra output -raw alb_dns_name)/ready
```

`mock_mode` in the response should read `false` for the deployed
environment; `environment` should read `dev`.

## 7. Observe it

Three independent paths -- see [observability.md](observability.md) for
the full architecture and why they're kept separate. In practice, day to
day:

### CloudWatch (always on, AWS-native)

```bash
aws logs tail /ecs/broadband-triage-agent-dev --region ap-southeast-2 --since 10m --format short
```

Every request logs `triage_request_received`/`triage_request_completed`/
`triage_resume_completed` with a `request_id`/`correlation_id` you can
grep for. **This is the first place to look when something looks wrong in
production** -- the API only ever returns a generic 500 to callers
(`main.py`'s exception handlers), so the real traceback is only ever in
these logs. CloudWatch also has two alarms (ALB 5xx rate, ECS CPU) from
`infra/modules/cloudwatch`.

### LangSmith (AI-specific tracing)

Needs the CLI once: `curl -sSL
https://raw.githubusercontent.com/langchain-ai/langsmith-cli/main/scripts/install.sh
| sh` (or use the `langsmith-skills` Claude Code skills installed earlier
in this project -- `langsmith-trace` in particular).

```bash
export LANGSMITH_API_KEY=$(aws secretsmanager get-secret-value \
  --secret-id broadband-triage-agent/dev/langchain-api-key \
  --region ap-southeast-2 --query SecretString --output text)

langsmith trace list --project broadband-triage-agent --limit 10 \
  --api-url https://apac.api.smith.langchain.com --api-key "$LANGSMITH_API_KEY"

langsmith trace get <trace-id> --project broadband-triage-agent \
  --api-url https://apac.api.smith.langchain.com --api-key "$LANGSMITH_API_KEY"
```

`trace get` shows the full node hierarchy -- which specialist(s) the
supervisor dispatched, every `agent_decision`/`ChatBedrockConverse`/
`tool_execution` call and its latency, in order. **This is the right tool
for "why did the model do that"** -- a live-model reasoning question
CloudWatch's plain-text logs can't answer, since they don't capture
prompts/tool-call arguments.

`--api-url` is required here because this LangSmith account is on the
APAC regional deployment, not the default US one -- see
`docs/architecture.md#known-limitations` if setting this up for a
different LangSmith account, since a key only authenticates against the
region it was issued in and the failure mode (`403 Forbidden` on every
request) doesn't obviously point at "wrong region."

### OpenTelemetry -> ADOT -> Cribl -> Datadog/Splunk (not live in this account)

The code path is real and exercised locally
(`docker compose -f docker/docker-compose.yml up --build`, see
`docs/deployment.md`), and the ADOT sidecar runs in the deployed ECS task,
but there's no real Cribl endpoint in this personal AWS account -- the
sidecar's export attempts just fail harmlessly and log the failure
(`dial tcp: lookup cribl.example.internal ...`) in CloudWatch. Nothing to
action; this is expected until `cribl_otlp_endpoint` points at a real
instance.

## Known gaps in this workflow

- No staging environment -- `main` deploys straight to the one `dev` ECS
  service. A change is live the moment it merges.
- No automated `terraform plan` on PRs that touch `infra/` -- infra
  changes are proposed and reviewed as a diff, but not validated by CI
  before merge (and can't be, without giving CI access to the local state
  file -- see the note in step 5).
- No rollback automation -- reverting means `git revert` the bad merge
  commit, pushing that to `main`, and waiting for `deploy.yml` to run
  again on the revert; there's no one-click "redeploy the previous task
  definition." (`gh workflow run deploy.yml --ref <branch>` re-runs
  `deploy.yml` from any branch/tag, but a raw commit SHA doesn't work as
  `--ref` -- it only accepts a branch or tag name.)
