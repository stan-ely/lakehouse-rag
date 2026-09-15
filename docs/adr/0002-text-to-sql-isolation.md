# ADR 0002: Text-to-SQL isolation

- **Status:** accepted
- **Date:** 2026-09-15

## Context
The assistant answers record questions ("how many late shipments did Brightline Foods have last
month?") by letting a model write SQL. That SQL is untrusted: the question, and retrieved tickets,
chats and emails, can all carry prompt injection. Two views are sensitive:

| View | Visible to |
|---|---|
| `analytics.employee_compensation` | hr, exec |
| `analytics.invoices` | finance, sales, exec |

Migration 0004 filtered both views on `app.user_groups`, a setting the API wrote before switching
to the read-only role `rag_sql_readonly`. A probe against the local database showed this is not a
boundary. Running as `rag_sql_readonly` with groups `all-staff,sales`, one SELECT could:

- widen its own groups with `set_config('app.user_groups', 'hr', true)` inside a CTE, and read
  all 107 compensation rows;
- switch back to the session role with `set_config('role', 'rag', true)`. Locally that role is
  a superuser.

`set_config` is executable by PUBLIC, and a role switch is checked against the *session* user's
memberships, not the current role's.

## Decision
Two independent layers. Each is tested with the other one removed.

1. **Validator (`app/sql_tool/validator.py`).**
   - **Parsing:** sqlglot must parse exactly one query.
   - **Rejected statements:** data-modifying, locking, `INTO` and utility statements.
   - **Views:** every table must be one of the analytics views offered to this caller.
   - **Functions:** an allowlist. sqlglot leaves unmodelled functions as `Anonymous` nodes, and
     that is where `set_config`, `current_setting`, `pg_sleep`, `dblink` and `pg_read_file` live.
     They are rejected unless allowlisted.
   - **What runs:** SQL regenerated from the tree, with a LIMIT cap, never the model's text.
2. **Database (migration 0006).**
   - **Login:** the SQL tool connects as `rag_sql`, a member of `rag_sql_readonly` and nothing
     else. It uses its own connection pool, so no other role is reachable.
   - **Group context:** `authz.begin_sql_request(groups)` is SECURITY DEFINER. It writes the
     caller's groups to `authz.sql_request_context`, keyed by the current transaction id.
   - **Read-only transaction:** the executor then runs `SET LOCAL transaction_read_only = on`. After
     that, Postgres refuses every write, including a rewrite of the context row, and refuses any
     switch back to read-write ("must be set before any query").
   - **Sensitive views:** they read `authz.sql_request_groups()`. It returns no groups unless the
     transaction is read-only and owns a context row, so every other path fails closed.
   - **Per query:** a statement timeout, and the transaction is always rolled back.

`tests/integration/test_sql_access_control.py` sends hostile SQL straight to the executor,
bypassing layer 1. It covers `set_config` group widening, role switching, flipping back to
read-write, re-writing the context, base-table reads and data-modifying CTEs.
`tests/unit/test_sql_validator.py` covers layer 1 on its own.

Retrieval RLS on `rag.chunks` keeps using `app.user_groups`, because retrieval never runs
caller-written SQL.

## Alternatives considered
- **Check `current_user` and the groups after the query:** bypassable, because the same statement
  can widen, read and restore both settings before it ends.
- **`REVOKE EXECUTE ON FUNCTION set_config FROM PUBLIC`:** needs superuser rights on `pg_catalog`,
  which RDS does not grant, and it would also break the retriever's own `set_config` calls.
- **One login per access tier:** this also holds, but every new sensitive view multiplies the
  logins and pools to run.
- **HMAC-signed group setting:** this also holds, but it needs key distribution into the database,
  and every row filter pays for the verification.

## Consequences
- **Portable:** the design works on RDS. It needs no superuser, only a login role plus `CREATE
  FUNCTION`.
- **Local and CI passwords:** migrations stay credential-free. `mise run migrate` runs
  `db/local_logins.py` to set development passwords, and AWS sets them from Secrets Manager.
- **Context rows are inert:** they only exist inside rolled-back transactions. A row committed by
  some other client is keyed by a transaction id that is never reused.
- **Residual risk:** if layer 1 were bypassed, a query could still raise its own
  `statement_timeout` and hold a connection. Cancelling from the client side is the week 6
  follow-up. Data exposure is unaffected.
- **Adding a sensitive view:** filter it on `(SELECT authz.sql_request_groups())`, add it to
  `app/sql_tool/catalog.py` with `required_groups`, and extend the access-control tests. An
  integration test fails if the catalog and the database drift apart.
