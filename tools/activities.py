"""Activity finder tools for Millennial Mum."""

import json
from pydantic import BaseModel, Field
from copilot import define_tool


class SuggestActivityParams(BaseModel):
    child_age: int = Field(description="Child's age in years (0-7)")
    time_available_minutes: int = Field(default=60, description="How much time you have")
    weather: str = Field(default="unknown", description="Weather: sunny, rainy, cold, hot, unknown")
    energy_level: str = Field(default="medium", description="Parent's energy level: low, medium, high")
    indoor_only: bool = Field(default=False, description="Must be indoor activity")
    num_children: int = Field(default=1, description="Number of children to entertain")


@define_tool(description="Suggest age-appropriate activities based on time, weather, and energy levels", skip_permission=True)
async def suggest_activity(params: SuggestActivityParams) -> str:
    constraints = []
    if params.indoor_only or params.weather == "rainy":
        constraints.append("indoor only")
    if params.energy_level == "low":
        constraints.append("low-effort for parent (screen-free but minimal setup)")
    if params.num_children > 1:
        constraints.append(f"must work for {params.num_children} kids together")

    age_stage = ""
    if params.child_age < 1:
        age_stage = "baby (sensory play, tummy time, music)"
    elif params.child_age < 3:
        age_stage = "toddler (messy play, simple crafts, movement, water play)"
    elif params.child_age < 5:
        age_stage = "preschooler (imaginative play, early learning games, baking, nature)"
    else:
        age_stage = "school-age (projects, sports, board games, science experiments)"

    return json.dumps({
        "age_years": params.child_age,
        "age_stage": age_stage,
        "time_minutes": params.time_available_minutes,
        "weather": params.weather,
        "constraints": constraints,
        "instruction": (
            "Suggest 3 activities that fit these constraints. For each include: "
            "name, time needed, materials (use common household items), "
            "mess level (1-5), and learning benefit. "
            "Prioritise activities that don't require a trip to the shops."
        )
    })
