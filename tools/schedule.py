"""Schedule management tools for Millennial Mum."""

import json
from datetime import datetime, timedelta
from pydantic import BaseModel, Field
from copilot import define_tool

# In-memory schedule store (would be a database in production)
_schedule: list[dict] = []


class AddEventParams(BaseModel):
    title: str = Field(description="Event title e.g. 'Nursery pickup', 'Team standup'")
    date: str = Field(description="Date in YYYY-MM-DD format")
    time: str = Field(description="Time in HH:MM format (24h)")
    duration_minutes: int = Field(default=60, description="Duration in minutes")
    category: str = Field(default="general", description="Category: childcare, work, school, health, social, general")
    recurring: str = Field(default="none", description="Recurrence: none, daily, weekdays, weekly")


@define_tool(description="Add an event to the family schedule", skip_permission=True)
async def add_event(params: AddEventParams) -> str:
    event = {
        "title": params.title,
        "date": params.date,
        "time": params.time,
        "duration_minutes": params.duration_minutes,
        "category": params.category,
        "recurring": params.recurring,
    }
    _schedule.append(event)
    return f"✅ Added '{params.title}' on {params.date} at {params.time} ({params.duration_minutes} min, {params.category})"


class GetScheduleParams(BaseModel):
    date: str = Field(default="today", description="Date to check (YYYY-MM-DD or 'today')")


@define_tool(description="Get all events for a specific day, showing the family's schedule", skip_permission=True)
async def get_today_schedule(params: GetScheduleParams) -> str:
    target = params.date
    if target == "today":
        target = datetime.now().strftime("%Y-%m-%d")

    day_events = [e for e in _schedule if e["date"] == target]

    if not day_events:
        return f"📅 No events scheduled for {target}. Looks like a free day! (Or events haven't been added yet)"

    day_events.sort(key=lambda x: x["time"])
    lines = [f"📅 Schedule for {target}:"]
    for e in day_events:
        emoji = {"childcare": "👶", "work": "💼", "school": "🏫", "health": "🏥", "social": "🎉"}.get(e["category"], "📌")
        lines.append(f"  {emoji} {e['time']} - {e['title']} ({e['duration_minutes']} min)")

    return "\n".join(lines)


class FindFreeSlotsParams(BaseModel):
    date: str = Field(description="Date to check (YYYY-MM-DD)")
    min_duration_minutes: int = Field(default=30, description="Minimum free slot duration needed")


@define_tool(description="Find free time slots in the day for fitting in tasks or self-care", skip_permission=True)
async def find_free_slots(params: FindFreeSlotsParams) -> str:
    day_events = sorted(
        [e for e in _schedule if e["date"] == params.date],
        key=lambda x: x["time"]
    )

    if not day_events:
        return f"The whole day ({params.date}) appears free! No events have been scheduled yet."

    # Simple gap detection between 7am and 9pm
    slots = []
    day_start = 7 * 60  # 7:00 in minutes
    day_end = 21 * 60   # 21:00 in minutes

    current = day_start
    for e in day_events:
        h, m = map(int, e["time"].split(":"))
        event_start = h * 60 + m
        event_end = event_start + e["duration_minutes"]

        if event_start - current >= params.min_duration_minutes:
            slots.append(f"  🟢 {current//60:02d}:{current%60:02d} - {event_start//60:02d}:{event_start%60:02d} ({event_start - current} min free)")

        current = max(current, event_end)

    if day_end - current >= params.min_duration_minutes:
        slots.append(f"  🟢 {current//60:02d}:{current%60:02d} - 21:00 ({day_end - current} min free)")

    if not slots:
        return f"😅 No free slots of {params.min_duration_minutes}+ minutes on {params.date}. Tight day!"

    return f"Free slots on {params.date} (min {params.min_duration_minutes} min):\n" + "\n".join(slots)
