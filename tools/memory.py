"""Family profile memory for Millennial Mum.

Persists family details (children, work schedule, routines, preferences)
to a local JSON file so the agent remembers across sessions.
"""

import json
import os
from datetime import datetime
from pydantic import BaseModel, Field
from copilot import define_tool
from typing import Optional

_PROFILE_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "family_profile.json")

DEFAULT_PROFILE = {
    "children": [],
    "work_schedule": {},
    "partner": {},
    "childcare": {},
    "household": {},
    "medical": {},
    "preferences": {},
    "updated_at": None,
}


def _load_profile() -> dict:
    if os.path.exists(_PROFILE_FILE):
        with open(_PROFILE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return DEFAULT_PROFILE.copy()


def _save_profile(profile: dict):
    profile["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    with open(_PROFILE_FILE, "w", encoding="utf-8") as f:
        json.dump(profile, f, indent=2, ensure_ascii=False)


def _format_profile(profile: dict) -> str:
    """Format the profile as a readable summary for the agent."""
    sections = []

    if profile.get("children"):
        sections.append("👶 **Children:**")
        for child in profile["children"]:
            parts = [f"  - **{child.get('name', 'Unknown')}**"]
            if child.get("age"):
                parts.append(f"age {child['age']}")
            if child.get("dob"):
                parts.append(f"(DOB: {child['dob']})")
            if child.get("allergies"):
                parts.append(f"⚠️ Allergies: {', '.join(child['allergies'])}")
            if child.get("fussy_eating"):
                parts.append(f"🍽️ Won't eat: {', '.join(child['fussy_eating'])}")
            if child.get("loves"):
                parts.append(f"❤️ Loves: {', '.join(child['loves'])}")
            if child.get("nappy_size"):
                parts.append(f"Nappy size: {child['nappy_size']}")
            if child.get("notes"):
                parts.append(f"Notes: {child['notes']}")
            sections.append(", ".join(parts))

    if profile.get("work_schedule") and any(profile["work_schedule"].values()):
        ws = profile["work_schedule"]
        sections.append("💼 **Work Schedule:**")
        if ws.get("work_days"):
            sections.append(f"  - Work days: {', '.join(ws['work_days'])}")
        if ws.get("work_hours"):
            sections.append(f"  - Hours: {ws['work_hours']}")
        if ws.get("wfh_days"):
            sections.append(f"  - WFH days: {', '.join(ws['wfh_days'])}")
        if ws.get("commute_time"):
            sections.append(f"  - Commute: {ws['commute_time']}")
        if ws.get("notes"):
            sections.append(f"  - Notes: {ws['notes']}")

    if profile.get("partner") and any(profile["partner"].values()):
        p = profile["partner"]
        sections.append("👫 **Partner:**")
        if p.get("name"):
            sections.append(f"  - Name: {p['name']}")
        if p.get("work_days"):
            sections.append(f"  - Work days: {', '.join(p['work_days'])}")
        if p.get("work_hours"):
            sections.append(f"  - Hours: {p['work_hours']}")
        if p.get("notes"):
            sections.append(f"  - Notes: {p['notes']}")

    if profile.get("childcare") and any(profile["childcare"].values()):
        cc = profile["childcare"]
        sections.append("🏫 **Childcare:**")
        if cc.get("type"):
            sections.append(f"  - Type: {cc['type']}")
        if cc.get("name"):
            sections.append(f"  - Name: {cc['name']}")
        if cc.get("days"):
            sections.append(f"  - Days: {', '.join(cc['days'])}")
        if cc.get("hours"):
            sections.append(f"  - Hours: {cc['hours']}")
        if cc.get("drop_off"):
            sections.append(f"  - Drop-off: {cc['drop_off']}")
        if cc.get("pick_up"):
            sections.append(f"  - Pick-up: {cc['pick_up']}")
        if cc.get("notes"):
            sections.append(f"  - Notes: {cc['notes']}")

    if profile.get("household") and any(profile["household"].values()):
        h = profile["household"]
        sections.append("🏠 **Household:**")
        if h.get("location"):
            sections.append(f"  - Location: {h['location']}")
        if h.get("pets"):
            sections.append(f"  - Pets: {', '.join(h['pets'])}")
        if h.get("car"):
            sections.append(f"  - Car: {h['car']}")
        if h.get("notes"):
            sections.append(f"  - Notes: {h['notes']}")

    if profile.get("medical") and any(profile["medical"].values()):
        m = profile["medical"]
        sections.append("🏥 **Medical:**")
        if m.get("gp_surgery"):
            sections.append(f"  - GP: {m['gp_surgery']}")
        if m.get("health_visitor"):
            sections.append(f"  - Health visitor: {m['health_visitor']}")
        if m.get("notes"):
            sections.append(f"  - Notes: {m['notes']}")

    if profile.get("preferences") and any(profile["preferences"].values()):
        pr = profile["preferences"]
        sections.append("⚙️ **Preferences:**")
        if pr.get("dietary"):
            sections.append(f"  - Dietary: {pr['dietary']}")
        if pr.get("budget"):
            sections.append(f"  - Budget: {pr['budget']}")
        if pr.get("supermarket"):
            sections.append(f"  - Supermarket: {pr['supermarket']}")
        if pr.get("notes"):
            sections.append(f"  - Notes: {pr['notes']}")

    if not sections:
        return "No family profile set up yet."

    return "\n".join(sections)


# --- Tools ---

class UpdateChildParams(BaseModel):
    name: str = Field(description="Child's name")
    age: Optional[str] = Field(default=None, description="Child's age e.g. '2', '18 months', '4'")
    dob: Optional[str] = Field(default=None, description="Date of birth e.g. '2023-06-15'")
    allergies: Optional[list[str]] = Field(default=None, description="Known allergies e.g. ['dairy', 'eggs']")
    fussy_eating: Optional[list[str]] = Field(default=None, description="Foods they refuse e.g. ['peas', 'mushrooms']")
    loves: Optional[list[str]] = Field(default=None, description="Things they love e.g. ['dinosaurs', 'Bluey', 'pasta']")
    nappy_size: Optional[str] = Field(default=None, description="Current nappy size")
    notes: Optional[str] = Field(default=None, description="Any other notes about this child")


@define_tool(description="Save or update a child's details in the family profile. Use when a parent mentions their child's name, age, allergies, preferences, or any personal details worth remembering.", skip_permission=True)
async def save_child(params: UpdateChildParams) -> str:
    profile = _load_profile()
    children = profile.get("children", [])

    # Find existing child by name or add new
    existing = None
    for child in children:
        if child.get("name", "").lower() == params.name.lower():
            existing = child
            break

    if existing is None:
        existing = {"name": params.name}
        children.append(existing)

    # Update only provided fields
    if params.age is not None:
        existing["age"] = params.age
    if params.dob is not None:
        existing["dob"] = params.dob
    if params.allergies is not None:
        existing["allergies"] = params.allergies
    if params.fussy_eating is not None:
        existing["fussy_eating"] = params.fussy_eating
    if params.loves is not None:
        existing["loves"] = params.loves
    if params.nappy_size is not None:
        existing["nappy_size"] = params.nappy_size
    if params.notes is not None:
        existing["notes"] = params.notes

    profile["children"] = children
    _save_profile(profile)
    return f"✅ Saved details for {params.name}. I'll remember this for next time!"


class UpdateWorkScheduleParams(BaseModel):
    work_days: Optional[list[str]] = Field(default=None, description="Days worked e.g. ['Monday', 'Tuesday', 'Wednesday']")
    work_hours: Optional[str] = Field(default=None, description="Working hours e.g. '9am-5pm', '8:30-3:30'")
    wfh_days: Optional[list[str]] = Field(default=None, description="Work from home days e.g. ['Monday', 'Friday']")
    commute_time: Optional[str] = Field(default=None, description="Commute duration e.g. '45 mins', '1 hour'")
    notes: Optional[str] = Field(default=None, description="Other work notes e.g. 'flexible Fridays', 'compressed hours'")


@define_tool(description="Save the parent's work schedule. Use when they mention work days, hours, WFH days, or commute.", skip_permission=True)
async def save_work_schedule(params: UpdateWorkScheduleParams) -> str:
    profile = _load_profile()
    ws = profile.get("work_schedule", {})

    if params.work_days is not None:
        ws["work_days"] = params.work_days
    if params.work_hours is not None:
        ws["work_hours"] = params.work_hours
    if params.wfh_days is not None:
        ws["wfh_days"] = params.wfh_days
    if params.commute_time is not None:
        ws["commute_time"] = params.commute_time
    if params.notes is not None:
        ws["notes"] = params.notes

    profile["work_schedule"] = ws
    _save_profile(profile)
    return "✅ Work schedule saved. I'll factor this into scheduling from now on."


class UpdateChildcareParams(BaseModel):
    type: Optional[str] = Field(default=None, description="Type: nursery, childminder, preschool, school, family")
    name: Optional[str] = Field(default=None, description="Name of nursery/school/childminder")
    days: Optional[list[str]] = Field(default=None, description="Days attended e.g. ['Monday', 'Wednesday', 'Friday']")
    hours: Optional[str] = Field(default=None, description="Hours e.g. '8am-6pm', '9-3'")
    drop_off: Optional[str] = Field(default=None, description="Drop-off time e.g. '8:15am'")
    pick_up: Optional[str] = Field(default=None, description="Pick-up time e.g. '5:30pm'")
    notes: Optional[str] = Field(default=None, description="Other notes e.g. 'early finish Fridays', 'closed bank holidays'")


@define_tool(description="Save childcare details (nursery, school, childminder). Use when a parent mentions childcare arrangements.", skip_permission=True)
async def save_childcare(params: UpdateChildcareParams) -> str:
    profile = _load_profile()
    cc = profile.get("childcare", {})

    if params.type is not None:
        cc["type"] = params.type
    if params.name is not None:
        cc["name"] = params.name
    if params.days is not None:
        cc["days"] = params.days
    if params.hours is not None:
        cc["hours"] = params.hours
    if params.drop_off is not None:
        cc["drop_off"] = params.drop_off
    if params.pick_up is not None:
        cc["pick_up"] = params.pick_up
    if params.notes is not None:
        cc["notes"] = params.notes

    profile["childcare"] = cc
    _save_profile(profile)
    return "✅ Childcare details saved. I'll remember this for scheduling."


class UpdateFamilyInfoParams(BaseModel):
    section: str = Field(description="Which section to update: 'partner', 'household', 'medical', or 'preferences'")
    data: dict = Field(description="Key-value pairs to save. For partner: name, work_days, work_hours, notes. For household: location, pets, car, notes. For medical: gp_surgery, health_visitor, notes. For preferences: dietary, budget, supermarket, notes.")


@define_tool(description="Save family info (partner details, household, medical contacts, preferences). Use when a parent mentions their partner, home, GP, dietary needs, or budget.", skip_permission=True)
async def save_family_info(params: UpdateFamilyInfoParams) -> str:
    profile = _load_profile()
    valid_sections = ["partner", "household", "medical", "preferences"]

    if params.section not in valid_sections:
        return f"Unknown section '{params.section}'. Use one of: {', '.join(valid_sections)}"

    current = profile.get(params.section, {})
    current.update(params.data)
    profile[params.section] = current
    _save_profile(profile)

    labels = {
        "partner": "Partner details",
        "household": "Household info",
        "medical": "Medical contacts",
        "preferences": "Preferences",
    }
    return f"✅ {labels[params.section]} saved. I'll keep this in mind!"


class GetProfileParams(BaseModel):
    section: Optional[str] = Field(default=None, description="Specific section to show: 'children', 'work_schedule', 'childcare', 'partner', 'household', 'medical', 'preferences'. Leave empty for full profile.")


@define_tool(description="Get the saved family profile. Use at the start of conversations to load context, or when the parent asks what you know about them.", skip_permission=True)
async def get_family_profile(params: GetProfileParams) -> str:
    profile = _load_profile()

    if not os.path.exists(_PROFILE_FILE):
        return (
            "No family profile set up yet! Tell me about your family and I'll remember:\n"
            "- Your children (names, ages, allergies, likes)\n"
            "- Your work schedule (days, hours, WFH)\n"
            "- Childcare setup (nursery days, drop-off/pick-up)\n"
            "- Partner details\n"
            "- Preferences (dietary, budget, supermarket)"
        )

    return _format_profile(profile)


def get_profile_context() -> str:
    """Load profile as context string for the system prompt.

    Called at session start to inject family context.
    """
    if not os.path.exists(_PROFILE_FILE):
        return ""

    profile = _load_profile()
    formatted = _format_profile(profile)

    if formatted == "No family profile set up yet.":
        return ""

    return (
        "\n\n## 📋 Family Profile (remembered from previous sessions)\n"
        f"{formatted}\n"
        "Use this context automatically — don't ask for details you already know.\n"
    )
