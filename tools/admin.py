"""Admin autopilot tools for Millennial Mum."""

import json
from pydantic import BaseModel, Field
from copilot import define_tool


class DraftEmailParams(BaseModel):
    recipient: str = Field(description="Who the email is to e.g. 'school office', 'GP surgery', 'nursery manager'")
    purpose: str = Field(description="Purpose of email e.g. 'absence notification', 'request meeting', 'complaint', 'thank you'")
    child_name: str = Field(description="Child's name")
    key_details: str = Field(description="Key details to include in the email")
    tone: str = Field(default="friendly-professional", description="Tone: friendly-professional, formal, casual, firm")


@define_tool(description="Draft a school/nursery/GP email or letter for the parent to review and send", skip_permission=True)
async def draft_email(params: DraftEmailParams) -> str:
    return json.dumps({
        "to": params.recipient,
        "purpose": params.purpose,
        "child_name": params.child_name,
        "details": params.key_details,
        "tone": params.tone,
        "instruction": (
            "Draft a short, clear email for this purpose. Use the specified tone. "
            "Include: appropriate greeting, the key information clearly stated, "
            "any action needed from the recipient, and a polite sign-off. "
            "Keep it under 150 words — schools/GPs are busy too. "
            "Add [PARENT NAME] as placeholder for signature. "
            "Format as ready-to-copy email with Subject line."
        )
    })
