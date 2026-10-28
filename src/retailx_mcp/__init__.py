from fastmcp import FastMCP
import psycopg2
from psycopg2.extras import RealDictCursor
import os
from datetime import date
from typing import Dict, List, Any

# Initialize FastMCP server
mcp = FastMCP("SalesManagementServer")

def get_db_connection():
    db_url = os.getenv("DATABASE_URL", "postgresql://postgres:password@localhost:5432/sales_db")
    return psycopg2.connect(db_url, cursor_factory=RealDictCursor)

@mcp.tool()
def add_sale(username: str, amount: float, sale_date: str, item_name: str, quantity: int = 1) -> str:
    """
    Adds a new sale record into the PostgreSQL sales database for a specific user.

    Args:
        username: The unique username of the user recording the sale.
        amount: The total monetary amount or price of the sale (e.g., 150.50).
        sale_date: The date when the sale occurred in exact YYYY-MM-DD format.
        item_name: The name, description, or title of the product or service sold.
        quantity: The quantity of items sold (defaults to 1 if not specified).

    Returns:
        A success confirmation message string indicating the sale has been saved.
    """
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO sales (username, sale_date, amount, item_name, quantity) VALUES (%s, %s, %s, %s, %s)",
            (username, sale_date, amount, item_name, quantity)
        )
        conn.commit()
        cur.close()
        conn.close()
        return f"Successfully recorded sale of ${amount} for item '{item_name}' on {sale_date} for user {username}."
    except Exception as e:
        return f"Error adding sale to database: {str(e)}"

@mcp.tool()
def get_sales_by_date(username: str, sale_date: str) -> List[Dict[str, Any]]:
    """
    Retrieves all sales transactions for a specific user on an exact single date from the PostgreSQL database.

    Args:
        username: The unique username whose sales records are to be retrieved.
        sale_date: The exact date to query in YYYY-MM-DD format.

    Returns:
        A list of dictionaries containing sale details (id, sale_date, amount, item_name, quantity) for that date.
    """
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT id, sale_date, amount, item_name, quantity FROM sales WHERE username = %s AND sale_date = %s ORDER BY sale_date DESC",
            (username, sale_date)
        )
        rows = cur.fetchall()
        cur.close()
        conn.close()

        results: List[Dict[str, Any]] = []
        for row in rows:
            r: Dict[str, Any] = dict(row) # type: ignore
            d = r.get('sale_date')
            if isinstance(d, date):
                r['sale_date'] = d.isoformat()
            if r.get('amount') is not None:
                r['amount'] = float(r['amount'])
            results.append(r)
        return results
    except Exception as e:
        return [{"error": str(e)}]

@mcp.tool()
def get_sales_range(username: str, start_date: str, end_date: str) -> List[Dict[str, Any]]:
    """
    Retrieves all sales records for a specific user within a date range (inclusive) from the PostgreSQL database.
    Use this when the user asks for sales from a particular starting day to a particular ending day.

    Args:
        username: The unique username whose sales records are requested.
        start_date: The beginning date of the range in YYYY-MM-DD format.
        end_date: The ending date of the range in YYYY-MM-DD format.

    Returns:
        A list of dictionaries representing all sales made between start_date and end_date.
    """
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT id, sale_date, amount, item_name, quantity FROM sales WHERE username = %s AND sale_date BETWEEN %s AND %s ORDER BY sale_date ASC",
            (username, start_date, end_date)
        )
        rows = cur.fetchall()
        cur.close()
        conn.close()

        results: List[Dict[str, Any]] = []
        for row in rows:
            r: Dict[str, Any] = dict(row) # type: ignore
            d = r.get('sale_date')
            if isinstance(d, date):
                r['sale_date'] = d.isoformat()
            if r.get('amount') is not None:
                r['amount'] = float(r['amount'])
            results.append(r)
        return results
    except Exception as e:
        return [{"error": str(e)}]

@mcp.tool()
def get_sales_chart_data(username: str, start_date: str, end_date: str) -> Dict[str, Any]:
    """
    Aggregates and formats sales data points specifically for visualization (line charts) across a given date range.
    Use this tool when the user requests to visualize, plot, or graph their sales over a time period.

    Args:
        username: The unique username requesting the chart visualization data.
        start_date: The start date of the visualization period in YYYY-MM-DD format.
        end_date: The end date of the visualization period in YYYY-MM-DD format.

    Returns:
        A dictionary containing structured data points with 'x_axis' (dates) and 'y_axis' (total sales amounts) ready for plotting.
    """
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT sale_date, SUM(amount) as total_amount
            FROM sales
            WHERE username = %s AND sale_date BETWEEN %s AND %s
            GROUP BY sale_date
            ORDER BY sale_date ASC
            """,
            (username, start_date, end_date)
        )
        rows = cur.fetchall()
        cur.close()
        conn.close()

        dates: List[str] = []
        amounts: List[float] = []
        for row in rows:
            r: Dict[str, Any] = dict(row) # type: ignore
            d = r.get('sale_date')
            if isinstance(d, date):
                dates.append(d.isoformat())
            elif d is not None:
                dates.append(str(d))

            tot = r.get('total_amount')
            if tot is not None:
                amounts.append(float(tot))

        return {
            "chart_type": "line",
            "x_axis": dates,
            "y_axis": amounts,
            "summary": f"Retrieved chart data from {start_date} to {end_date} with {len(dates)} data points."
        }
    except Exception as e:
        return {"error": str(e)}

if __name__ == "__main__":
    mcp.run()
