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

## Revisited 2026-09-17: open-weight models

The question was whether an open-weight model would do better on the SQL cases, which is
where Nova Lite's remaining errors are concentrated. Bedrock's Converse API is model-agnostic
and `run_eval.py` already takes `--model`, so this cost no code — only prices, which came from
the same Price List API query, on demand, us-west-2. That query returned Nova Lite at exactly
the values already in `app/llm/cost.py`, which is what says the method is sound.

Self-hosting was never a candidate: 7.8 GB of RAM and 2 GB of VRAM (ADR 0001) put a useful
model out of reach, and CPU inference would take an eval run from three minutes to over an
hour, which is the property the whole harness depends on.

Scored on the 37 SQL cases, breaker disabled, zero errors in each run:

| | **Nova Lite** | gpt-oss-20b | gpt-oss-120b | Qwen3 Coder 30B |
|---|---|---|---|---|
| SQL execution success | **0.946** | 0.838 | 0.838 | 0.811 |
| answer correctness | 0.838 | 0.811 | 0.730 | 0.865 |
| router accuracy | **0.865** | 0.838 | 0.838 | 0.838 |
| unexpected refusals | **0.054** | 0.054 | 0.081 | 0.108 |
| p50 latency | 2.26 s | 3.80 s | 4.29 s | **2.04 s** |
| cost per 37-case run | **$0.003** | $0.006 | $0.012 | $0.008 |

**No open-weight model beats Nova Lite on SQL execution success**, which is the metric the
exercise was run to move, and all of them cost more. Qwen3 Coder leads answer correctness by
0.027 — one case — and is worse at the SQL itself.

That one-case lead is worth naming as noise. Two identical Nova Lite runs scored 0.757 and
0.838 answer correctness, so on 37 cases a single case is 0.027 and run-to-run variance is
around three. **Only differences larger than about 0.08 mean anything at this sample size**,
and none of the differences above clear that bar except Nova Lite's SQL success.

The conclusion is the useful part: the remaining SQL errors are a prompt problem, not a model
capability problem. Few-shot examples and a repair step stay the plan.

### What the exercise exposed in the harness

Both of these scored a mechanism rather than a model, and both are now fixed:

- **Citations were matched as ASCII `[1]` only.** gpt-oss cites as full-width `【1】` whatever
  the prompt asks, so every answer failed the grounding check and was refused. It scored 0.189
  answer correctness while its SQL was correct and executing.
- **The circuit breaker ran during scoring.** Two transient faults opened it sixteen cases in,
  and the remaining nineteen failed instantly with `CircuitOpen`, faster than its own reset
  window. The run reported 0.432 SQL success for questions the model was never asked. The
  breaker is right for serving and wrong for batch measurement.

### Operational note

~~Third-party marketplace models on Bedrock — both the OpenAI and the Qwen ones, not Amazon's
own — intermittently fail `Converse` with `ValidationException` on an internal
`CreateOAuth2Token` operation.~~ **Wrong; corrected 2026-09-17.** This has nothing to do with
Bedrock, or with which model is called.

`CreateOAuth2Token` is how botocore renews an `aws login` session. The cached token in
`~/.aws/login/cache/` lives for exactly 900 seconds, and
`LoginCredentialFetcher._REFRESH_THRESHOLD` is 300, so once fewer than five minutes remain every
request triggers a live renewal: reload the token, sign a DPoP header with the cached private
key, call `signin.CreateOAuth2Token` with `grantType=refresh_token`. On this machine that
renewal fails with `ValidationException` — a malformed request, not an auth denial — and the
cache file's mtime shows no renewal has ever succeeded. **Each `aws login` therefore gives about
ten usable minutes**, and every call after that fails no matter what it was calling.

The evidence that looked like a model-specific fault fits this better. The window that failed 37
of 37 was a run started late in a token's life; the 25 consecutive successes "minutes later"
followed a fresh login. Concurrency, endpoint configuration and prompt size were all correctly
ruled out — the cause was simply not in the list of things being tested, and the errors were
attributed to the models that happened to be under test at the time.

So the open-weight scores in the table above stand, and nothing here disqualifies those models
on reliability grounds. The practical rule for any run that reaches real Bedrock: log in
immediately beforehand, or resolve credentials once into the environment
(`aws configure export-credentials --format env`) so botocore never attempts a renewal mid-run.

The lesson is the same one this ADR keeps relearning: an error surfacing through a component is
not evidence about that component. The bracket glyph scored the model, the breaker scored the
harness, and this scored the credential chain.
