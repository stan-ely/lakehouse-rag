import pytest

from app.sql_tool.catalog import VIEWS, describe, views_for
from app.sql_tool.validator import SQLRejected, validate_sql

ALL_VIEWS = [view.name for view in VIEWS]
SALES_VIEWS = [view.name for view in views_for(["all-staff", "sales"])]


def _ok(sql: str, views: list[str] = ALL_VIEWS, max_rows: int = 100) -> str:
    return validate_sql(sql, views, max_rows=max_rows).sql


def test_views_offered_follow_group_rules() -> None:
    assert SALES_VIEWS == ["customers", "shipments", "employee_directory", "invoices"]
    assert "employee_compensation" in [v.name for v in views_for(["hr"])]
    assert views_for([]) == []


def test_describe_renders_qualified_views_with_columns() -> None:
    text = describe(views_for(["support"]))
    assert "analytics.shipments (" in text
    assert "is_late boolean" in text
    assert "invoices" not in text


def test_simple_select_is_normalised_qualified_and_capped() -> None:
    result = validate_sql("select name from customers where tier = 'gold';", ALL_VIEWS, max_rows=50)

    assert result.sql == "SELECT name FROM analytics.customers WHERE tier = 'gold' LIMIT 50"
    assert result.views == ("customers",)


def test_joins_ctes_windows_and_date_functions_are_allowed() -> None:
    sql = _ok(
        """
        WITH late AS (
            SELECT customer_id, date_trunc('month', delivered_at) AS month, count(*) AS n
            FROM analytics.shipments
            WHERE is_late AND delivered_at >= now() - interval '90 days'
            GROUP BY 1, 2
        )
        SELECT c.name, l.month, l.n, rank() OVER (PARTITION BY l.month ORDER BY l.n DESC)
        FROM late AS l JOIN customers AS c USING (customer_id)
        ORDER BY l.month, l.n DESC
        LIMIT 10
        """
    )
    assert "FROM analytics.shipments" in sql
    assert "JOIN analytics.customers" in sql
    assert sql.endswith("LIMIT 10")


def test_small_limit_is_kept_and_large_limit_is_capped() -> None:
    assert _ok("SELECT * FROM shipments LIMIT 5").endswith("LIMIT 5")
    assert _ok("SELECT * FROM shipments LIMIT 100000").endswith("LIMIT 100")


def test_set_operations_are_capped_as_a_whole() -> None:
    sql = _ok("SELECT customer_id FROM customers UNION SELECT customer_id FROM shipments")
    assert sql.startswith("SELECT * FROM (")
    assert sql.endswith("LIMIT 100")


def test_safe_unmodelled_functions_are_allowed() -> None:
    assert "AGE(" in _ok("SELECT age(hire_date) FROM employee_directory").upper()


@pytest.mark.parametrize(
    ("sql", "reason"),
    [
        pytest.param(
            "WITH g AS MATERIALIZED (SELECT set_config('app.user_groups', 'hr', true)) "
            "SELECT * FROM analytics.employee_compensation, g",
            "set_config",
            id="widen-groups-via-set-config",
        ),
        pytest.param("SELECT set_config('role', 'rag', true)", "set_config", id="switch-role"),
        pytest.param(
            "SELECT pg_catalog.set_config('role', 'rag', true) FROM customers",
            "set_config",
            id="schema-qualified-set-config",
        ),
        pytest.param(
            "SELECT current_setting('app.user_groups') FROM customers", "current_setting", id="gucs"
        ),
        pytest.param("SELECT pg_sleep(30) FROM customers", "pg_sleep", id="sleep"),
        pytest.param(
            "SELECT pg_read_file('/etc/passwd') FROM customers", "pg_read_file", id="file"
        ),
        pytest.param("SELECT 1 FROM customers; DROP TABLE x", "one statement", id="stacked"),
        pytest.param("DELETE FROM analytics.customers", "SELECT", id="delete"),
        pytest.param(
            "WITH d AS (DELETE FROM analytics.customers RETURNING *) SELECT * FROM d",
            "DELETE",
            id="data-modifying-cte",
        ),
        pytest.param("SELECT * INTO copy FROM customers", "INTO", id="select-into"),
        pytest.param("SELECT * FROM customers FOR UPDATE", "LOCK", id="row-lock"),
        pytest.param("SET ROLE rag", "SELECT", id="set-role"),
        pytest.param("COPY analytics.customers TO '/tmp/x'", "SELECT", id="copy"),
        pytest.param("SELECT * FROM ops.employee_compensation", "not an available view", id="ops"),
        pytest.param("SELECT * FROM pg_catalog.pg_roles", "not an available view", id="catalog"),
        pytest.param("SELECT * FROM information_schema.tables", "not an available view", id="info"),
        pytest.param("SELECT * FROM pg_roles", "not an available view", id="unqualified-catalog"),
        pytest.param("SELECT 1", "at least one", id="no-view"),
        pytest.param("", "empty", id="empty"),
        pytest.param("SELEC name FROM customers", "parsed", id="unparseable"),
    ],
)
def test_dangerous_or_out_of_scope_sql_is_rejected(sql: str, reason: str) -> None:
    with pytest.raises(SQLRejected, match=reason):
        validate_sql(sql, ALL_VIEWS, max_rows=100)


def test_views_outside_the_callers_groups_are_rejected() -> None:
    with pytest.raises(SQLRejected, match="not an available view"):
        validate_sql("SELECT * FROM employee_compensation", SALES_VIEWS, max_rows=100)


def test_a_cte_cannot_launder_a_forbidden_view_name() -> None:
    sql = "WITH employee_compensation AS (SELECT * FROM analytics.employee_compensation) SELECT 1"
    with pytest.raises(SQLRejected, match="not an available view"):
        validate_sql(sql, SALES_VIEWS, max_rows=100)
