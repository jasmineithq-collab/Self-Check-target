from datetime import date, timedelta


LOGISTICS_SCHEMA = """
CREATE TABLE shipments (
    order_id TEXT PRIMARY KEY REFERENCES sales_orders(order_id),
    tracking_id TEXT NOT NULL UNIQUE COLLATE NOCASE,
    carrier TEXT NOT NULL,
    expected_delivery TEXT NOT NULL,
    delivered_on TEXT,
    exception_reason TEXT
);
CREATE TABLE shipment_events (
    order_id TEXT NOT NULL REFERENCES shipments(order_id),
    sequence INTEGER NOT NULL,
    event_date TEXT NOT NULL,
    state TEXT NOT NULL,
    PRIMARY KEY (order_id, sequence)
);
CREATE TABLE order_refunds (
    order_id TEXT PRIMARY KEY REFERENCES sales_orders(order_id),
    refund_reference TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,
    amount_sen INTEGER NOT NULL CHECK (amount_sen >= 0),
    updated_on TEXT NOT NULL
);
CREATE TABLE area_inventory (
    area TEXT NOT NULL,
    sku TEXT NOT NULL,
    item_name TEXT NOT NULL,
    on_hand INTEGER NOT NULL CHECK (on_hand >= 0),
    reserved INTEGER NOT NULL CHECK (reserved BETWEEN 0 AND on_hand),
    updated_on TEXT NOT NULL,
    PRIMARY KEY (area, sku)
);
"""


def seed_shipment(connection, order_id: str, reference: str, state: str, ordered: date, today: date, amount: int):
    expected = ordered + timedelta(days=3)
    event_day = min(expected, today)
    delivered = event_day.isoformat() if state in {"Delivered", "Pending POD"} else None
    reason = "Delivery location closed" if state == "Delivery exception" else None
    connection.execute(
        "INSERT INTO shipments VALUES (?, ?, ?, ?, ?, ?)",
        (order_id, f"TRK-{reference}", "Demo Parcel Express", expected.isoformat(), delivered, reason),
    )
    latest = "Delivered · POD pending" if state == "Pending POD" else state
    connection.executemany("INSERT INTO shipment_events VALUES (?, ?, ?, ?)", [
        (order_id, 1, ordered.isoformat(), "Label created"),
        (order_id, 2, min(ordered + timedelta(days=1), today).isoformat(), "Dispatched"),
        (order_id, 3, event_day.isoformat(), latest),
    ])
    if reference.endswith(("-05", "-10")):
        refund_status = "Approved" if reference.endswith("-05") else "Requested"
        connection.execute("INSERT INTO order_refunds VALUES (?, ?, ?, ?, ?)",
                           (order_id, f"RFD-{reference}", refund_status, amount // 2, event_day.isoformat()))


def seed_inventory(connection, areas, items, today: date):
    connection.executemany("INSERT INTO area_inventory VALUES (?, ?, ?, ?, ?, ?)", [
        (area, f"SKU-{index + 1:03}", item, 20 + index * 11 + area_index * 7, (index * 3) % 19, today.isoformat())
        for area_index, area in enumerate(areas) for index, (_, item) in enumerate(items)
    ])
