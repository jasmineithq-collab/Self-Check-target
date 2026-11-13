import os
import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from mcp.server.fastmcp import FastMCP


SALES_TIMEZONE = ZoneInfo("Asia/Kuala_Lumpur")
DEMO_USERS = {
    "Salesperson · Aina": "aina@example.test",
    "Sales manager · Mei": "mei@example.test",
}
mcp = FastMCP("Sales targets")

# Reference schema for the data adapter. Amounts are stored in sen to keep
# aggregation exact. Order dates are local calendar dates in YYYY-MM-DD format.
SCHEMA = """
CREATE TABLE staff (
    staff_id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
    name TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('salesperson', 'sales_manager')),
    manager_id TEXT REFERENCES staff(staff_id),
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1))
);
CREATE TABLE sales_targets (
    staff_id TEXT NOT NULL REFERENCES staff(staff_id),
    month_start TEXT NOT NULL,
    target_sen INTEGER NOT NULL CHECK (target_sen >= 0),
    PRIMARY KEY (staff_id, month_start)
);
CREATE TABLE sales_orders (
    order_id TEXT PRIMARY KEY,
    staff_id TEXT NOT NULL REFERENCES staff(staff_id),
    order_date TEXT NOT NULL,
    status TEXT NOT NULL,
    amount_sen INTEGER NOT NULL CHECK (amount_sen >= 0)
);
CREATE INDEX orders_by_staff_date ON sales_orders (staff_id, order_date);
CREATE TABLE sales_sync (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    updated_at TEXT NOT NULL
);
"""


def seed_demo(connection: sqlite3.Connection, today: date) -> None:
    connection.executescript(SCHEMA)
    connection.executemany(
        "INSERT INTO staff VALUES (?, ?, ?, ?, ?, 1)",
        [
            ("manager", "mei@example.test", "Mei Tan", "sales_manager", None),
            ("other-manager", "farah@example.test", "Farah Noor", "sales_manager", None),
            ("aina", "aina@example.test", "Aina Rahman", "salesperson", "manager"),
            ("daniel", "daniel@example.test", "Daniel Lim", "salesperson", "manager"),
            ("ravi", "ravi@example.test", "Ravi Kumar", "salesperson", "manager"),
            ("other", "sam@example.test", "Sam Lee", "salesperson", "other-manager"),
        ],
    )
    month = today.replace(day=1)
    previous_month = (month - timedelta(days=1)).replace(day=1)
    for period in (previous_month, month):
        for staff_id, target, confirmed in [
            ("aina", 50_000, 38_500),
            ("daniel", 55_000, 57_500),
            ("ravi", 45_000, 27_500),
            ("other", 80_000, 90_000),
        ]:
            connection.execute(
                "INSERT INTO sales_targets VALUES (?, ?, ?)",
                (staff_id, period.isoformat(), target * 100),
            )
            connection.executemany(
                "INSERT INTO sales_orders VALUES (?, ?, ?, ?, ?)",
                [
                    (f"{period}-{staff_id}-1", staff_id, period.isoformat(), "confirmed", confirmed * 60),
                    (f"{period}-{staff_id}-2", staff_id, period.isoformat(), "confirmed", confirmed * 40),
                    (f"{period}-{staff_id}-3", staff_id, period.isoformat(), "pending", 350_000),
                ],
            )
    connection.commit()


def open_sales_database(mode: str, today: date) -> sqlite3.Connection:
    if mode == "demo":
        connection = sqlite3.connect(":memory:")
        seed_demo(connection, today)
        return connection
    if mode != "live":
        raise ValueError("Invalid sales mode")
    path = Path(os.environ["SALES_DB_PATH"]).expanduser().resolve()
    return sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)


def load_snapshot(
    connection: sqlite3.Connection, email: str, month: date, today: date
) -> dict[str, Any]:
    if month.day != 1 or month > today:
        raise ValueError("Expected the first day of a current or past month")
    next_month = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
    cutoff = min(next_month, today + timedelta(days=1))

    connection.row_factory = sqlite3.Row
    connection.execute("BEGIN")
    staff = connection.execute(
        "SELECT staff_id, name, role FROM staff WHERE email = ? AND active = 1", (email,)
    ).fetchone()
    if staff is None:
        return {"authorized": False}

    rows = connection.execute(
        """
        WITH visible_staff AS (
            SELECT staff_id, name FROM staff
            WHERE active = 1 AND role = 'salesperson'
                AND ((:role = 'salesperson' AND staff_id = :viewer)
                  OR (:role = 'sales_manager' AND manager_id = :viewer))
        ), order_totals AS (
            SELECT sales_orders.staff_id, SUM(amount_sen) AS ordered_sen, COUNT(*) AS order_count
            FROM sales_orders JOIN visible_staff USING (staff_id)
            WHERE status = 'confirmed' AND order_date >= :month AND order_date < :cutoff
            GROUP BY sales_orders.staff_id
        )
        SELECT v.name, t.target_sen,
               COALESCE(o.ordered_sen, 0) AS ordered_sen,
               COALESCE(o.order_count, 0) AS order_count
        FROM visible_staff v
        LEFT JOIN sales_targets t ON t.staff_id = v.staff_id AND t.month_start = :month
        LEFT JOIN order_totals o ON o.staff_id = v.staff_id
        ORDER BY v.name, v.staff_id
        """,
        {"viewer": staff["staff_id"], "role": staff["role"],
         "month": month.isoformat(), "cutoff": cutoff.isoformat()},
    ).fetchall()
    sync = connection.execute("SELECT updated_at FROM sales_sync WHERE id = 1").fetchone()
    return {
        "authorized": True,
        "name": staff["name"],
        "role": staff["role"],
        "through": (cutoff - timedelta(days=1)).isoformat(),
        "source_updated_at": sync["updated_at"] if sync else None,
        "rows": [dict(row) for row in rows],
    }


@mcp.tool()
def get_sales_progress(month_start: str) -> dict[str, Any]:
    """Read targets and confirmed order totals for the current verified viewer."""
    # Streamlit binds these values when it launches this private stdio process.
    email = os.environ["SALES_VIEWER_EMAIL"].strip().casefold()
    mode = os.environ["SALES_MODE"]
    today = datetime.now(SALES_TIMEZONE).date()
    with closing(open_sales_database(mode, today)) as connection:
        return load_snapshot(connection, email, date.fromisoformat(month_start), today)


if __name__ == "__main__":
    mcp.run(transport="stdio")
