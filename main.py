import datetime
import os
from contextlib import contextmanager
from functools import lru_cache

import psycopg2
from fastmcp import FastMCP
from psycopg2.extras import RealDictCursor

CATEGORIES_PATH = os.path.join(os.path.dirname(__file__), "categories.json")

mcp = FastMCP("ExpenseTracker")


def _connect():
    """Read DATABASE_URL at call time (not at import) and open a connection."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Example: postgresql://user:password@host:5432/dbname"
        )
    return psycopg2.connect(url)


@lru_cache(maxsize=1)
def init_db():
    """Create the table once, on first use. Failures are not cached, so it retries."""
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS expenses(
                    id SERIAL PRIMARY KEY,
                    date DATE NOT NULL,
                    amount DOUBLE PRECISION NOT NULL,
                    category TEXT NOT NULL,
                    subcategory TEXT DEFAULT '',
                    note TEXT DEFAULT ''
                )
            """)
        conn.commit()
    finally:
        conn.close()


@contextmanager
def get_conn():
    """Open a connection, commit on success, rollback on error, always close."""
    init_db()
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@mcp.tool()
def add_expense(
    date: str,
    amount: float,
    category: str,
    subcategory: str = "",
    note: str = "",
):
    """Add a new expense entry to the database. Date format: YYYY-MM-DD."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO expenses(date, amount, category, subcategory, note)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
            """,
            (date, amount, category, subcategory, note),
        )
        row = cur.fetchone()
        if row is None:
            raise RuntimeError("Insert failed: no id returned from database")
        new_id = row[0]
    return {"status": "ok", "id": new_id}


@mcp.tool()
def list_expenses(start_date: str, end_date: str):
    """List expense entries within an inclusive date range (YYYY-MM-DD)."""
    with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT id, date::text AS date, amount, category, subcategory, note
            FROM expenses
            WHERE date BETWEEN %s AND %s
            ORDER BY id ASC
            """,
            (start_date, end_date),
        )
        return [dict(r) for r in cur.fetchall()]


@mcp.tool()
def summarize(start_date: str, end_date: str, category: str | None = None):
    """Summarize expenses by category within an inclusive date range."""
    with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        query = """
            SELECT category, SUM(amount) AS total_amount
            FROM expenses
            WHERE date BETWEEN %s AND %s
        """
        params: list = [start_date, end_date]

        if category:
            query += " AND category = %s"
            params.append(category)

        query += " GROUP BY category ORDER BY category ASC"

        cur.execute(query, params)
        return [dict(r) for r in cur.fetchall()]


@mcp.tool()
def get_chart_data(start_date: str, end_date: str):
    """Get daily spending by category between two dates (YYYY-MM-DD).

    Use this when the user wants to visualize, chart, plot or graph their
    expenses. Returns one row per day, including days with no spending (0).
    """
    start = datetime.date.fromisoformat(start_date)
    end = datetime.date.fromisoformat(end_date)
    if start > end:
        raise ValueError("start_date must be on or before end_date")

    with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT date::text AS period, category, SUM(amount) AS total
            FROM expenses
            WHERE date BETWEEN %s AND %s
            GROUP BY date, category
            """,
            (start_date, end_date),
        )
        data = cur.fetchall()

    categories = sorted({r["category"] for r in data})
    lookup = {(r["period"], r["category"]): float(r["total"]) for r in data}

    rows = []
    day = start
    while day <= end:
        period = day.isoformat()
        row: dict = {"period": period}
        for cat in categories:
            row[cat] = lookup.get((period, cat), 0.0)
        row["total"] = sum(row[cat] for cat in categories)
        rows.append(row)
        day += datetime.timedelta(days=1)

    return rows


@mcp.resource("expense://categories", mime_type="application/json")
def categories():
    # Read fresh each time so you can edit the file without restarting
    with open(CATEGORIES_PATH, encoding="utf-8") as f:
        return f.read()


if __name__ == "__main__":
    mcp.run()
