# ADR 0003: Amazon Nova Lite on Bedrock for evaluation runs

Status: accepted (2026-09-17)

## Context

The fake provider is deterministic and free, so it gates CI, but it cannot write SQL, route a
question or judge an answer. Week 5's full-pipeline metrics — router accuracy, SQL execution
accuracy, answer correctness — therefore need a real model.

Bedrock is the provider: the same AWS account already hosts the smoke-test infrastructure, and
credentials come from `aws login` rather than a long-lived key in a file.

Two constraints turned up when we tried to pick a model:

- **Claude Sonnet 5 and Opus 5 are not enabled for this account.** Bedrock lists them, but
  `Converse` answers `AccessDeniedException: not available for this account`. Sonnet 4.6 returns
  `Model use case details have not been submitted for this account`, which is a one-time console
  form. Only Haiku 4.5 answered among Claude models.
- **This is a portfolio project on a personal account**, so a run that costs a few dollars is a
  real consideration, and the golden set is meant to be run often.

Bedrock's Converse API is model-agnostic, so the provider already works with any model on it.

## Decision

Run the full evaluation profile on **`us.amazon.nova-lite-v1:0`**, overridable with `--model`.

The three Nova models the account can call were each scored on the whole 121-case golden set:

| | Nova Micro | **Nova Lite** | Nova Pro |
|---|---|---|---|
| cost per full run | $0.007 | **$0.013** | $0.189 |
| recall@k (end to end) | 0.667 | **0.903** | 0.931 |
| MRR | 0.610 | **0.825** | 0.818 |
| router accuracy | 0.743 | **0.927** | 0.807 |
| answer correctness | 0.688 | **0.872** | 0.899 |
| SQL execution success | 0.915 | **0.936** | 0.872 |
| refusal on restricted questions | 1.000 | **1.000** | 1.000 |
| ACL leaks | 0 | **0** | 0 |
| unexpected refusals | 0.147 | **0.046** | 0.009 |
| p50 latency | 1.74 s | **1.62 s** | 2.78 s |

Nova Lite is the only one that passes every threshold in `eval/thresholds.yaml`, and it does so
at a seventieth of Pro's cost. Two results are worth keeping in mind:

- **Pro routes worse than Lite** (0.807 vs 0.927). It sends plain policy questions to `hybrid`,
  which costs a pointless SQL attempt. That is a prompt problem, not a capability one.
- **Micro is not usable here**, and it fails structurally rather than marginally. It sends 19
  plain document questions ("what is the nightly hotel cap?") to the SQL route, where there is
  nothing to find, and then refuses 15 answerable questions for lack of evidence — a refusal
  caused by its own routing, not by the index. Lite misroutes 8 questions and refuses 5. Since
  Micro saves half a cent per run, there is no trade to make: the cheaper model is not cheaper
  in any way that matters.

No model leaked restricted content or failed to refuse, which is the result that matters most:
access control is enforced in Postgres and in the retrieval filter, not by the model's
discretion.

## Consequences

- A full run costs about a cent, so it can run on every meaningful change rather than nightly.
- Cost reporting needed Nova prices; they come from the AWS Price List API, the same source AWS
  bills from, and the 10% geo-profile premium is now scoped to Anthropic models, which are the
  only ones that carry it.
- `mise run eval-full` reaches real Bedrock while S3, SQS and Postgres stay local, so the
  evaluation is the only thing that leaves the machine.
- If Claude access is granted later, `mise run eval-full -- --model global.anthropic.claude-sonnet-5`
  re-runs the same set, and MLflow keeps both runs for comparison. Claude remains the default
  for the API itself.
- Nova is weaker than Claude at the harder SQL cases (window functions, `GROUP BY` with extra
  projected columns), so thresholds reflect Nova's ceiling, not the system's.
- Lite's remaining misses are concentrated in SQL: 6 of its 9 wrong answers are queries that run
  but compute the wrong thing, which is the failure mode worth attacking next (few-shot examples
  in the SQL prompt, or a repair step that feeds the result back). Routing and grounding are not
  the bottleneck at this size.
