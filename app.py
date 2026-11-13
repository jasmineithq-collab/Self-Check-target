import asyncio
import json
import logging
import os
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import streamlit as st
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from streamlit.errors import StreamlitSecretNotFoundError

from mcp_db_server import SALES_TIMEZONE
from staff_roster import CODE_PATTERN, load_roster
from sales_assistants import answer_request


LOGGER = logging.getLogger(__name__)
SERVER = Path(__file__).with_name("mcp_db_server.py").resolve()


def setting(name: str, default: str = "") -> str:
    try:
        return st.secrets.get(name, os.environ.get(name, default))
    except StreamlitSecretNotFoundError:
        return os.environ.get(name, default)


def staff_code(mode: str) -> str:
    code = st.session_state.get("sales_staff_code", "")
    if code:
        if st.sidebar.button("Switch user", width="stretch"):
            del st.session_state["sales_staff_code"]
            st.rerun()
        return code

    with st.form("staff_sign_in"):
        entered = st.text_input("Staff ID", placeholder="Enter your staff code", max_chars=32)
        submitted = st.form_submit_button("Continue", type="primary")
    if submitted:
        code = entered.strip().upper()
        if not CODE_PATTERN.fullmatch(code):
            st.error("Enter a valid staff ID.")
        elif mode == "demo" and code not in load_roster(setting("STAFF_ROSTER_XLSX_B64"), setting("STAFF_ROSTER_PATH")):
            st.error("Staff ID was not found. Check the code and try again.")
        else:
            st.session_state["sales_staff_code"] = code
            st.rerun()
    st.stop()


def viewer_email(mode: str, code: str) -> str:
    if mode == "demo":
        return f"{code.lower()}@example.test"

    if not getattr(st.user, "is_logged_in", False):
        st.write("Sign in with your company Google account to view sales progress.")
        if st.button("Sign in with Google", type="primary"):
            st.login()
        st.stop()

    st.sidebar.button("Sign out", on_click=st.logout)
    claims = st.user.to_dict()
    email = claims.get("email")
    expires = claims.get("exp")
    if (
        claims.get("email_verified") is not True
        or not isinstance(email, str)
        or not email.strip()
        or not isinstance(expires, (int, float))
        or expires <= datetime.now(SALES_TIMEZONE).timestamp()
    ):
        st.error("Your sign-in could not be verified. Sign out and sign in again.")
        st.stop()
    return email.strip().casefold()


async def fetch_snapshot(mode: str, email: str, code: str, month: date) -> dict:
    env = {"SALES_MODE": mode, "SALES_VIEWER_EMAIL": email, "SALES_VIEWER_USERNAME": code}
    if mode == "demo":
        for name in ("STAFF_ROSTER_XLSX_B64", "STAFF_ROSTER_PATH"):
            if value := setting(name):
                env[name] = value
    if mode == "live":
        database_path = setting("SALES_DB_PATH")
        if not database_path:
            raise ValueError("Missing SALES_DB_PATH")
        env["SALES_DB_PATH"] = str(Path(database_path).expanduser().resolve())
    # Preserve the import path when the caller uses a workspace Python bundle.
    if "PYTHONPATH" in os.environ:
        env["PYTHONPATH"] = os.environ["PYTHONPATH"]
    params = StdioServerParameters(command=sys.executable, args=[str(SERVER)], env=env)
    async with asyncio.timeout(20):
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool("get_sales_progress", {"month_start": month.isoformat()})
    if result.isError or result.structuredContent is None:
        raise RuntimeError("Sales progress tool failed")
    return result.structuredContent


def money(sen: int | None) -> str:
    if sen is None:
        return "—"
    return f"RM {Decimal(sen) / 100:,.2f}"


def attainment(ordered: int, target: int | None) -> str:
    if not target:
        return "—"
    return f"{Decimal(ordered) / target:.1%}"


def display_metrics(rows: list[dict], manager: bool) -> dict:
    targets = [row["target_sen"] for row in rows if row["target_sen"] is not None]
    target = sum(targets) if len(targets) == len(rows) else None
    ordered = sum(row["ordered_sen"] for row in rows)
    order_count = sum(row["order_count"] for row in rows)
    remaining = max(target - ordered, 0) if target is not None else None

    st.subheader("Team progress" if manager else "My progress")
    columns = st.columns(4)
    columns[0].metric("Monthly target", money(target))
    columns[1].metric("Confirmed order value", money(ordered))
    columns[2].metric("Target achieved", attainment(ordered, target))
    columns[3].metric("Remaining to target", money(remaining))
    if target:
        st.progress(min(ordered / target, 1.0), text=f"{attainment(ordered, target)} of monthly target")
    st.caption(f"Confirmed orders · {order_count:,}")
    if target is None:
        message = (
            "Some team members are missing targets for this month."
            if manager else "No target is set for this month."
        )
        st.info(message)
    if manager:
        st.subheader("Direct reports")
        st.dataframe(
            [
                {
                    "Salesperson": row["name"],
                    "Staff ID": row["staff_id"],
                    "Area": row["area"],
                    "Target": money(row["target_sen"]),
                    "Confirmed order value": money(row["ordered_sen"]),
                    "Confirmed orders": row["order_count"],
                    "Target achieved": attainment(row["ordered_sen"], row["target_sen"]),
                }
                for row in rows
            ],
            hide_index=True,
            width="stretch",
        )
    return {
        "monthly_target": money(target),
        "confirmed_order_value": money(ordered),
        "confirmed_order_count": order_count,
        "target_achieved": attainment(ordered, target),
        "remaining_to_target": money(remaining),
    }


def display_orders(items: list[dict], manager: bool) -> None:
    st.subheader("Orders and items")
    if not items:
        st.info("No orders match this month and your sales scope.")
        return
    rows = []
    for item in items:
        row = {"Order": item["order_id"], "Date": item["order_date"]}
        if manager:
            row["Salesperson"] = f'{item["name"]} ({item["staff_id"]})'
        row.update({
            "Area": item["area"], "Customer": item["customer"], "Invoice": item["invoice"],
            "Item category": item["item_category"], "Item name": item["item_name"],
            "Status": item["status"].replace("_", " ").title(),
            "Line value (MYR)": float(Decimal(item["amount_sen"]) / 100),
        })
        rows.append(row)
    st.caption("Confirmed orders count toward the target. The table includes all visible order statuses.")
    st.dataframe(rows, hide_index=True, width="stretch", column_config={
        "Line value (MYR)": st.column_config.NumberColumn(format="%,.2f"),
    })


def display_assistant(snapshot: dict, summary: dict, month: date, mode: str) -> None:
    st.subheader("Ask an assistant")
    invoice = next((order["invoice"] for order in snapshot["orders"] if order["invoice"]), None)
    examples = "Sales: Orders by customer · HR: How is commission calculated?"
    if invoice:
        examples += f" · Logistics: POD for {invoice}"
    st.caption(examples)
    with st.form("sales_question", clear_on_submit=True):
        question = st.text_input("Question", placeholder="How are my orders tracking against target?", max_chars=500)
        submitted = st.form_submit_button("Ask")
    if not submitted:
        return
    assistant, answer = answer_request(question, snapshot, summary, month, mode, money)
    if not assistant:
        st.warning(answer)
        return
    st.markdown(f"**{assistant} assistant**")
    st.text(answer)

    api_key = setting("GOOGLE_API_KEY")
    if assistant != "Sales" or not api_key or "customer" in question.casefold():
        return

    from google import genai
    from google.genai import types

    context = {"month": month.isoformat()[:7], "orders_through": snapshot["through"], **summary}
    try:
        with st.spinner("Preparing an answer…"):
            with genai.Client(
                api_key=api_key, vertexai=False, http_options=types.HttpOptions(timeout=15000)
            ) as client:
                response = client.models.generate_content(
                    model=setting("GEMINI_MODEL", "gemini-3.8-flash"),
                    contents=json.dumps({"sales_summary": context, "task": "Summarize progress against target."}),
                    config=types.GenerateContentConfig(
                        system_instruction=(
                            "Answer briefly using only sales_summary. Use its exact figures. "
                            "A dash means the figure is unavailable. For requests beyond the supplied "
                            "totals, explain what information is available. Do not invent figures."
                        ),
                        max_output_tokens=800,
                    ),
                )
        if response.text:
            with st.expander("Gemini summary"):
                st.text(response.text)
    except Exception as error:
        LOGGER.warning("Sales assistant failed (%s)", type(error).__name__)
        st.warning("The AI assistant is temporarily unavailable.")


def main() -> None:
    st.set_page_config(page_title="Sales Target Check", page_icon="📈", layout="wide")
    st.title("Sales Target Check")
    mode = setting("SALES_MODE", "demo")
    if mode not in {"demo", "live"}:
        st.error("Sales mode is misconfigured. Please contact your administrator.")
        st.stop()
    if mode == "demo":
        st.info("Presentation demo · Areas, orders, targets, commission and POD records are samples.")
    try:
        code = staff_code(mode)
    except Exception as error:
        LOGGER.warning("Staff roster could not load (%s)", type(error).__name__)
        st.error("The staff list could not be loaded. Contact your administrator.")
        st.stop()
    email = viewer_email(mode, code)

    today = datetime.now(SALES_TIMEZONE).date()
    current = today.replace(day=1)
    months = [current]
    for _ in range(1 if mode == "demo" else 23):
        months.append((months[-1] - timedelta(days=1)).replace(day=1))
    month = st.sidebar.selectbox("Target month", months, format_func=lambda day: day.strftime("%B %Y"))
    st.sidebar.button("Refresh figures", width="stretch")

    try:
        with st.spinner("Loading sales figures…"):
            snapshot = asyncio.run(fetch_snapshot(mode, email, code, month))
    except Exception as error:
        LOGGER.warning("Sales figures could not load (%s)", type(error).__name__)
        st.error("Sales figures are unavailable. Try refreshing or contact your administrator.")
        st.stop()
    if not snapshot["authorized"]:
        st.error("This staff ID could not be validated for your account. Check the ID or switch users.")
        st.stop()
    if snapshot.get("configuration_required"):
        st.error("The sales data source needs area assignments and order details before it can show these figures.")
        st.stop()

    manager = snapshot["role"] == "sales_manager"
    st.sidebar.text(snapshot["name"])
    st.sidebar.caption(f"{'Sales manager' if manager else 'Salesperson'} · {code}")
    through = date.fromisoformat(snapshot["through"]).strftime("%d %b %Y")
    st.caption(f"{month:%B %Y} · Order date cutoff {through} · Malaysia time")
    if mode == "live":
        updated = snapshot["source_updated_at"]
        st.caption(f"Source last synced · {updated}" if updated else "Source last sync time unavailable")

    rows = snapshot["rows"]
    if not rows:
        st.info("No active direct reports are assigned to your account.")
        st.stop()
    if not manager:
        if not rows[0]["area"]:
            st.warning("Your staff ID has no assigned sales area.")
            st.stop()
        st.text(f'Sales area · {rows[0]["area"]}')
    summary = display_metrics(rows, manager)
    display_orders(snapshot["items"], manager)
    display_assistant(snapshot, summary, month, mode)


if __name__ == "__main__":
    main()
