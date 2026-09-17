# 6. Fix text-to-SQL in the prompt, not by changing model

Date: 2026-09-17

## Status

Accepted. Follows ADR 0003 (evaluation model), which concluded the SQL gap was a prompt problem
and left the fix unspecified.

## Context

Nova Lite answered 9 of the golden set's answerable cases wrong, and 6 of those were SQL or
hybrid cases. ADR 0003 compared four open-weight models against it and found none better on
`sql_success`, so the remaining gap had to be addressed in the prompt or not at all.

Reading the failing queries showed something the aggregate metric hides. **Every one of them was
valid SQL that executed successfully and returned zero rows.** The repair loop in
`app/sql_tool/service.py` only re-prompts on a validator rejection or a database error, so it
never saw them; `sql_success` counts a query that ran, and each of these ran.

Running the corrected query by hand against the same views, as the same persona, returned the
expected fact in every case:

| case | model's query | corrected result |
|---|---|---|
| `sql-customer-late-1` | `WHERE customer_id = 'Hardy Outfitters'` | join `customers` on `name` → 5 |
| `sql-account-manager-1` | refused, no SQL | two-step join to `employee_directory` → Benjamin Hamilton |
| `sql-invoices-disputed` | added `CURRENT_DATE BETWEEN issued_on AND due_on` for "currently" | 112 |
| `sql-shipments-mode-road` | added `status = 'booked'` for "have we booked" | 2442 |
| `hybrid-credit-1` | selected `c.tier` outside `GROUP BY` | — (database error) |

So the model can write these queries; it was reasoning from a schema that did not tell it enough.
Four distinct mistakes:

1. **Identifier versus name.** Questions name a customer; the schema offers `customer_id` and
   `name` with no hint which one a question refers to, and the model filtered the id on a name.
2. **Unstated join path.** `account_manager_id references employee_directory` was not enough to
   get a two-step join to `full_name`; the model refused instead.
3. **Over-constraining.** English verbs were read as column values: "have we booked" became
   `status = 'booked'`, "currently disputed" added a date range.
4. **`GROUP BY`.** A plain Postgres rule the model broke under the weight of the rest.

A fifth case, `sql-headcount-executive`, was not a model failure at all: the query was correct
and returned 3, but the answer was scored a miss. `eval/metrics.py` required the digit, and
English prose spells out small numbers.

## Decision

Fix the prompt, and fix the harness bug separately.

- **View descriptions** (`app/sql_tool/catalog.py`) say that questions name customers by name and
  that other views join on `customer_id`; that the account manager comes from a named join; and
  that `status` describes every shipment ever placed, so a question about shipments in general is
  not a question about `status = 'booked'`.
- **Three worked examples** in the system prompt, one per measured mistake: a lookup by customer
  name, the two-step manager join, and an aggregate with a correct `GROUP BY`. They use customers
  that do not exist in the generated company, so an example teaches the shape of a query without
  carrying an answer into the prompt. A unit test asserts both names stay absent from the data.
- **Two rules**: filter on exactly what is asked and nothing more, and query only what the views
  hold — leaving a policy clause to the document leg rather than encoding a guess at it, which is
  what produced the invented credit amounts in the `hybrid-credit` cases.
- **`contains` in `eval/metrics.py`** accepts the English word for whole numbers up to twenty.
  Scoring "three people" as a miss measured the model's prose style, not its arithmetic.

## Consequences

The examples cost roughly 300 input tokens on every SQL call: about $0.00002 per call at Nova
Lite's price, which does not change the cost of a full run to any figure worth reporting.

**Not done: re-prompting on an empty result.** It would have caught four of these five directly,
and it is the obvious next idea. It is not built, because a legitimately empty result is a correct
answer, and a second attempt invites the model to loosen a filter until something comes back —
turning a right "none" into a plausible wrong number. That is the failure mode ADR 0003 already
warned about with the circuit breaker: a failed run is recoverable, a plausible-looking wrong
answer is not. If the prompt fix leaves empty-result cases behind, this gets revisited with
measurements rather than assumed.

## Measured, 2026-09-17

Two clean full runs on Nova Lite (121 cases, 0 errors, $0.014 each), against the pre-change
baseline:

| metric | baseline | run 1 | run 2 |
|---|---|---|---|
| answer_correctness | 0.872 | 0.917 | 0.899 |
| sql_success | 0.936 | 0.957 | 0.894 |
| recall_at_k (end to end) | 0.903 | 0.972 | 0.986 |
| correct cases (of 109 answerable) | 95 | 100 | 98 |

**Six cases were fixed and stayed fixed in both runs**: `sql-headcount-executive`,
`sql-shipments-mode-road`, `sql-shipments-mode-rail`, `sql-invoices-disputed`,
`sql-customer-late-1`, `sql-customer-late-2`. That is the id-versus-name confusion and the
over-constraining gone, which is what the change was for.

`sql_success` nevertheless reads *worse* in run 2 than at baseline, and the reason is not this
change. It counts cases of kind `sql` or `hybrid` that produced a query with no error, so a case
the router sends to `docs` scores zero on it without the SQL tool ever being called. Run 2
misrouted all three `sql-account-manager-*` cases to `docs`; run 1 misrouted two. The metric is
measuring the router through the SQL tool.

That is the same mistake this ADR and ADR 0003 keep finding: a number attributed to the component
it surfaced in rather than the one that caused it. `sql_success` should be read only over cases
that actually reached the SQL tool, and that is worth fixing in `eval/metrics.py`.

### What remains, by cause

- **Router, 8 cases.** `sql-account-manager-*` ("Who is the account manager for X?" reads as a
  documents question but the answer is in the database), `sql-customer-tier-*` sent to `hybrid`,
  and `stale-hotel-cap` and `doc-pto-carryover` sent to `sql`. This is now the largest single
  bucket and the obvious next piece of work.
- **Policy values in hybrid answers, 3 cases.** The `hybrid-credit-*` cases get the count right
  and the credit wrong, and `hybrid-credit-1` had the SQL model invent an `analytics.credit_policy`
  view to join against. The rule telling it to leave policy to the document leg is not enough on
  its own.
- **Scoring, 1 case.** `doc-sla-credit-bronze` expects the literal fact `none` and the answer says
  "receives no late delivery credit", which is correct English and a scored miss.
- **Run-to-run variance.** Six cases flipped between two runs of the same code and model, so any
  gap under roughly three cases (0.028) means nothing at this sample size.

The gates in `eval/thresholds.yaml` stay unchanged: run 2 clears every one of them, and raising a
floor to a number that variance alone can breach would make CI flaky rather than strict.
