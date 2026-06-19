"""Meal planning tools for Millennial Mum."""

import json
from datetime import datetime
from pydantic import BaseModel, Field
from copilot import define_tool


class SuggestMealParams(BaseModel):
    ingredients: list[str] = Field(description="Ingredients available at home")
    child_age: int = Field(description="Child's age in years (0-7)")
    max_prep_minutes: int = Field(default=30, description="Maximum prep time in minutes")
    dietary_requirements: str = Field(default="none", description="Any allergies or dietary needs e.g. dairy-free, vegetarian")


@define_tool(description="Suggest a quick healthy meal based on available ingredients, child's age, and time available", skip_permission=True)
async def suggest_meal(params: SuggestMealParams) -> str:
    """Returns meal suggestion context for the LLM to elaborate on."""
    age_notes = ""
    if params.child_age < 1:
        age_notes = "Baby under 1: no honey, no whole nuts, no salt, soft textures only."
    elif params.child_age < 3:
        age_notes = "Toddler: cut grapes/cherry tomatoes lengthways, avoid whole nuts, low salt."
    elif params.child_age < 5:
        age_notes = "Preschooler: can handle most textures, still supervise with small round foods."
    else:
        age_notes = "School age: most foods fine, focus on balanced plate."

    return json.dumps({
        "ingredients_available": params.ingredients,
        "time_budget_minutes": params.max_prep_minutes,
        "age_safety_notes": age_notes,
        "dietary": params.dietary_requirements,
        "instruction": "Suggest 2-3 meal ideas using these ingredients. Include prep time, kid-friendliness rating (1-5 stars), and any safety notes for this age group."
    })


class ShoppingListParams(BaseModel):
    meals_planned: list[str] = Field(description="List of meals planned for the week")
    household_size: int = Field(default=4, description="Number of people in household")
    budget_gbp: float = Field(default=80.0, description="Weekly food budget in GBP")


@define_tool(description="Generate a weekly shopping list from planned meals, organised by supermarket aisle", skip_permission=True)
async def generate_shopping_list(params: ShoppingListParams) -> str:
    return json.dumps({
        "meals": params.meals_planned,
        "serves": params.household_size,
        "budget": f"£{params.budget_gbp:.2f}",
        "instruction": "Generate a shopping list grouped by category (fruit & veg, dairy, meat/protein, cupboard staples, frozen). Include estimated cost per category. Flag any items that could be swapped for budget alternatives."
    })
