# ADOT Collector config for the sidecar. Deliberately forwards ONLY to
# Cribl -- CloudWatch gets logs via the ECS awslogs driver on the app
# container, a completely separate path (see docs/observability.md).
receivers:
  otlp:
    protocols:
      http:
        endpoint: 0.0.0.0:4318
      grpc:
        endpoint: 0.0.0.0:4317

processors:
  batch: {}

exporters:
  otlphttp/cribl:
    endpoint: "${cribl_otlp_endpoint}"

service:
  pipelines:
    traces:
      receivers: [otlp]
      processors: [batch]
      exporters: [otlphttp/cribl]
    metrics:
      receivers: [otlp]
      processors: [batch]
      exporters: [otlphttp/cribl]
    logs:
      receivers: [otlp]
      processors: [batch]
      exporters: [otlphttp/cribl]
