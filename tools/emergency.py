"""Emergency Quick-Ref tool — Toddler Down 🚨

Provides quick-reference emergency guidance for toddler health situations.
All information sourced ONLY from official NHS resources (nhs.uk, NHS 111).
"""

from pydantic import BaseModel, Field
from copilot import define_tool


# NHS-sourced guidance for common toddler emergencies
NHS_GUIDANCE = {
    "fever": {
        "title": "High Temperature (Fever)",
        "source": "https://www.nhs.uk/conditions/baby/health/treating-a-high-temperature-in-children/",
        "immediate_actions": [
            "Keep them cool — remove extra layers, don't wrap them up",
            "Offer plenty of fluids — breast milk, formula, or water",
            "Give children's paracetamol or ibuprofen if they're distressed (check age/dose on packet)",
            "Check on them regularly through the night if it's bedtime",
        ],
        "do_not": [
            "Do NOT underdress them or sponge with cold water",
            "Do NOT give aspirin to under-16s",
            "Do NOT give ibuprofen to babies under 3 months or under 5kg",
        ],
        "when_to_go_to_hospital": [
            "Under 3 months old with a temperature of 38°C or higher",
            "3-6 months old with a temperature of 39°C or higher",
            "Fever with a stiff neck or sensitivity to light",
            "Fever with a rash that doesn't fade when you press a glass against it",
            "Having a fit or seizure for the first time",
            "Unusually cold or floppy",
            "Not responding normally or difficult to wake",
        ],
        "call_111_if": [
            "Under 2 and temperature lasts more than 24 hours",
            "Over 2 and temperature lasts more than 3 days",
            "You're worried but they don't seem seriously unwell",
        ],
    },
    "choking": {
        "title": "Choking",
        "source": "https://www.nhs.uk/conditions/baby/first-aid-and-safety/first-aid/what-to-do-if-your-child-is-choking/",
        "immediate_actions": [
            "If they're coughing — encourage them to keep coughing, don't interfere",
            "If they CAN'T cough, cry or breathe — call 999 immediately",
            "Child over 1: Give up to 5 back blows between shoulder blades with heel of hand",
            "Still choking? Give up to 5 abdominal thrusts (stand behind, pull inwards and upwards above belly button)",
            "Repeat back blows and abdominal thrusts until object clears or help arrives",
        ],
        "do_not": [
            "Do NOT put your fingers in their mouth to sweep (can push object further)",
            "Do NOT hold them upside down",
        ],
        "when_to_go_to_hospital": [
            "Call 999 if they can't breathe, cough, or cry",
            "After choking episode even if object cleared — to check for damage",
            "If abdominal thrusts were used (internal injury risk)",
            "If you suspect something is still stuck",
        ],
        "call_111_if": [
            "They had a choking episode but seem fine now and you want reassurance",
            "Persistent cough after episode",
        ],
    },
    "rash": {
        "title": "Rashes in Babies and Children",
        "source": "https://www.nhs.uk/conditions/baby/caring-for-a-newborn/rash/",
        "immediate_actions": [
            "Do the glass test — press a clear glass firmly against the rash",
            "If the rash does NOT fade under pressure — call 999 immediately (possible meningitis)",
            "If it fades — likely less serious, but observe for other symptoms",
            "Note when it started, if it's spreading, and any other symptoms",
            "Take a photo to show a healthcare professional",
        ],
        "do_not": [
            "Do NOT assume a rash is 'just viral' without doing the glass test",
            "Do NOT wait to see if a non-blanching rash goes away",
        ],
        "when_to_go_to_hospital": [
            "Rash does not fade when pressed with a glass (call 999)",
            "Rash with high fever and child seems very unwell",
            "Rash with stiff neck, sensitivity to light, or confusion",
            "Purple or blood-coloured spots appearing quickly",
            "Child is unusually drowsy or difficult to wake",
        ],
        "call_111_if": [
            "Rash with mild fever but child otherwise well",
            "Rash lasting more than a few days",
            "You're unsure what type of rash it is",
        ],
    },
    "burns": {
        "title": "Burns and Scalds",
        "source": "https://www.nhs.uk/conditions/burns-and-scalds/",
        "immediate_actions": [
            "Cool the burn under cool running water for at least 20 minutes",
            "Remove clothing/jewellery near the burn (unless stuck to skin)",
            "After cooling, cover loosely with cling film or a clean non-fluffy dressing",
            "Give children's paracetamol or ibuprofen for pain",
        ],
        "do_not": [
            "Do NOT use ice, iced water, or any creams/butter on the burn",
            "Do NOT burst any blisters",
            "Do NOT remove clothing stuck to the burn",
            "Do NOT use fluffy materials like cotton wool on the burn",
        ],
        "when_to_go_to_hospital": [
            "All burns on babies and toddlers (even if small)",
            "Burns to face, hands, feet, joints, or genitals",
            "Burns bigger than child's hand",
            "Chemical or electrical burns",
            "Burns that look white or charred (deep burns)",
            "Burns that go all the way around a limb",
            "Child is in a lot of pain after 20 mins cooling",
        ],
        "call_111_if": [
            "Small, superficial burn on non-sensitive area and you're unsure about care",
        ],
    },
    "head_injury": {
        "title": "Head Injury",
        "source": "https://www.nhs.uk/conditions/head-injury-and-concussion/",
        "immediate_actions": [
            "Hold something cold against the bump (frozen peas in a tea towel)",
            "Keep them awake and watch closely for first few hours",
            "Rest — no rough play for the rest of the day",
            "Give children's paracetamol for pain (NOT ibuprofen — can increase bleeding risk)",
            "Watch for any warning signs over next 48 hours",
        ],
        "do_not": [
            "Do NOT let them fall asleep immediately without monitoring",
            "Do NOT give ibuprofen (increases bleeding risk)",
            "Do NOT shake them to keep them awake",
        ],
        "when_to_go_to_hospital": [
            "Knocked out or unconscious (even briefly) — call 999",
            "Vomiting more than once after the injury",
            "Seizure or fit",
            "Clear fluid or blood from ears or nose",
            "Unusual drowsiness — hard to wake or not responding normally",
            "Problems with vision, walking, or balance",
            "Weakness in arms or legs",
            "Bruising behind ears or black eyes (not from direct hit)",
            "Fall from significant height (more than their own height)",
            "Baby under 1 year with any head bump",
        ],
        "call_111_if": [
            "You're worried but child seems normal",
            "Child has a headache that won't go away",
            "Bump is very large or still swollen after a few days",
        ],
    },
    "breathing_difficulty": {
        "title": "Breathing Difficulties (Croup, Wheeze, Breathlessness)",
        "source": "https://www.nhs.uk/conditions/croup/",
        "immediate_actions": [
            "Keep them calm — crying makes breathing harder",
            "Sit them upright (or hold them upright on your lap)",
            "Don't put anything in their mouth",
            "If croup: try taking them into steamy bathroom OR cool night air for 10 mins",
            "Monitor their breathing — count breaths per minute",
        ],
        "do_not": [
            "Do NOT lay them flat if struggling to breathe",
            "Do NOT try to look in their throat",
            "Do NOT give cough medicine to under-6s",
        ],
        "when_to_go_to_hospital": [
            "Call 999 if they're struggling to breathe (ribs pulling in, flaring nostrils)",
            "Skin, lips, or tongue turning blue or grey",
            "Unusually quiet or floppy",
            "Cannot swallow or drooling excessively",
            "Getting worse quickly",
            "Making a high-pitched sound (stridor) when breathing in at rest",
        ],
        "call_111_if": [
            "Barking cough but breathing normally between coughing fits",
            "Mild wheeze but eating and drinking normally",
            "Breathing seems a bit faster than normal but no other symptoms",
        ],
    },
    "vomiting_diarrhoea": {
        "title": "Vomiting and Diarrhoea",
        "source": "https://www.nhs.uk/conditions/diarrhoea-and-vomiting/",
        "immediate_actions": [
            "Keep offering small sips of fluid frequently (water, diluted juice, or oral rehydration sachets)",
            "Breastfeed as normal if breastfeeding",
            "Once vomiting settles, offer bland food if hungry — don't force it",
            "Watch for signs of dehydration (fewer wet nappies, dry mouth, no tears, sunken eyes)",
            "Keep them off nursery for 48 hours after last episode",
        ],
        "do_not": [
            "Do NOT give anti-diarrhoea or anti-sickness medicine to under-12s",
            "Do NOT give fruit juice or fizzy drinks (can make diarrhoea worse)",
            "Do NOT force food",
        ],
        "when_to_go_to_hospital": [
            "Signs of severe dehydration — drowsy, cold hands/feet, very few wet nappies",
            "Blood in sick or poo",
            "Green or yellow-green vomit (bile)",
            "Severe tummy pain",
            "Under 1 year and not keeping fluids down",
            "Floppy, irritable, or not responding normally",
            "Still vomiting after 24 hours with no fluids kept down",
        ],
        "call_111_if": [
            "Mild dehydration signs but still taking some fluids",
            "Vomiting and diarrhoea lasting more than a few days",
            "You're worried but child is still alert and having wet nappies",
        ],
    },
    "seizure": {
        "title": "Febrile Seizures (Fits/Convulsions)",
        "source": "https://www.nhs.uk/conditions/febrile-seizures/",
        "immediate_actions": [
            "Note the time it started",
            "Clear the area around them — move hard/sharp objects away",
            "Put them on their side (recovery position) if possible",
            "Stay with them and time the seizure",
            "Once it stops: put in recovery position, check breathing",
        ],
        "do_not": [
            "Do NOT put anything in their mouth",
            "Do NOT restrain them or try to stop the movements",
            "Do NOT move them unless they're in danger",
            "Do NOT try to cool them with cold water during the seizure",
        ],
        "when_to_go_to_hospital": [
            "Call 999 if seizure lasts more than 5 minutes",
            "First ever seizure — always get checked (A&E or urgent GP)",
            "Child doesn't recover fully within an hour",
            "Another seizure happens shortly after",
            "You think the seizure isn't caused by fever",
            "Child has breathing difficulties after the seizure",
        ],
        "call_111_if": [
            "Seizure lasted under 5 minutes and child recovered fully",
            "Child has had febrile seizures before but you want advice",
        ],
    },
    "allergic_reaction": {
        "title": "Allergic Reaction / Anaphylaxis",
        "source": "https://www.nhs.uk/conditions/anaphylaxis/",
        "immediate_actions": [
            "If they have an adrenaline pen (EpiPen) — use it immediately",
            "Call 999 — say 'anaphylaxis'",
            "Remove trigger if possible (e.g., stop eating the food)",
            "Lie them flat (or sit up if breathing is difficult)",
            "Give a second adrenaline pen after 5 mins if no better",
        ],
        "do_not": [
            "Do NOT stand them up if feeling faint",
            "Do NOT delay using the adrenaline pen 'to see if it gets better'",
        ],
        "when_to_go_to_hospital": [
            "Call 999 for: swelling of throat/tongue, difficulty breathing, feeling faint/dizzy",
            "Any suspected anaphylaxis — even if adrenaline pen used (can have second reaction later)",
            "Swelling spreading rapidly",
            "Widespread hives with breathing difficulty or vomiting",
        ],
        "call_111_if": [
            "Mild localised reaction (small hives, mild itching) with no breathing problems",
            "Reaction settling but you want advice on follow-up or allergy testing",
        ],
    },
}


class EmergencyQuickRefParams(BaseModel):
    symptom: str = Field(description="The symptom or emergency to look up. Examples: 'fever', 'choking', 'rash', 'burns', 'head injury', 'breathing difficulty', 'vomiting', 'diarrhoea', 'seizure', 'allergic reaction'")


@define_tool(description="Toddler Down 🚨 — Emergency quick-reference for toddler health concerns. Uses ONLY official NHS sources (nhs.uk). Returns immediate actions, do-not warnings, when to go to hospital, and when to call 111.")
async def emergency_quick_ref(params: EmergencyQuickRefParams) -> str:
    """Look up toddler emergency quick-reference guidance from official NHS sources."""
    # Normalise input
    symptom_lower = params.symptom.lower().strip()

    # Map common variations to keys
    keyword_map = {
        "fever": "fever",
        "temperature": "fever",
        "hot": "fever",
        "choking": "choking",
        "choke": "choking",
        "swallowed": "choking",
        "rash": "rash",
        "spots": "rash",
        "meningitis": "rash",
        "burn": "burns",
        "scald": "burns",
        "hot water": "burns",
        "head": "head_injury",
        "bump": "head_injury",
        "fell": "head_injury",
        "fall": "head_injury",
        "concussion": "head_injury",
        "breathing": "breathing_difficulty",
        "croup": "breathing_difficulty",
        "wheeze": "breathing_difficulty",
        "asthma": "breathing_difficulty",
        "stridor": "breathing_difficulty",
        "vomit": "vomiting_diarrhoea",
        "sick": "vomiting_diarrhoea",
        "diarrhoea": "vomiting_diarrhoea",
        "diarrhea": "vomiting_diarrhoea",
        "dehydrat": "vomiting_diarrhoea",
        "seizure": "seizure",
        "fit": "seizure",
        "convulsion": "seizure",
        "febrile": "seizure",
        "allerg": "allergic_reaction",
        "anaphylaxis": "allergic_reaction",
        "epipen": "allergic_reaction",
        "swelling": "allergic_reaction",
        "hives": "allergic_reaction",
    }

    # Find matching guidance
    matched_key = None
    for keyword, key in keyword_map.items():
        if keyword in symptom_lower:
            matched_key = key
            break

    if not matched_key:
        # Return list of available topics
        available = [g["title"] for g in NHS_GUIDANCE.values()]
        return (
            "🚨 **Toddler Down — Emergency Quick-Ref**\n\n"
            "I don't have specific guidance for that symptom.\n\n"
            "**Available topics:**\n"
            + "\n".join(f"- {t}" for t in available)
            + "\n\n**In any emergency, call 999.**\n"
            "**For non-emergency medical advice, call NHS 111.**\n\n"
            "📋 Source: nhs.uk"
        )

    guidance = NHS_GUIDANCE[matched_key]

    # Format the response
    sections = []
    sections.append(f"🚨 **Toddler Down — {guidance['title']}**")
    sections.append(f"📋 NHS Source: {guidance['source']}")
    sections.append("")

    # Immediate actions
    sections.append("## ⚡ What to do NOW:")
    for action in guidance["immediate_actions"]:
        sections.append(f"- {action}")
    sections.append("")

    # Do NOT section
    if guidance.get("do_not"):
        sections.append("## 🚫 Do NOT:")
        for item in guidance["do_not"]:
            sections.append(f"- {item}")
        sections.append("")

    # When to go to hospital
    sections.append("## 🏥 When to go to hospital (A&E / call 999):")
    for item in guidance["when_to_go_to_hospital"]:
        sections.append(f"- {item}")
    sections.append("")

    # Call 111
    if guidance.get("call_111_if"):
        sections.append("## 📞 Call NHS 111 if:")
        for item in guidance["call_111_if"]:
            sections.append(f"- {item}")
        sections.append("")

    sections.append("---")
    sections.append("⚠️ This is quick-reference guidance from nhs.uk — not a diagnosis.")
    sections.append("When in doubt: call 999 (emergency) or 111 (non-emergency).")

    return "\n".join(sections)
