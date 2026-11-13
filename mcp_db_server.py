import os
import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from mcp.server.fastmcp import FastMCP

from staff_roster import load_roster


SALES_TIMEZONE = ZoneInfo("Asia/Kuala_Lumpur")
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

DETAIL_SCHEMA = """
CREATE TABLE staff_areas (
    staff_id TEXT NOT NULL REFERENCES staff(staff_id),
    area TEXT NOT NULL,
    PRIMARY KEY (staff_id, area)
);
CREATE TABLE order_details (
    order_id TEXT PRIMARY KEY REFERENCES sales_orders(order_id),
    area TEXT NOT NULL,
    customer TEXT NOT NULL,
    invoice TEXT UNIQUE COLLATE NOCASE,
    pod_status TEXT,
    pod_reference TEXT
);
CREATE TABLE order_items (
    order_id TEXT NOT NULL REFERENCES sales_orders(order_id),
    line_number INTEGER NOT NULL,
    item_category TEXT NOT NULL,
    item_name TEXT NOT NULL,
    amount_sen INTEGER NOT NULL CHECK (amount_sen >= 0),
    PRIMARY KEY (order_id, line_number)
);
"""

DEMO_AREAS = ("Petaling Jaya", "Shah Alam", "Klang", "Puchong", "Subang Jaya", "Kajang", "Cheras")
DEMO_ITEMS = (
    ("Beverages", "Jasmine Green Tea 500 ml"),
    ("Snacks", "Sea Salt Potato Chips 80 g"),
    ("Pantry", "Instant Oats 500 g"),
    ("Beverages", "Sparkling Lemon Water 330 ml"),
    ("Pantry", "Jasmine Rice 5 kg"),
)


def seed_demo(connection: sqlite3.Connection, today: date) -> None:
    roster = load_roster(os.environ.get("STAFF_ROSTER_XLSX_B64", ""), os.environ.get("STAFF_ROSTER_PATH", ""))
    connection.executescript(SCHEMA + DETAIL_SCHEMA)
    connection.executemany(
        "INSERT INTO staff VALUES (?, ?, ?, ?, ?, 1)",
        [
            (staff.code, f"{staff.code.lower()}@example.test", staff.name,
             "salesperson" if staff.manager_code else "sales_manager", staff.manager_code)
            for staff in roster.values()
        ],
    )
    reps = sorted(staff.code for staff in roster.values() if staff.manager_code)
    month = today.replace(day=1)
    previous_month = (month - timedelta(days=1)).replace(day=1)
    for index, staff_id in enumerate(reps):
        area = DEMO_AREAS[index % len(DEMO_AREAS)]
        connection.execute("INSERT INTO staff_areas VALUES (?, ?)", (staff_id, area))
        target = (45_000 + (index % 7) * 5_000) * 100
        for period in (previous_month, month):
            connection.execute(
                "INSERT INTO sales_targets VALUES (?, ?, ?)",
                (staff_id, period.isoformat(), target),
            )
            progress = 68 + (index % 6) * 9 + (8 if period == previous_month else 0)
            ordered = target * progress // 100
            for order, amount in enumerate((ordered * 55 // 100, ordered - ordered * 55 // 100, 350_000), 1):
                reference = f"{period:%y%m}-{index + 1:03}-{order:02}"
                order_id = f"SO-{reference}"
                confirmed = order < 3
                order_date = min(period + timedelta(days=(order - 1) * 5), today)
                connection.execute(
                    "INSERT INTO sales_orders VALUES (?, ?, ?, ?, ?)",
                    (order_id, staff_id, order_date.isoformat(), "confirmed" if confirmed else "pending", amount),
                )
                connection.execute(
                    "INSERT INTO order_details VALUES (?, ?, ?, ?, ?, ?)",
                    (order_id, area, f"{area} {'Retail' if order == 1 else 'Wholesale'} (Demo)",
                     f"INV-{reference}" if confirmed else None,
                     ("Delivered" if order == 1 else "Pending POD") if confirmed else None,
                     f"POD-{reference}" if order == 1 else None),
                )
                items = ((index + order) % len(DEMO_ITEMS), (index + order + 1) % len(DEMO_ITEMS))
                first_amount = amount * 70 // 100
                connection.executemany(
                    "INSERT INTO order_items VALUES (?, ?, ?, ?, ?)",
                    [(order_id, 1, *DEMO_ITEMS[items[0]], first_amount),
                     (order_id, 2, *DEMO_ITEMS[items[1]], amount - first_amount)],
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
    connection: sqlite3.Connection, email: str, month: date, today: date, username: str = ""
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
    if staff is None or (username and staff["staff_id"].upper() != username):
        return {"authorized": False}

    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    has_details = {"staff_areas", "order_details", "order_items"} <= tables
    if not has_details:
        # Previous snapshots support totals, but cannot enforce an area assignment.
        return {"authorized": True, "configuration_required": True}
    params = {"viewer": staff["staff_id"], "role": staff["role"],
              "month": month.isoformat(), "cutoff": cutoff.isoformat()}
    scope = """
        WITH visible_staff AS (
            SELECT staff_id, name FROM staff
            WHERE active = 1 AND role = 'salesperson'
                AND ((:role = 'salesperson' AND staff_id = :viewer)
                  OR (:role = 'sales_manager' AND manager_id = :viewer))
        ), visible_orders AS (
            SELECT o.*, v.name, d.area, d.customer, d.invoice, d.pod_status, d.pod_reference
            FROM sales_orders o JOIN visible_staff v USING (staff_id)
            LEFT JOIN order_details d USING (order_id)
            WHERE o.order_date >= :month AND o.order_date < :cutoff
              AND (:role = 'sales_manager' OR EXISTS (
                  SELECT 1 FROM staff_areas a WHERE a.staff_id = :viewer AND a.area = d.area
              ))
        )
    """
    rows = connection.execute(
        scope + """
        , order_totals AS (
            SELECT staff_id, SUM(amount_sen) AS ordered_sen, COUNT(*) AS order_count
            FROM visible_orders WHERE status = 'confirmed' GROUP BY staff_id
        )
        SELECT v.staff_id, v.name, t.target_sen,
               COALESCE(o.ordered_sen, 0) AS ordered_sen,
               COALESCE(o.order_count, 0) AS order_count,
               (SELECT group_concat(area, ', ') FROM staff_areas WHERE staff_id = v.staff_id) AS area
        FROM visible_staff v
        LEFT JOIN sales_targets t ON t.staff_id = v.staff_id AND t.month_start = :month
        LEFT JOIN order_totals o ON o.staff_id = v.staff_id
        ORDER BY v.name, v.staff_id
        """,
        params,
    ).fetchall()
    orders = connection.execute(
        scope + "SELECT * FROM visible_orders ORDER BY order_date DESC, order_id", params
    ).fetchall()
    items = connection.execute(
        scope + """
        SELECT o.order_id, o.name, o.staff_id, o.area, o.customer, o.invoice, o.status, o.order_date,
               i.item_category, i.item_name, COALESCE(i.amount_sen, o.amount_sen) AS amount_sen
        FROM visible_orders o LEFT JOIN order_items i USING (order_id)
        ORDER BY o.order_date DESC, o.order_id, i.line_number
        """, params,
    ).fetchall()
    sync = connection.execute("SELECT updated_at FROM sales_sync WHERE id = 1").fetchone()
    return {
        "authorized": True,
        "name": staff["name"],
        "role": staff["role"],
        "through": (cutoff - timedelta(days=1)).isoformat(),
        "source_updated_at": sync["updated_at"] if sync else None,
        "rows": [dict(row) for row in rows],
        "orders": [dict(row) for row in orders],
        "items": [dict(row) for row in items],
    }


@mcp.tool()
def get_sales_progress(month_start: str) -> dict[str, Any]:
    """Read targets and confirmed order totals for the current verified viewer."""
    # Streamlit binds these values when it launches this private stdio process.
    email = os.environ["SALES_VIEWER_EMAIL"].strip().casefold()
    username = os.environ.get("SALES_VIEWER_USERNAME", "")
    mode = os.environ["SALES_MODE"]
    today = datetime.now(SALES_TIMEZONE).date()
    with closing(open_sales_database(mode, today)) as connection:
        return load_snapshot(connection, email, date.fromisoformat(month_start), today, username)


if __name__ == "__main__":
    mcp.run(transport="stdio")
