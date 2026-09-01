from .meal_planner import suggest_meal, generate_shopping_list
from .schedule import add_event, get_today_schedule, find_free_slots
from .activities import suggest_activity
from .budget import log_expense, get_weekly_summary
from .admin import draft_email
from .shopping_list import add_to_shopping_list, get_shopping_list, mark_bought, clear_shopping_list
from .emergency import emergency_quick_ref
from .memory import (
    save_child, save_work_schedule, save_childcare,
    save_family_info, get_family_profile, get_profile_context,
)

# Specialist domain -> its tools. Source of truth for wiring each decomposed
# agent; the orchestrator itself holds no domain tools.
DOMAIN_TOOLS = {
    # Kitchen specialist: meals + the shopping list that flows from them.
    "kitchen": [
        suggest_meal, generate_shopping_list,
        add_to_shopping_list, get_shopping_list, mark_bought, clear_shopping_list,
    ],
    # Planner specialist: calendar + age-appropriate activities.
    "planner": [add_event, get_today_schedule, find_free_slots, suggest_activity],
    # Admin & Budget specialist: expense tracking + drafting.
    "admin_budget": [log_expense, get_weekly_summary, draft_email],
    # Health specialist ("Toddler Down"): NHS-sourced emergency reference only.
    "health": [emergency_quick_ref],
    # Shared family memory: a service every agent reads/writes (not routed to).
    "memory": [
        save_child, save_work_schedule, save_childcare,
        save_family_info, get_family_profile,
    ],
}

# Flat list preserved for the existing monolith and any callers that import it.
ALL_TOOLS = [tool for tools in DOMAIN_TOOLS.values() for tool in tools]

__all__ = ["DOMAIN_TOOLS", "ALL_TOOLS", "get_profile_context"]
