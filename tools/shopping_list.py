"""Persistent shopping list for Millennial Mum.

Keeps a running list that accumulates items over time. Items are stored via
``tools.storage``, which uses Azure Blob Storage when configured (durable and
shared across container replicas) and a local file otherwise.
"""

import asyncio
from datetime import datetime
from pydantic import BaseModel, Field
from tools._dual import define_tool
from tools import storage

_LIST_NAME = "shopping_list.json"


def _load_list() -> list[dict]:
    data = storage.read_json(_LIST_NAME, default=[]).data
    return data if isinstance(data, list) else []


def _is_bought(entry: dict) -> bool:
    return bool(entry.get("bought"))


class AddToListParams(BaseModel):
    items: list[str] = Field(description="Items to add e.g. ['milk', 'nappies size 4', 'calpol']")
    category: str = Field(default="general", description="Category: fresh, dairy, meat, cupboard, frozen, household, baby, pharmacy, other")
    urgency: str = Field(default="normal", description="Urgency: urgent (need today), normal, nice-to-have")


@define_tool(description="Add items to the running shopping list. Use this whenever the parent mentions needing something - capture those fleeting thoughts!", skip_permission=True)
async def add_to_shopping_list(params: AddToListParams) -> str:
    def mutate(current: list[dict]) -> list[str]:
        added = []
        for item in params.items:
            current.append({
                "item": item,
                "category": params.category,
                "urgency": params.urgency,
                "added": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "bought": False,
            })
            added.append(item)
        return added

    current, added = await asyncio.to_thread(
        storage.update_json, _LIST_NAME, mutate, default=[]
    )
    count = len([i for i in current if not _is_bought(i)])
    return f"Added to list: {', '.join(added)}\nYou now have {count} items on your shopping list."


class GetShoppingListParams(BaseModel):
    group_by: str = Field(default="category", description="How to organise: 'category' (aisle-friendly) or 'urgency' or 'all'")
    include_bought: bool = Field(default=False, description="Include already-bought items")


@define_tool(description="Get the full shopping list - perfect for when you arrive at the shop! Shows all those things you've been meaning to buy.", skip_permission=True)
async def get_shopping_list(params: GetShoppingListParams) -> str:
    current = await asyncio.to_thread(_load_list)

    if not current:
        return "Your shopping list is empty! Tell me whenever you think of something you need and I'll add it."

    items = current if params.include_bought else [i for i in current if not _is_bought(i)]

    if not items:
        return "Everything on your list is bought! Fresh start. Tell me when you think of something."

    if params.group_by == "category":
        grouped: dict[str, list] = {}
        for i in items:
            grouped.setdefault(i.get("category", "other"), []).append(i)

        lines = ["🛒 **YOUR SHOPPING LIST**", ""]
        category_emojis = {
            "fresh": "🥬", "dairy": "🥛", "meat": "🥩", "cupboard": "🥫",
            "frozen": "🧊", "household": "🧹", "baby": "👶", "pharmacy": "💊", "other": "📦"
        }
        for cat, cat_items in sorted(grouped.items()):
            emoji = category_emojis.get(cat, "📦")
            lines.append(f"{emoji} **{cat.title()}**")
            for i in cat_items:
                urgent = " ⚡" if i.get("urgency") == "urgent" else ""
                lines.append(f"  [ ] {i['item']}{urgent}")
            lines.append("")

        lines.append(f"---\n📊 {len(items)} items total")
        urgent_count = len([i for i in items if i.get("urgency") == "urgent"])
        if urgent_count:
            lines.append(f"⚡ {urgent_count} urgent")

    elif params.group_by == "urgency":
        lines = ["🛒 **YOUR SHOPPING LIST**", ""]
        for level in ["urgent", "normal", "nice-to-have"]:
            level_items = [i for i in items if i.get("urgency") == level]
            if level_items:
                label = {"urgent": "⚡ NEED TODAY", "normal": "📋 Normal", "nice-to-have": "💭 Nice to have"}[level]
                lines.append(f"**{label}**")
                for i in level_items:
                    lines.append(f"  [ ] {i['item']} ({i.get('category', 'other')})")
                lines.append("")
    else:
        lines = ["🛒 **YOUR SHOPPING LIST**", ""]
        for i in items:
            urgent = " ⚡" if i.get("urgency") == "urgent" else ""
            lines.append(f"  [ ] {i['item']} ({i.get('category', 'other')}){urgent}")

    return "\n".join(lines)


class MarkBoughtParams(BaseModel):
    items: list[str] = Field(description="Items to mark as bought (partial match works)")


@define_tool(description="Mark items as bought/done on the shopping list", skip_permission=True)
async def mark_bought(params: MarkBoughtParams) -> str:
    def mutate(current: list[dict]) -> list[str]:
        marked = []
        for target in params.items:
            for entry in current:
                if not _is_bought(entry) and target.lower() in entry.get("item", "").lower():
                    entry["bought"] = True
                    marked.append(entry["item"])
                    break
        return marked

    current, marked = await asyncio.to_thread(
        storage.update_json, _LIST_NAME, mutate, default=[]
    )
    remaining = len([i for i in current if not _is_bought(i)])

    if marked:
        return f"✅ Marked as bought: {', '.join(marked)}\n📋 {remaining} items remaining."
    else:
        return f"Couldn't find those items on your list. Try 'show my shopping list' to see what's there."


class ClearListParams(BaseModel):
    clear_bought_only: bool = Field(default=True, description="If true, only clears bought items. If false, clears everything.")


@define_tool(description="Clear the shopping list - either just bought items or everything for a fresh start", skip_permission=True)
async def clear_shopping_list(params: ClearListParams) -> str:
    def mutate(current: list[dict]) -> int:
        before = len(current)
        keep = [i for i in current if not _is_bought(i)] if params.clear_bought_only else []
        current[:] = keep
        return before - len(keep)

    current, removed = await asyncio.to_thread(
        storage.update_json, _LIST_NAME, mutate, default=[]
    )

    if params.clear_bought_only:
        return f"🧹 Cleared {removed} bought items. {len(current)} still on the list."
    return "🧹 Shopping list cleared completely. Fresh start!"
