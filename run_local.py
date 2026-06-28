"""
run_local.py — TwoKeys local batch runner

Runs your real ADK Workflow (app/agent.py) against a curated batch of
sample expense requests, so the Builder and Auditor agents produce real
LLM reasoning that gets written into twokeys_memory.db via app/memory.py.

Run this from the project root (same folder as app/, .env, etc.):

    python run_local.py

Requirements:
    - .env must contain a valid GOOGLE_API_KEY and GOOGLE_GENAI_USE_VERTEXAI=False
    - google-adk must be installed (pip install google-adk)

After running, twokeys_memory.db will be populated. Then:
    1. streamlit run dashboard.py   -> to preview it locally
    2. git add -f twokeys_memory.db -> to include it despite .gitignore
    3. git commit -m "Add populated demo database"
    4. git push                     -> Streamlit Cloud will redeploy with real data
"""

import asyncio
import uuid

from dotenv import load_dotenv
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from app.agent import app as twokeys_app

load_dotenv()

# A deliberately varied batch: covers auto-approve (amount < 100), clean
# approvals, vague-description denials, over-limit denials, blocked-vendor
# denials at different amounts, and a repeat-offender vendor to trigger the
# auditor's "2+ past rejections -> escalate" rule. This avoids the earlier
# problem of 5 near-identical BlockedInc cards in a row.
SAMPLE_EXPENSES = [
    # Auto-approved (under $100, skips agents entirely)
    {"vendor": "Office Depot", "amount": 42.50, "description": "Printer paper and pens for the team"},
    {"vendor": "Starbucks", "amount": 18.75, "description": "Coffee for morning standup"},

    # Clean approvals (good business justification, valid vendor, under $5000)
    {"vendor": "Starbucks", "amount": 150.00, "description": "Coffee and pastries for client onboarding meeting"},
    {"vendor": "Delta Airlines", "amount": 480.00, "description": "Flight to Chicago for the Q3 partner summit"},
    {"vendor": "Adobe", "amount": 599.00, "description": "Annual Creative Cloud license renewal for design team"},

    # Vague/weak description (should likely be denied on policy grounds)
    {"vendor": "General Vendor", "amount": 250.00, "description": "stuff"},
    {"vendor": "QuickMart", "amount": 130.00, "description": "misc"},

    # Over the $5000 limit (should be denied on amount alone)
    {"vendor": "Premium Events Co", "amount": 7200.00, "description": "Venue rental for annual company offsite"},
    {"vendor": "Starbucks", "amount": 6000.00, "description": "Custom espresso machine for office kitchen"},

    # Blocked vendor at varying amounts (tests blocked-vendor logic specifically)
    {"vendor": "BlockedInc", "amount": 120.00, "description": "Software licenses for engineering team"},
    {"vendor": "Shady Supplies Co", "amount": 340.00, "description": "Office furniture restock"},

    # Repeat requests from the same blocked vendor (tests vendor history /
    # auditor's "2+ rejections -> escalate" scrutiny rule)
    {"vendor": "Offshore Holdings LLC", "amount": 500.00, "description": "Consulting services for Q4 planning"},
    {"vendor": "Offshore Holdings LLC", "amount": 275.00, "description": "Quarterly retainer payment"},
    {"vendor": "Offshore Holdings LLC", "amount": 300.00, "description": "Third consecutive request from this vendor"},
]


async def run_one(runner: Runner, session_service: InMemorySessionService, app_name: str, expense: dict) -> None:
    user_id = "demo-user"
    session_id = str(uuid.uuid4())

    await session_service.create_session(
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
    )

    message = types.Content(
        role="user",
        parts=[types.Part(text=(
            f"vendor: {expense['vendor']}\n"
            f"amount: {expense['amount']}\n"
            f"description: {expense['description']}"
        ))],
    )

    print(f"\n--- Processing: {expense['vendor']} | ${expense['amount']:.2f} | {expense['description'][:50]} ---")
    try:
        async for event in runner.run_async(
            user_id=user_id,
            session_id=session_id,
            new_message=message,
        ):
            # Print a short trace so you can watch it work in real time.
            label = getattr(event, "author", None) or type(event).__name__
            print(f"  [{label}] event received")
    except Exception as e:
        print(f"  !! Run failed for {expense['vendor']}: {e}")


async def main() -> None:
    session_service = InMemorySessionService()
    app_name = twokeys_app.name

    runner = Runner(
        app=twokeys_app,
        session_service=session_service,
    )

    for expense in SAMPLE_EXPENSES:
        await run_one(runner, session_service, app_name, expense)
        # Small delay to stay friendly with API rate limits between calls.
        await asyncio.sleep(2)

    print("\nDone. Check twokeys_memory.db for populated decisions.")


if __name__ == "__main__":
    asyncio.run(main())
