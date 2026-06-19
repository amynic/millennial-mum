"""Budget tracking tools for Millennial Mum."""

import json
from datetime import datetime, timedelta
from pydantic import BaseModel, Field
from copilot import define_tool

# In-memory expense store
_expenses: list[dict] = []


class LogExpenseParams(BaseModel):
    amount: float = Field(description="Amount spent in GBP")
    category: str = Field(description="Category: groceries, childcare, clothing, activities, transport, health, household, treats")
    description: str = Field(description="Brief description e.g. 'Aldi weekly shop', 'Nursery fees March'")
    date: str = Field(default="today", description="Date of expense (YYYY-MM-DD or 'today')")


@define_tool(description="Log a family expense for budget tracking", skip_permission=True)
async def log_expense(params: LogExpenseParams) -> str:
    date = params.date if params.date != "today" else datetime.now().strftime("%Y-%m-%d")
    expense = {
        "amount": params.amount,
        "category": params.category,
        "description": params.description,
        "date": date,
    }
    _expenses.append(expense)
    total = sum(e["amount"] for e in _expenses)
    return f"💰 Logged: £{params.amount:.2f} for '{params.description}' ({params.category})\n📊 Running total this session: £{total:.2f}"


class WeeklySummaryParams(BaseModel):
    week_start: str = Field(default="auto", description="Start of week (YYYY-MM-DD) or 'auto' for current week")


@define_tool(description="Get a weekly spending summary broken down by category", skip_permission=True)
async def get_weekly_summary(params: WeeklySummaryParams) -> str:
    if not _expenses:
        return "No expenses logged yet. Use 'log expense' to start tracking! Even rough estimates help build a picture over time."

    # Group by category
    by_category: dict[str, float] = {}
    for e in _expenses:
        by_category[e["category"]] = by_category.get(e["category"], 0) + e["amount"]

    total = sum(by_category.values())
    lines = ["💰 **Spending Summary**", ""]
    for cat, amount in sorted(by_category.items(), key=lambda x: -x[1]):
        pct = (amount / total) * 100
        bar = "█" * int(pct / 5)
        lines.append(f"  {cat}: £{amount:.2f} ({pct:.0f}%) {bar}")

    lines.append(f"\n  **Total: £{total:.2f}**")
    lines.append(f"  ({len(_expenses)} transactions logged)")

    return "\n".join(lines)
