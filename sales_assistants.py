import re
from collections import defaultdict
from datetime import date


UNSAFE_REQUEST = re.compile(
    r"\b(?:ignore|override|bypass|forget)\b.{0,80}\b(?:instructions?|rules?|guardrails?|access|permissions?)\b"
    r"|\b(?:system prompt|secrets?|credentials?|passwords?|api keys?|raw sql|execute sql|other teams?|another area)\b"
    r"|\b(?:delete|drop|update|change|insert|alter|cancel|edit)\s+(?:(?:the|an?|my|our|all)\s+)?"
    r"(?:records?|orders?|targets?|invoices?|tables?|users?|commission|data|pod)\b",
    re.IGNORECASE,
)
ORDER_REF = re.compile(r"\bSO(?:[-/][A-Z0-9]+|\d[A-Z0-9]*)(?:[-/][A-Z0-9]+)*\b", re.IGNORECASE)
INVOICE_REF = re.compile(r"\bINV(?:[-/][A-Z0-9]+|\d[A-Z0-9]*)(?:[-/][A-Z0-9]+)*\b", re.IGNORECASE)
INVOICE_NUMBER = re.compile(r"\b(?:invoice|inv|invois)\s*(?:#|no\.?\s*)?([A-Z0-9/_-]*\d[A-Z0-9/_-]*)\b", re.IGNORECASE)
OUT_OF_SCOPE = "That question is outside the supported topics. Ask about commission, sales, shipments and POD, stock, refunds, or returns."
UNRELATED_TASK = re.compile(r"\b(?:write|compose|tell|generate)\b.{0,35}\b(?:poem|joke|song|story)\b|\b(?:court orders?|target shooting|commission a painting)\b", re.IGNORECASE)
PII = (
    (re.compile(r"\b[\w.+-]+@[\w.-]+\.[a-z]{2,}\b", re.IGNORECASE), "[email redacted]"),
    (re.compile(r"(?<![\w-])\d{6}[- ]\d{2}[- ]\d{4}(?![\w-])"), "[ID redacted]"),
    (re.compile(r"(?<![\w-])(?:\d[ -]?){12,18}\d(?![\w-])"), "[number redacted]"),
    (re.compile(r"(?<![\w-])(?:\+?60[ -]?1\d|01\d)[ -]?\d{3,4}[ -]?\d{4}(?![\w-])"), "[phone redacted]"),
    (re.compile(r"\b(?:recipient|address|passport|national ID|NRIC)\s*[:=]\s*[^\n;,.!?]+", re.IGNORECASE), "[personal detail redacted]"),
)


def mask_pii(text: str) -> str:
    for pattern, replacement in PII:
        text = pattern.sub(replacement, text)
    return text


def guardrail_message(question: str) -> str:
    """Reject unsafe input before it reaches an agent."""
    question = " ".join(question.split())
    if not question or len(question) > 500:
        return "Enter a question up to 500 characters."
    if UNSAFE_REQUEST.search(question) or re.search(
        r"<\/?(?:system|developer|assistant)>|\[/?INST\]|\b(?:jailbreak|disregard previous|developer mode)\b",
        question, re.IGNORECASE,
    ):
        return "This request is outside the assistant's read-only access. Ask about visible orders, commission policy, or an invoice POD."
    if UNRELATED_TASK.search(question):
        return OUT_OF_SCOPE
    return ""


def find_reference(question, orders, field, pattern, argument=None):
    match = pattern.search(question)
    if match:
        return match[0]
    if argument and (match := argument.search(question)):
        return match[1]
    # Live exports can use reference formats other than the demo's INV and SO codes.
    for order in orders:
        reference = order[field]
        if reference and re.search(rf"(?<![\w/-]){re.escape(reference)}(?![\w/-])", question, re.IGNORECASE):
            return reference
    return None


def matching_order(orders, field, reference):
    return next((order for order in orders if (order[field] or "").casefold() == reference.casefold()), None)


def invoice_answer(question, invoice, order_id, orders, month, money):
    if invoice or order_id:
        order = matching_order(orders, "invoice", invoice) if invoice else matching_order(orders, "order_id", order_id)
        if order is None:
            return "That reference is unavailable in your access scope for the selected month."
        if not order["invoice"]:
            return f"Order {order['order_id']} has no linked invoice."
        return (
            f"Invoice {order['invoice']} · Order {order['order_id']}\n"
            f"Customer · {order['customer'] or 'Unavailable'}\n"
            f"Linked order value · {money(order['amount_sen'])}"
        )
    invoiced = [order for order in orders if order["invoice"]]
    if re.search(r"\bcustomers?\b", question, re.IGNORECASE) or mentions_customer(question, invoiced):
        return customer_answer(question, invoiced, month, money, "Invoiced order value")
    total = sum(order["amount_sen"] for order in invoiced)
    answer = f"{month:%B %Y} · {len(invoiced)} invoices · {money(total)} in linked order value"
    if invoiced and re.search(r"\b(?:list|show|which)\b", question, re.IGNORECASE):
        lines = [f"{order['invoice']} · {order['customer'] or 'Unavailable'} · {money(order['amount_sen'])}"
                 for order in invoiced[:10]]
        if len(invoiced) > 10:
            lines.append(f"Showing 10 of {len(invoiced)} invoices.")
        answer += "\n" + "\n".join(lines)
    return answer


def sales_answer(question, order_id, orders, summary, month, money):
    if order_id:
        order = matching_order(orders, "order_id", order_id)
        if order is None:
            return "That order is unavailable in your access scope for the selected month."
        return (
            f"Order {order['order_id']} · {order['status'].title()} · {order['order_date']}\n"
            f"Customer · {order['customer'] or 'Unavailable'}\n"
            f"Order value · {money(order['amount_sen'])}\n"
            f"Invoice · {order['invoice'] or 'Unavailable'}"
        )
    requested_statuses = [status for status in ("pending", "cancelled", "confirmed")
                          if re.search(rf"\b{status}\b", question, re.IGNORECASE)]
    all_orders = not requested_statuses and re.search(r"\b(?:all|every)\s+(?:sales\s+)?orders?\b", question, re.IGNORECASE)
    statuses = requested_statuses or ["confirmed"]
    selected = orders if all_orders else [order for order in orders if order["status"] in statuses]
    label = "All orders" if all_orders else " and ".join(statuses).capitalize() + " orders"
    if re.search(r"\bcustomers?\b", question, re.IGNORECASE) or mentions_customer(question, orders):
        return customer_answer(question, selected, month, money, label)
    list_orders = re.search(r"\b(?:list|which)\b", question, re.IGNORECASE)
    if all_orders or statuses != ["confirmed"] or list_orders:
        answer = f"{month:%B %Y} · {label} · {len(selected)} · {money(sum(order['amount_sen'] for order in selected))}"
        if list_orders and selected:
            lines = [f"{order['order_id']} · {order['status'].title()} · {money(order['amount_sen'])}" for order in selected[:10]]
            if len(selected) > 10:
                lines.append(f"Showing 10 of {len(selected)} orders.")
            answer += "\n" + "\n".join(lines)
        return answer
    return (
        f"{month:%B %Y} · {summary['confirmed_order_count']} confirmed orders\n"
        f"Order value {summary['confirmed_order_value']} · Target {summary['monthly_target']}\n"
        f"Achieved {summary['target_achieved']} · Remaining {summary['remaining_to_target']}"
    )


def mentions_customer(question, orders):
    query = question.casefold()
    return any(order["customer"] and order["customer"].removesuffix(" (Demo)").casefold() in query for order in orders)


def customer_answer(question, orders, month, money, label):
    totals = defaultdict(int)
    for order in orders:
        if order["customer"]:
            totals[order["customer"]] += order["amount_sen"]
    query = question.casefold()
    named = [name for name in totals if name.removesuffix(" (Demo)").casefold() in query]
    specific = re.search(r"\bcustomer\s*(?:named\s+|:\s*)\S|\bfor customer\s+\S", question, re.IGNORECASE)
    if specific and not named:
        return "No matching orders for that customer are visible in the selected month."
    if not totals:
        return "No matching customer orders are visible in the selected month."
    ranked = sorted(named or totals, key=lambda name: (-totals[name], name.casefold()))
    lines = [f"{name} · {money(totals[name])}" for name in ranked[:10]]
    if len(ranked) > 10:
        lines.append(f"Showing the 10 highest of {len(ranked)} visible customer totals.")
    return f"{label} by customer · {month:%B %Y}\n" + "\n".join(lines)


def hr_answer(rows, mode, money):
    if mode != "demo":
        return "An approved commission policy has not been configured. Contact HR for the current policy."
    eligible = [row for row in rows if row["target_sen"] and row["ordered_sen"] >= row["target_sen"]]
    estimate = sum((row["ordered_sen"] * 2 + 50) // 100 for row in eligible)
    missing = sum(not row["target_sen"] for row in rows)
    answer = (
        "Sample policy · A salesperson earns 2% of their confirmed monthly order value once they reach "
        "100% of their own target. Pending and cancelled orders are excluded.\n"
        f"Sample commission for the visible scope · {money(estimate)}"
    )
    if missing:
        answer += f"\n{missing} salesperson(s) could not be assessed without a positive target."
    return answer


def logistics_answer(question, invoice, order_id, orders):
    if not invoice and not order_id:
        if re.search(r"\b(?:invoices|how many|which|list)\b", question, re.IGNORECASE):
            invoiced = [order for order in orders if order["invoice"]]
            requested = re.search(r"\b(?:delivered|pending pod|in transit|delivery exception)\b", question, re.IGNORECASE)
            if requested:
                status = requested[0].casefold()
                matched = [order for order in invoiced if (order["pod_status"] or "").casefold() == status]
                answer = f"{len(matched)} visible invoices · {requested[0].capitalize()}"
                if matched and re.search(r"\b(?:which|list|show)\b", question, re.IGNORECASE):
                    answer += "\n" + "\n".join(order["invoice"] for order in matched[:10])
                    if len(matched) > 10:
                        answer += f"\nShowing 10 of {len(matched)} invoices."
                return answer
            statuses = defaultdict(int)
            for order in invoiced:
                statuses[order["pod_status"] or "Unavailable"] += 1
            if not statuses:
                return "No invoices are visible in the selected month."
            lines = [f"{status} · {count}" for status, count in sorted(statuses.items())]
            return "Visible invoices by delivery / POD status\n" + "\n".join(lines)
        return "Include an invoice number from the orders table so I can look up its delivery and POD."
    match = matching_order(orders, "invoice", invoice) if invoice else matching_order(orders, "order_id", order_id)
    if match is None:
        return "That reference is unavailable in your access scope for the selected month."
    return (
        f"Invoice {match['invoice'] or 'Unavailable'} · Order {match['order_id']}\n"
        f"Delivery / POD status · {match['pod_status'] or 'Unavailable'}\n"
        f"POD reference · {match['pod_reference'] or 'Unavailable'}"
    )
