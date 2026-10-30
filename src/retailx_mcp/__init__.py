import os
import logging
import sqlite3
from pathlib import Path
from datetime import date
from typing import Optional

import aiosqlite
from dotenv import load_dotenv
from fastmcp import FastMCP


# --------------------------------------------------
# Configuration
# --------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[2]

load_dotenv(PROJECT_ROOT / ".env")

DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "sales.db"

# For this version, one configured shop uses this database scope.
# Do not let the LLM choose this value.
SHOP_ID = os.getenv("SHOP_ID", "default-shop")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("sales-mcp")

mcp = FastMCP("SalesManagementServer")


# --------------------------------------------------
# Database initialization
# --------------------------------------------------

def initialize_database():
    """Create tables/indexes. Runs at import time so it also works
    when the server is started with `fastmcp run`."""

    with sqlite3.connect(DB_PATH) as db:

        db.execute("""
            CREATE TABLE IF NOT EXISTS sales (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                shop_id TEXT NOT NULL,
                sale_date TEXT NOT NULL,
                product TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                unit_price REAL NOT NULL,
                total_amount REAL NOT NULL,
                customer TEXT,
                category TEXT,
                note TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_sales_shop_date
            ON sales(shop_id, sale_date)
        """)

        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_sales_shop_product
            ON sales(shop_id, product)
        """)

        db.commit()


initialize_database()


async def get_db():

    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row

    return db


# --------------------------------------------------
# Validation helpers
# --------------------------------------------------

def validate_date(value: str) -> str:
    """Validate and normalize an ISO date."""

    try:
        return date.fromisoformat(value).isoformat()
    except (TypeError, ValueError):
        raise ValueError("Date must use YYYY-MM-DD format.")


def validate_date_range(start_date, end_date):

    if start_date:
        start_date = validate_date(start_date)

    if end_date:
        end_date = validate_date(end_date)

    if start_date and end_date and start_date > end_date:
        raise ValueError("start_date cannot be after end_date.")

    return start_date, end_date


def validate_number_range(low, high, label):

    if low is not None and high is not None and low > high:
        raise ValueError(
            f"min_{label} cannot be greater than max_{label}."
        )


def apply_filters(
    query: str,
    params: list,
    start_date=None,
    end_date=None,
    product=None,
    min_amount=None,
    max_amount=None,
    min_unit_price=None,
    max_unit_price=None,
):
    """Append optional WHERE conditions to a query."""

    if start_date:
        query += " AND sale_date >= ?"
        params.append(start_date)

    if end_date:
        query += " AND sale_date <= ?"
        params.append(end_date)

    if product:
        query += " AND product LIKE ?"
        params.append(f"%{product}%")

    if min_amount is not None:
        query += " AND total_amount >= ?"
        params.append(min_amount)

    if max_amount is not None:
        query += " AND total_amount <= ?"
        params.append(max_amount)

    if min_unit_price is not None:
        query += " AND unit_price >= ?"
        params.append(min_unit_price)

    if max_unit_price is not None:
        query += " AND unit_price <= ?"
        params.append(max_unit_price)

    return query, params


# --------------------------------------------------
# Add sale
# --------------------------------------------------

@mcp.tool()
async def add_sale(
    product: str,
    quantity: int,
    unit_price: float,
    sale_date: Optional[str] = None,
    customer: Optional[str] = None,
    category: Optional[str] = None,
    note: Optional[str] = None,
) -> dict:
    """
    Record a new sale.

    Provide product name, quantity, unit price and optionally
    sale date, customer, category and note.
    """

    if not product or not product.strip():
        raise ValueError("Product name cannot be empty.")

    if quantity <= 0:
        raise ValueError("Quantity must be greater than zero.")

    if unit_price < 0:
        raise ValueError("Unit price cannot be negative.")

    if sale_date is None:
        sale_date = date.today().isoformat()
    else:
        sale_date = validate_date(sale_date)

    total_amount = round(quantity * unit_price, 2)

    db = await get_db()

    try:
        cursor = await db.execute(
            """
            INSERT INTO sales (
                shop_id,
                sale_date,
                product,
                quantity,
                unit_price,
                total_amount,
                customer,
                category,
                note
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                SHOP_ID,
                sale_date,
                product.strip(),
                quantity,
                unit_price,
                total_amount,
                customer,
                category,
                note,
            ),
        )

        await db.commit()

        return {
            "success": True,
            "sale_id": cursor.lastrowid,
            "product": product,
            "quantity": quantity,
            "total_amount": total_amount,
            "sale_date": sale_date,
            "message": "Sale successfully recorded.",
        }

    finally:
        await db.close()


# --------------------------------------------------
# Retrieve sales
# --------------------------------------------------

@mcp.tool()
async def get_sales(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    product: Optional[str] = None,
    min_amount: Optional[float] = None,
    max_amount: Optional[float] = None,
    min_unit_price: Optional[float] = None,
    max_unit_price: Optional[float] = None,
    limit: int = 500,
) -> dict:
    """
    Retrieve sales records with optional filters.

    Filters:
    - start_date / end_date: YYYY-MM-DD
    - product: product name (partial match)
    - min_amount / max_amount: filter on the TOTAL amount of the
      sale (quantity x unit price). Use this for requests like
      "sales of 70 rupees or less" (max_amount=70).
    - min_unit_price / max_unit_price: filter on the price of ONE
      unit. Use only when the user explicitly says per unit.
    - limit: maximum number of records
    """

    if limit < 1 or limit > 5000:
        raise ValueError("Limit must be between 1 and 5000.")

    start_date, end_date = validate_date_range(start_date, end_date)

    validate_number_range(min_amount, max_amount, "amount")
    validate_number_range(
        min_unit_price, max_unit_price, "unit_price"
    )

    query = """
        SELECT
            id,
            sale_date,
            product,
            quantity,
            unit_price,
            total_amount,
            customer,
            category,
            note
        FROM sales
        WHERE shop_id = ?
    """

    query, params = apply_filters(
        query,
        [SHOP_ID],
        start_date,
        end_date,
        product,
        min_amount,
        max_amount,
        min_unit_price,
        max_unit_price,
    )

    query += " ORDER BY sale_date DESC, id DESC LIMIT ?"
    params.append(limit)

    db = await get_db()

    try:
        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()

        records = [dict(row) for row in rows]

        return {
            "count": len(records),
            "sales": records,
        }

    finally:
        await db.close()


# --------------------------------------------------
# Sales summary
# --------------------------------------------------

@mcp.tool()
async def sales_summary(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    min_amount: Optional[float] = None,
    max_amount: Optional[float] = None,
) -> dict:
    """
    Calculate sales summary for a date range, optionally limited to
    sales whose total amount is between min_amount and max_amount.

    Returns total revenue, number of transactions,
    units sold and average transaction value.
    """

    start_date, end_date = validate_date_range(start_date, end_date)

    validate_number_range(min_amount, max_amount, "amount")

    query = """
        SELECT
            COALESCE(SUM(total_amount), 0) AS total_revenue,
            COUNT(*) AS total_transactions,
            COALESCE(SUM(quantity), 0) AS units_sold,
            COALESCE(AVG(total_amount), 0) AS average_transaction
        FROM sales
        WHERE shop_id = ?
    """

    query, params = apply_filters(
        query,
        [SHOP_ID],
        start_date,
        end_date,
        min_amount=min_amount,
        max_amount=max_amount,
    )

    db = await get_db()

    try:
        cursor = await db.execute(query, params)
        row = await cursor.fetchone()

        return {
            "total_revenue": round(row["total_revenue"], 2),
            "total_transactions": row["total_transactions"],
            "units_sold": row["units_sold"],
            "average_transaction": round(
                row["average_transaction"], 2
            ),
            "start_date": start_date,
            "end_date": end_date,
            "min_amount": min_amount,
            "max_amount": max_amount,
        }

    finally:
        await db.close()


# --------------------------------------------------
# Sales trend
# --------------------------------------------------

@mcp.tool()
async def get_sales_trend(
    start_date: str,
    end_date: str,
    min_amount: Optional[float] = None,
    max_amount: Optional[float] = None,
) -> dict:
    """
    Retrieve daily revenue between two dates, optionally limited to
    sales whose total amount is between min_amount and max_amount.
    Useful for generating sales trend charts.
    """

    start_date = validate_date(start_date)
    end_date = validate_date(end_date)

    if start_date > end_date:
        raise ValueError("start_date cannot be after end_date.")

    validate_number_range(min_amount, max_amount, "amount")

    query = """
        SELECT
            sale_date,
            SUM(total_amount) AS revenue,
            SUM(quantity) AS units_sold,
            COUNT(*) AS transactions
        FROM sales
        WHERE shop_id = ?
          AND sale_date BETWEEN ? AND ?
    """

    query, params = apply_filters(
        query,
        [SHOP_ID, start_date, end_date],
        min_amount=min_amount,
        max_amount=max_amount,
    )

    query += """
        GROUP BY sale_date
        ORDER BY sale_date ASC
    """

    db = await get_db()

    try:
        cursor = await db.execute(query, params)

        rows = await cursor.fetchall()

        return {
            "start_date": start_date,
            "end_date": end_date,
            "min_amount": min_amount,
            "max_amount": max_amount,
            "trend": [
                {
                    "date": row["sale_date"],
                    "revenue": round(row["revenue"], 2),
                    "units_sold": row["units_sold"],
                    "transactions": row["transactions"],
                }
                for row in rows
            ],
        }

    finally:
        await db.close()


# --------------------------------------------------
# Top-selling products
# --------------------------------------------------

@mcp.tool()
async def top_products(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = 5,
) -> dict:
    """
    Find the best-selling products by quantity sold.
    Also returns revenue generated by each product.
    """

    if limit < 1 or limit > 50:
        raise ValueError("Limit must be between 1 and 50.")

    start_date, end_date = validate_date_range(start_date, end_date)

    query = """
        SELECT
            product,
            SUM(quantity) AS units_sold,
            SUM(total_amount) AS revenue
        FROM sales
        WHERE shop_id = ?
    """

    query, params = apply_filters(
        query,
        [SHOP_ID],
        start_date,
        end_date,
    )

    query += """
        GROUP BY product
        ORDER BY units_sold DESC
        LIMIT ?
    """

    params.append(limit)

    db = await get_db()

    try:
        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()

        return {
            "top_products": [
                {
                    "product": row["product"],
                    "units_sold": row["units_sold"],
                    "revenue": round(row["revenue"], 2),
                }
                for row in rows
            ]
        }

    finally:
        await db.close()


# --------------------------------------------------
# Server startup
# --------------------------------------------------

if __name__ == "__main__":
    mcp.run(transport="stdio")
