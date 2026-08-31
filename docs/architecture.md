# Architecture

## 1. Context diagram

```mermaid
flowchart LR
    Customer([Customer])
    API["API Gateway / ALB"]
    Agent["LangGraph Agent Runtime<br/>ECS Fargate"]
    Bedrock[("Amazon Bedrock")]
    Enterprise[["Enterprise APIs<br/>customer / service / outage /<br/>diagnostics / fault-mgmt"]]
    LangSmith[("LangSmith")]
    ADOT["ADOT Collector"]
    Cribl[["Cribl"]]
    Datadog[("Datadog")]
    Splunk[("Splunk")]
    CloudWatch[("CloudWatch")]

    Customer --> API --> Agent
    Agent --> Bedrock
    Agent --> Enterprise
    Agent -. "AI tracing" .-> LangSmith
    Agent -. "enterprise telemetry" .-> ADOT --> Cribl
    Cribl --> Datadog
    Cribl --> Splunk
    Agent -. "AWS-native logs/metrics" .-> CloudWatch
```

Two observability paths leave the agent, and they never merge:
**LangSmith** (AI-specific run/evaluation tracing) and **OpenTelemetry ->
ADOT -> Cribl -> Datadog/Splunk** (vendor-neutral enterprise telemetry).
**CloudWatch** is a third, AWS-native path fed directly by ECS/AWS, not by
the OTel pipeline. See [observability.md](observability.md) for the full
rationale.

## 2. Logical architecture

```mermaid
flowchart TB
    subgraph Runtime["LangGraph Agent Runtime"]
        Perception["perception"]
        Decision["agent_decision"]
        ToolExec["tool_execution"]
        Observation["observation"]
        Approval["human_approval"]
        Response["final_response"]
    end

    Perception --> Decision
    Decision -->|"tool, low-risk / approved"| ToolExec
    Decision -->|"tool, high-risk, not approved"| Approval
    Decision -->|"complete / escalate"| Response
    ToolExec --> Observation --> Decision
    Approval --> Decision
    Response --> Done([END])

    Decision <--> Bedrock[("Amazon Bedrock<br/>ChatBedrockConverse + tool binding")]
    ToolExec --> Registry[["Tool Registry<br/>allow-list + pydantic schemas"]]
    Registry --> Tools[["5 mocked enterprise tools"]]
```

The graph (state machine structure, allowed transitions, the human-approval
boundary) is fixed by LangGraph. The **decision policy** plugged into
`agent_decision` (Bedrock in production, a rule-based mock for local/CI use)
is what actually chooses the next action at runtime from the current
`AgentState.observations` -- see [agent-design.md](agent-design.md).

## 3. LangGraph state graph

```mermaid
stateDiagram-v2
    [*] --> perception
    perception --> agent_decision
    agent_decision --> tool_execution: tool, low-risk or approved
    agent_decision --> human_approval: tool, high-risk, not approved
    agent_decision --> final_response: complete, escalate, invalid action, or max iterations
    tool_execution --> observation
    observation --> agent_decision
    human_approval --> agent_decision: interrupt then Command resume
    final_response --> [*]
```

## 4. Physical deployment diagram

```mermaid
flowchart LR
    Internet((Internet)) --> ALB["Application Load Balancer"]
    ALB --> Service["ECS Fargate Service"]

    subgraph Task["ECS Task"]
        AppC["agent container<br/>FastAPI + LangGraph"]
        ADOTC["adot-collector container<br/>sidecar"]
        AppC -- "OTLP localhost:4318" --> ADOTC
    end

    Service --> Task
    AppC --> BedrockSvc[("Amazon Bedrock")]
    AppC --> SecretsMgr[("Secrets Manager")]
    AppC -- "awslogs driver" --> CW[("CloudWatch Logs")]
    ADOTC -- "OTLP/HTTP" --> CriblSvc[["Cribl"]]
    CriblSvc --> DD[("Datadog")]
    CriblSvc --> SP[("Splunk")]
```

See [deployment.md](deployment.md) for the Terraform that provisions this
(`infra/`) and the container image (`docker/Dockerfile`).

## 5. Sequence diagram

```mermaid
sequenceDiagram
    participant C as Customer
    participant API as FastAPI Triage Endpoint
    participant G as LangGraph
    participant B as Bedrock
    participant T as Enterprise Tool
    participant LS as LangSmith
    participant OT as OpenTelemetry
    participant ADOT as ADOT Collector
    participant CS as Cribl

    C->>API: POST /api/v1/triage (customer_id, message)
    API->>G: invoke(state, thread_id=request_id)
    G->>G: perception (classify issue)
    loop ReAct loop
        G->>B: agent_decision (state + tool specs)
        B-->>G: tool call OR finish
        alt tool call
            G->>T: tool_execution (validated call)
            T-->>G: result
            G->>G: observation (normalize into state)
        else finish
            G->>G: final_response (templated, grounded)
        end
    end
    opt high-risk tool (create_fault_ticket)
        G-->>API: interrupt (awaiting_approval)
        API-->>C: 200 status=awaiting_approval, approval={...}
        C->>API: POST /approve (approved=true)
        API->>G: Command(resume={approved: true})
    end
    G-->>API: final state
    API-->>C: 200 status, summary, ticket_id
    par independent telemetry paths
        G-->>LS: run/node/tool traces, tags, metadata (redacted)
    and
        G-->>OT: spans + metrics (OTLP)
        OT-->>ADOT: export
        ADOT-->>CS: forward to Datadog and Splunk
    end
```

LangSmith and OpenTelemetry are emitted independently and never depend on
each other -- disabling either one does not affect the customer-facing
response or the other pipeline.

## 6. Observability architecture

See [observability.md](observability.md) for the full write-up. Summary:

| Concern | System | Notes |
|---|---|---|
| Agent run/node/LLM/tool traces, evaluation | **LangSmith** | AI-specific; gated by `LANGCHAIN_TRACING_V2` |
| Vendor-neutral traces/metrics/logs | **OpenTelemetry -> ADOT -> Cribl -> Datadog/Splunk** | Enterprise pipeline; gated by `OTEL_ENABLED` |
| AWS-native container/app logs, ECS/ALB metrics, alarms | **CloudWatch** | Fed directly by ECS `awslogs` driver + native AWS metrics, not via OTel |

## 7. Security architecture

See [security.md](security.md) for the full write-up. Summary: an explicit
tool allow-list is the actual enforcement boundary (not prompt wording), a
human-approval interrupt gates the one high-risk action, and PII is redacted
before it reaches LangSmith or structured logs.

## Known limitations

- This environment had no AWS credentials and no Bedrock model access:
  `BedrockDecisionPolicy` (`agent/src/agent/models/bedrock.py`) is implemented
  against the documented LangChain/Bedrock tool-calling contract and its
  response-parsing logic is unit-tested, but it has **not been exercised
  against a live Bedrock model**. `MockDecisionPolicy` is what all local runs
  and automated tests exercise (`MOCK_MODE=true`, the default).
- LangSmith and the Cribl/Datadog/Splunk pipeline were not exercised against
  real endpoints for the same reason; the OTLP/LangSmith integration code
  paths are real, but only validated with `OTEL_ENABLED`/`LANGCHAIN_TRACING_V2`
  toggled off, plus a local OTel Collector with a `logging` exporter
  (`docker/docker-compose.yml`).
- `infra/` was validated with `terraform validate`/`terraform fmt` only --
  it has not been applied against a real AWS account (no credentials/VPC
  available here).
