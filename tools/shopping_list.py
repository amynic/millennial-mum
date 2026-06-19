"""Persistent shopping list for Millennial Mum.

Keeps a running list that accumulates items over time.
Persists to a local JSON file so it survives restarts.
"""

import json
import os
from datetime import datetime
from pydantic import BaseModel, Field
from copilot import define_tool

# Persist to file in the project directory
_LIST_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "shopping_list.json")


def _load_list() -> list[dict]:
    if os.path.exists(_LIST_FILE):
        with open(_LIST_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def _save_list(items: list[dict]):
    with open(_LIST_FILE, "w", encoding="utf-8") as f:
        json.dump(items, f, indent=2, ensure_ascii=False)


class AddToListParams(BaseModel):
    items: list[str] = Field(description="Items to add e.g. ['milk', 'nappies size 4', 'calpol']")
    category: str = Field(default="general", description="Category: fresh, dairy, meat, cupboard, frozen, household, baby, pharmacy, other")
    urgency: str = Field(default="normal", description="Urgency: urgent (need today), normal, nice-to-have")


@define_tool(description="Add items to the running shopping list. Use this whenever the parent mentions needing something - capture those fleeting thoughts!", skip_permission=True)
async def add_to_shopping_list(params: AddToListParams) -> str:
    current = _load_list()
    added = []
    for item in params.items:
        entry = {
            "item": item,
            "category": params.category,
            "urgency": params.urgency,
            "added": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "bought": False,
        }
        current.append(entry)
        added.append(item)

    _save_list(current)
    count = len([i for i in current if not i["bought"]])
    return f"Added to list: {', '.join(added)}\nYou now have {count} items on your shopping list."


class GetShoppingListParams(BaseModel):
    group_by: str = Field(default="category", description="How to organise: 'category' (aisle-friendly) or 'urgency' or 'all'")
    include_bought: bool = Field(default=False, description="Include already-bought items")


@define_tool(description="Get the full shopping list - perfect for when you arrive at the shop! Shows all those things you've been meaning to buy.", skip_permission=True)
async def get_shopping_list(params: GetShoppingListParams) -> str:
    current = _load_list()

    if not current:
        return "Your shopping list is empty! Tell me whenever you think of something you need and I'll add it."

    items = current if params.include_bought else [i for i in current if not i["bought"]]

    if not items:
        return "Everything on your list is bought! Fresh start. Tell me when you think of something."

    if params.group_by == "category":
        grouped: dict[str, list] = {}
        for i in items:
            grouped.setdefault(i["category"], []).append(i)

        lines = ["🛒 **YOUR SHOPPING LIST**", ""]
        category_emojis = {
            "fresh": "🥬", "dairy": "🥛", "meat": "🥩", "cupboard": "🥫",
            "frozen": "🧊", "household": "🧹", "baby": "👶", "pharmacy": "💊", "other": "📦"
        }
        for cat, cat_items in sorted(grouped.items()):
            emoji = category_emojis.get(cat, "📦")
            lines.append(f"{emoji} **{cat.title()}**")
            for i in cat_items:
                urgent = " ⚡" if i["urgency"] == "urgent" else ""
                lines.append(f"  [ ] {i['item']}{urgent}")
            lines.append("")

        lines.append(f"---\n📊 {len(items)} items total")
        urgent_count = len([i for i in items if i["urgency"] == "urgent"])
        if urgent_count:
            lines.append(f"⚡ {urgent_count} urgent")

    elif params.group_by == "urgency":
        lines = ["🛒 **YOUR SHOPPING LIST**", ""]
        for level in ["urgent", "normal", "nice-to-have"]:
            level_items = [i for i in items if i["urgency"] == level]
            if level_items:
                label = {"urgent": "⚡ NEED TODAY", "normal": "📋 Normal", "nice-to-have": "💭 Nice to have"}[level]
                lines.append(f"**{label}**")
                for i in level_items:
                    lines.append(f"  [ ] {i['item']} ({i['category']})")
                lines.append("")
    else:
        lines = ["🛒 **YOUR SHOPPING LIST**", ""]
        for i in items:
            urgent = " ⚡" if i["urgency"] == "urgent" else ""
            lines.append(f"  [ ] {i['item']} ({i['category']}){urgent}")

    return "\n".join(lines)


class MarkBoughtParams(BaseModel):
    items: list[str] = Field(description="Items to mark as bought (partial match works)")


@define_tool(description="Mark items as bought/done on the shopping list", skip_permission=True)
async def mark_bought(params: MarkBoughtParams) -> str:
    current = _load_list()
    marked = []

    for target in params.items:
        for entry in current:
            if not entry["bought"] and target.lower() in entry["item"].lower():
                entry["bought"] = True
                marked.append(entry["item"])
                break

    _save_list(current)
    remaining = len([i for i in current if not i["bought"]])

    if marked:
        return f"✅ Marked as bought: {', '.join(marked)}\n📋 {remaining} items remaining."
    else:
        return f"Couldn't find those items on your list. Try 'show my shopping list' to see what's there."


class ClearListParams(BaseModel):
    clear_bought_only: bool = Field(default=True, description="If true, only clears bought items. If false, clears everything.")


@define_tool(description="Clear the shopping list - either just bought items or everything for a fresh start", skip_permission=True)
async def clear_shopping_list(params: ClearListParams) -> str:
    current = _load_list()

    if params.clear_bought_only:
        remaining = [i for i in current if not i["bought"]]
        removed = len(current) - len(remaining)
        _save_list(remaining)
        return f"🧹 Cleared {removed} bought items. {len(remaining)} still on the list."
    else:
        _save_list([])
        return "🧹 Shopping list cleared completely. Fresh start!"
