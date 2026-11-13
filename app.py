import asyncio
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

from mcp_db_server import RETURNS_URI, SALES_TIMEZONE
from staff_roster import CODE_PATTERN, load_roster
from sales_assistants import guardrail_message, mask_pii


LOGGER = logging.getLogger(__name__)
SERVER = Path(__file__).with_name("mcp_db_server.py").resolve()


def setting(name: str, default: str = "") -> str:
    try:
        return st.secrets.get(name, os.environ.get(name, default))
    except StreamlitSecretNotFoundError:
        return os.environ.get(name, default)


def clear_chat_history() -> None:
    st.session_state.pop("sales_chat", None)
    st.session_state.pop("sales_pending_question", None)


def staff_code(mode: str) -> str:
    code = st.session_state.get("sales_staff_code", "")
    if code:
        if st.sidebar.button("Switch user", width="stretch"):
            del st.session_state["sales_staff_code"]
            clear_chat_history()
            st.session_state.pop("sales_show_all", None)
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


async def fetch_snapshot(mode: str, email: str, code: str, month: date, include_data: bool,
                         include_policy: bool = False, *, evaluation: bool = False) -> dict:
    env = {"SALES_MODE": mode, "SALES_VIEWER_EMAIL": email, "SALES_VIEWER_USERNAME": code}
    if mode == "demo" and evaluation:
        env["SALES_EVAL_FIXTURE"] = "1"
    elif mode == "demo":
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
                tools = await session.list_tools()
                if "get_sales_progress" not in {tool.name for tool in tools.tools}:
                    raise RuntimeError("Required MCP tool is unavailable")
                result = await session.call_tool(
                    "get_sales_progress", {"month_start": month.isoformat(), "include_data": include_data}
                )
                if result.isError or result.structuredContent is None:
                    raise RuntimeError("Sales progress tool failed")
                snapshot = result.structuredContent
                if include_policy and snapshot.get("authorized"):
                    resources = await session.list_resources()
                    resource = next((item for item in resources.resources if str(item.uri) == RETURNS_URI), None)
                    if resource:
                        policy = await session.read_resource(resource.uri)
                        snapshot["return_policy"] = "\n".join(part.text for part in policy.contents if hasattr(part, "text"))
                return snapshot


def money(sen: int | None) -> str:
    if sen is None:
        return "—"
    return f"RM {Decimal(sen) / 100:,.2f}"


def attainment(ordered: int, target: int | None) -> str:
    if not target:
        return "—"
    return f"{Decimal(ordered) / target:.1%}"


def sales_totals(rows: list[dict]) -> tuple[int | None, int, int]:
    targets = [row["target_sen"] for row in rows if row["target_sen"] is not None]
    target = sum(targets) if len(targets) == len(rows) else None
    ordered = sum(row["ordered_sen"] for row in rows)
    order_count = sum(row["order_count"] for row in rows)
    return target, ordered, order_count


def sales_summary(totals: tuple[int | None, int, int]) -> dict:
    target, ordered, order_count = totals
    remaining = max(target - ordered, 0) if target is not None else None
    return {
        "monthly_target": money(target),
        "confirmed_order_value": money(ordered),
        "confirmed_order_count": order_count,
        "target_achieved": attainment(ordered, target),
        "remaining_to_target": money(remaining),
    }


def display_metrics(rows: list[dict], totals: tuple[int | None, int, int], summary: dict, manager: bool) -> None:
    target, ordered, order_count = totals

    st.subheader("Team progress" if manager else "My progress")
    columns = st.columns(4)
    columns[0].metric("Monthly target", summary["monthly_target"])
    columns[1].metric("Confirmed order value", summary["confirmed_order_value"])
    columns[2].metric("Target achieved", summary["target_achieved"])
    columns[3].metric("Remaining to target", summary["remaining_to_target"])
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


def submit_question(scope: tuple) -> None:
    question = st.session_state["sales_question_input"].strip()
    st.session_state["sales_pending_question"] = (scope, question)


def suggested_questions(scope: tuple) -> None:
    st.sidebar.subheader("Suggested Questions")
    for question in (
        "Which invoices have been delivered?",
        "Which deliveries have exceptions or pending POD?",
        "Show the stock available in my area.",
        "Which orders have refunds in progress?",
        "What is the return window for damaged items?",
        "How am I tracking against my sales target?",
        "How is commission calculated?",
    ):
        st.sidebar.button(question, key=f"suggestion:{question}", width="stretch",
                          on_click=queue_suggestion, args=(scope, question))


def queue_suggestion(scope: tuple, question: str) -> None:
    st.session_state["sales_pending_question"] = (scope, question)


def display_chat(scope: tuple, history: list[dict]) -> None:
    st.subheader("Ask an assistant")
    st.caption("Ask about shipments and POD, stock, refunds, returns, sales, or commission.")
    st.toggle("Show all", key="sales_show_all", help="Show the full dashboard for the selected month.")
    for entry in history:
        render_message(entry)
    with st.container():
        st.chat_input("Ask in your own words", max_chars=500, key="sales_question_input", on_submit=submit_question, args=(scope,))


def render_message(entry: dict) -> None:
    with st.chat_message(entry["role"]):
        st.text(entry["text"], width="stretch")
        if evidence := entry.get("evidence"):
            with st.expander("Sources · " + ", ".join(evidence)):
                for source, results in evidence.items():
                    st.markdown(f"**{source}**")
                    for result in dict.fromkeys(results):
                        st.text(result, width="stretch")

def agent_reply(question, history, snapshot, month, mode, api_key, blocked):
    if blocked:
        return blocked, {}
    if not api_key:
        return "The AI assistant needs GOOGLE_API_KEY in Streamlit Secrets. You can view the dashboard with Show all.", {}
    if snapshot.get("configuration_required"):
        return "The sales source needs area assignments and order details. Contact your administrator.", {}
    if not snapshot["rows"] or (snapshot["role"] == "salesperson" and not snapshot["rows"][0]["area"]):
        return "There is no sales area or team assigned to this account. Contact your administrator.", {}
    try:
        from enterprise_agents import MODEL, coordinate

        summary = sales_summary(sales_totals(snapshot["rows"]))
        with st.spinner("Consulting the specialists…"):
            return asyncio.run(coordinate(
                question, history, snapshot, summary, month, mode, money, api_key, setting("GEMINI_MODEL", MODEL)
            ))
    except Exception as error:
        LOGGER.warning("Sales coordinator failed (%s)", type(error).__name__)
        return "The AI assistant is temporarily unavailable. Try again, or use Show all to view your figures.", {}


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

    scope = (mode, code, email, month)
    saved_scope, history = st.session_state.get("sales_chat", (None, []))
    if saved_scope != scope:
        history = []
        st.session_state["sales_chat"] = (scope, history)
    pending_scope, pending_question = st.session_state.pop("sales_pending_question", (None, ""))
    question = pending_question if pending_scope == scope else ""
    blocked = guardrail_message(question) if question else ""
    question = mask_pii(question)
    api_key = setting("GOOGLE_API_KEY")
    show_all = st.session_state.get("sales_show_all", False)
    include_data = bool(show_all or (question and api_key and not blocked))

    try:
        with st.spinner("Loading figures…" if include_data else "Checking staff access…"):
            snapshot = asyncio.run(fetch_snapshot(mode, email, code, month, include_data, bool(question and not blocked and api_key)))
    except Exception as error:
        LOGGER.warning("Sales figures could not load (%s)", type(error).__name__)
        st.error("Sales figures are unavailable. Try refreshing or contact your administrator.")
        st.stop()
    if not snapshot["authorized"]:
        clear_chat_history()
        st.error("This staff ID could not be validated for your account. Check the ID or switch users.")
        st.stop()
    display_chat(scope, history)
    if question:
        user_entry = {"role": "user", "text": question, "blocked": bool(blocked)}
        render_message(user_entry)
        answer, evidence = agent_reply(question, history, snapshot, month, mode, api_key, blocked)
        assistant_entry = {"role": "assistant", "text": answer, "evidence": evidence, "blocked": bool(blocked)}
        render_message(assistant_entry)
        history = [*history, user_entry, assistant_entry][-20:]
        st.session_state["sales_chat"] = (scope, history)
    st.sidebar.button("Clear chat history", on_click=clear_chat_history, width="stretch", disabled=not history)
    suggested_questions(scope)
    manager = snapshot.get("role") == "sales_manager"
    if snapshot.get("name"):
        st.sidebar.text(snapshot["name"])
        st.sidebar.caption(f"{'Sales manager' if manager else 'Salesperson'} · {code}")
    if not show_all:
        return
    if snapshot.get("configuration_required"):
        st.error("The sales data source needs area assignments and order details before it can show these figures.")
        return

    rows = snapshot["rows"]
    if not rows:
        st.info("No active direct reports are assigned to your account.")
        return
    if not manager and not rows[0]["area"]:
        st.warning("Your staff ID has no assigned sales area.")
        return
    totals = sales_totals(rows)
    summary = sales_summary(totals)
    through = date.fromisoformat(snapshot["through"]).strftime("%d %b %Y")
    st.caption(f"{month:%B %Y} · Order date cutoff {through} · Malaysia time")
    if mode == "live":
        updated = snapshot["source_updated_at"]
        st.caption(f"Source last synced · {updated}" if updated else "Source last sync time unavailable")

    if not manager:
        st.text(f'Sales area · {rows[0]["area"]}')
    display_metrics(rows, totals, summary, manager)
    display_orders(snapshot["items"], manager)


if __name__ == "__main__":
    main()
