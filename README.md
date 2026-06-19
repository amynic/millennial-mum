# Millennial Mum 🍼💼

The ultimate AI copilot for working parents juggling careers and small children.

Built with the [GitHub Copilot Python SDK](https://github.com/github/copilot-sdk).

## Features

- 🍱 **Meal Planner** — Quick healthy kid-friendly meals from what you have
- 📅 **Schedule Manager** — Juggle childcare, school runs, work, appointments
- 🎨 **Activity Finder** — Age-appropriate activities for available time/weather
- 💰 **Budget Helper** — Track family spend, find savings
- 📝 **Admin Autopilot** — Draft school emails, absence notes, appointment reminders
- 🚨 **Emergency Quick-Ref** — First aid, when to call 111/999, nearest GP

## Prerequisites

- Python 3.11+
- GitHub Copilot CLI installed (`gh copilot` or standalone)
- `gh auth login` + `gh auth refresh --scopes copilot`

## Setup

```bash
cd millennial-mum
python -m venv .venv
.venv\Scripts\activate      # Windows
# source .venv/bin/activate  # Mac/Linux
pip install -r requirements.txt
```

## Run

```bash
python app.py
```

## Architecture

```
millennial-mum/
├── app.py                  # Main entry point - interactive chat
├── agent_config.py         # System prompt & agent personality
├── tools/
│   ├── __init__.py
│   ├── meal_planner.py     # Meal suggestions & grocery lists
│   ├── schedule.py         # Calendar & reminder management
│   ├── activities.py       # Activity finder by age/time/weather
│   ├── budget.py           # Family budget tracking
│   └── admin.py            # Email drafts, forms, notes
├── requirements.txt
└── README.md
```

## Next: Deploy to Microsoft Foundry

Once running locally, this agent can be wrapped with the Microsoft Agent Framework 
hosting adapter and deployed to Microsoft Foundry as a hosted agent with:
- Hosted models (GPT-4o, GPT-5)
- Foundry Toolbox (web search, AI search, etc.)
- Production eval & tracing
