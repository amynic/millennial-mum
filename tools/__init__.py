from .meal_planner import suggest_meal, generate_shopping_list
from .schedule import add_event, get_today_schedule, find_free_slots
from .activities import suggest_activity
from .budget import log_expense, get_weekly_summary
from .admin import draft_email
from .shopping_list import add_to_shopping_list, get_shopping_list, mark_bought, clear_shopping_list

ALL_TOOLS = [
    suggest_meal,
    generate_shopping_list,
    add_event,
    get_today_schedule,
    find_free_slots,
    suggest_activity,
    log_expense,
    get_weekly_summary,
    draft_email,
    add_to_shopping_list,
    get_shopping_list,
    mark_bought,
    clear_shopping_list,
]
