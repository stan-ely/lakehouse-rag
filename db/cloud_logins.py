"""Service logins and role switching for the AWS smoke database (RDS), after migrations.

`local_logins.py` sets well-known passwords and refuses to run outside local and CI. Here each
call sets a fresh random password and hands it back, so the only copy lives in the calling
process's environment and is gone when the smoke run ends. The database is destroyed the same
day (ADR 0007).

RDS has no superuser: the master login gets ADMIN on the roles it creates, but since
PostgreSQL 16 not SET, so the `SET LOCAL ROLE` the retriever and indexer rely on would fail.
Granting SET (without INHERIT) keeps the local behaviour: the master acts as a role only when
the code asks for it.
"""

import secrets

import psycopg
from psycopg import sql

from db.local_logins import LOGINS

# Roles the application switches into with SET LOCAL ROLE (app/retrieval, ingestion/indexer).
SWITCHED_ROLES = ("rag_retriever", "rag_indexer")


def enable_logins(conn: psycopg.Connection) -> dict[str, str]:
    """Sets a random password on each service login; returns role -> password."""
    passwords: dict[str, str] = {}
    for role in LOGINS:
        password = secrets.token_urlsafe(24)
        conn.execute(
            sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD {}").format(
                sql.Identifier(role), sql.Literal(password)
            )
        )
        passwords[role] = password
    return passwords


def grant_role_switching(conn: psycopg.Connection) -> None:
    for role in SWITCHED_ROLES:
        conn.execute(
            sql.SQL("GRANT {} TO CURRENT_USER WITH INHERIT FALSE, SET TRUE").format(
                sql.Identifier(role)
            )
        )
