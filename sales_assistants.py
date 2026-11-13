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
INTENTS = {
    "Logistics": re.compile(r"\b(?:pod|proof of delivery|invoice|delivery status)\b", re.IGNORECASE),
    "HR": re.compile(r"\b(?:commission|incentive|hr|commission policy)\b", re.IGNORECASE),
    "Sales": re.compile(r"\b(?:sales|orders?|targets?|customers?|amount|progress|achievement|attainment|remaining)\b", re.IGNORECASE),
}
INVOICE_PATTERN = re.compile(r"\bINV(?:[-/][A-Z0-9]+|\d[A-Z0-9]*)(?:[-/][A-Z0-9]+)*\b", re.IGNORECASE)
INVOICE_ARGUMENT = re.compile(r"\binvoice\s*#?\s*([A-Z0-9][A-Z0-9/_-]{0,39})\b", re.IGNORECASE)


def answer_request(question, snapshot, summary, month: date, mode: str, money):
    question = " ".join(question.split())
    if not question or len(question) > 500:
        return None, "Enter one question up to 500 characters."
    if UNSAFE_REQUEST.search(question):
        return None, "This request is outside the assistant's read-only access. Ask about visible orders, commission policy, or an invoice POD."

    prefix = re.match(r"^(sales|hr|logistics)\s*:\s*(.+)$", question, re.IGNORECASE)
    if prefix:
        assistant = next(name for name in INTENTS if name.casefold() == prefix[1].casefold())
        question = prefix[2]
    elif INTENTS["HR"].search(question) and INTENTS["Logistics"].search(question):
        return None, "Ask one question at a time, or start with Sales:, HR:, or Logistics:."
    else:
        assistant = next((name for name, intent in INTENTS.items() if intent.search(question)), None)

    if assistant == "Logistics":
        return assistant, logistics_answer(question, snapshot["orders"])
    if assistant == "HR":
        return assistant, hr_answer(snapshot["rows"], mode, money)
    if assistant == "Sales":
        return assistant, sales_answer(question, snapshot["orders"], summary, month, money)
    return None, "Ask about sales and targets, commission policy, or proof of delivery for an invoice."


def sales_answer(question, orders, summary, month, money):
    if re.search(r"\bcustomers?\b", question, re.IGNORECASE):
        totals = defaultdict(int)
        for order in orders:
            if order["status"] == "confirmed" and order["customer"]:
                totals[order["customer"]] += order["amount_sen"]
        named = [name for name in totals if name.casefold() in question.casefold()]
        if not named:
            named = [name for name in totals if name.removesuffix(" (Demo)").casefold() in question.casefold()]
        specific = re.search(r"\bcustomer\s*(?:named\s+|:\s*)\S|\bfor customer\s+\S", question, re.IGNORECASE)
        if specific and not named:
            return "No confirmed orders for that customer are visible in the selected month."
        if not totals:
            return "No confirmed customer orders are visible in the selected month."
        ranked = sorted(named or totals, key=lambda name: (-totals[name], name.casefold()))
        lines = [f"{name} · {money(totals[name])}" for name in ranked[:10]]
        if len(ranked) > 10:
            lines.append(f"Showing the 10 highest of {len(ranked)} visible customer totals.")
        return f"Confirmed orders by customer · {month:%B %Y}\n" + "\n".join(lines)
    return (
        f"{month:%B %Y} · {summary['confirmed_order_count']} confirmed orders\n"
        f"Order value {summary['confirmed_order_value']} · Target {summary['monthly_target']}\n"
        f"Achieved {summary['target_achieved']} · Remaining {summary['remaining_to_target']}"
    )


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


def logistics_answer(question, orders):
    invoice = INVOICE_PATTERN.search(question) or INVOICE_ARGUMENT.search(question)
    if not invoice:
        return "Enter an invoice number, for example: POD for INV-2611-001-01."
    reference = invoice[0] if invoice.re is INVOICE_PATTERN else invoice[1]
    match = next((order for order in orders if (order["invoice"] or "").casefold() == reference.casefold()), None)
    if match is None:
        return "That invoice is unavailable in your access scope for the selected month."
    return (
        f"Invoice {match['invoice']} · Order {match['order_id']}\n"
        f"POD status · {match['pod_status'] or 'Unavailable'}\n"
        f"POD reference · {match['pod_reference'] or 'Unavailable'}"
    )
