"""Enables login for service roles with well-known passwords, for local development and CI only.

Migrations never contain credentials (see 0003). In AWS the same roles get their passwords from
Secrets Manager. Run after migrations: `mise run migrate` does both.
"""

import os
import sys

import psycopg
from psycopg import sql

# role -> (environment variable holding its password, local default)
LOGINS = {"rag_sql": ("RAG_SQL_DB_PASSWORD", "rag_sql")}


def main() -> None:
    env = os.environ.get("RAG_ENV", "local")
    if env not in {"local", "ci"}:
        sys.exit(f"refusing to set development passwords with RAG_ENV={env}")
    url = os.environ.get("DATABASE_URL", "postgresql://rag:rag@localhost:5432/rag")
    with psycopg.connect(url.replace("+psycopg", ""), autocommit=True) as conn:
        for role, (variable, default) in LOGINS.items():
            statement = sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD {}").format(
                sql.Identifier(role), sql.Literal(os.environ.get(variable, default))
            )
            conn.execute(statement)
            print(f"login enabled for {role}")


if __name__ == "__main__":
    main()
