# ADR 0005: hybrid questions retrieve on their document clauses

Status: accepted (2026-09-17)

## Context

Retrieval scored recall@k 0.931 and MRR 0.881, and the misses were not spread around. Every
one of the five was a hybrid question naming a customer:

> What first response time does our SLA policy promise Hardy Outfitters, and how many of their
> shipments were delivered late in August 2026?

The chunk that answers the first half is `pol-customer-sla`, a policy page that describes
promises by tier and never mentions any customer by name. The customer's name, meanwhile,
appears in dozens of tickets, chats and emails. Both retrieval legs reward it: the lexical leg
matches the name directly, and the vector leg places the question near the ticket cluster that
talks about that customer's shipments.

The obvious fix was reranking, which the plan had pencilled in. Measuring first showed it would
not have worked. Against a 200-candidate pool:

| case | rank of `pol-customer-sla` |
|---|---|
| hybrid-credit-3 | 6 |
| hybrid-credit-1 | 11 |
| hybrid-credit-2 | 19 |
| hybrid-sla-late-1 | 146 |
| hybrid-sla-late-3 | 152 |
| hybrid-sla-late-2 | 176 |

A cross-encoder reordering the top 40, or even the top 100, never sees the chunk it is supposed
to promote for three of the five. Reranking improves an ordering; it cannot recover a candidate
that is not in the pool. The `sla-late` cases are the worst because they run as the `support`
persona, whose groups reach the whole ticket corpus.

Searching the same index with the document half of the question alone puts the same chunk at
rank 1. The problem was never the ranking function. It was that the retrieval leg of a hybrid
question was searching with text written for the SQL leg.

## Decision

On the `hybrid` route only, retrieval searches `document_subquery(question)`: the question's
clauses, split on sentence ends and `, and`, keeping those that match the document vocabulary
the router already uses. If no clause matches, or all of them do, the whole question is
searched, which is exactly the previous behaviour.

Only retrieval narrows. The SQL leg still runs on the whole question, and the generation prompt
still carries the whole question, so the model always sees everything that was asked.

The split is deterministic and reuses `_DOC_PHRASES` from the router. An LLM could produce a
better sub-query, but this path already has a model call for routing and adding a second one
would put cost and a failure mode in front of retrieval to solve a problem that a regex solves.
Keeping it deterministic also keeps `mise run eval` free and model-free, which is the property
that lets it gate CI.

## Consequences

Measured on the 121-case golden set, retrieval profile:

| | before | after |
|---|---|---|
| recall@k | 0.931 | **1.000** |
| MRR | 0.881 | **0.937** |

Six cases improved and none regressed. The five misses became hits, and `hybrid-credit-3` moved
from rank 5 to rank 1. The four hybrid questions that were already passing take the fallback
path and are searched exactly as before.

- The retrieval gate moved to recall@k 0.98 and MRR 0.92, one case below the measured result.
- `run_eval.py` applies the same split in retrieval mode, choosing the route with
  `heuristic_route` rather than the case's label, so the free profile scores the path serving
  actually takes without either a model or the answer key.
- Reranking stays unbuilt. It would now be optimising an ordering whose recall is 1.000, and
  the remaining quality gap is in SQL correctness, not retrieval (ADR 0003).
- The split is tuned to the shape these questions take -- two clauses, one per tool. A hybrid
  question that interleaves its two halves would fall back to the whole question and retrieve
  as it does today, which is a safe failure rather than a wrong one.
