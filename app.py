import json
import os
import threading
from pathlib import Path

import anthropic
import streamlit as st
from dotenv import load_dotenv

from instacart_bot import add_ingredients_to_cart

# Load .env from the same directory as this file, regardless of working directory
load_dotenv(Path(__file__).parent / ".env", override=True)

SYSTEM_PROMPT = """You are a helpful assistant that extracts a structured ingredient list from recipes.
Given a recipe (or a raw ingredient list), return ONLY a JSON array — no markdown, no explanation.
Each element must have exactly these keys:
  - "name": the ingredient name (string)
  - "quantity": numeric amount as a string, or "" if unspecified
  - "unit": unit of measure (e.g. "cups", "g", "tbsp"), or "" if unspecified

Example output:
[
  {"name": "chicken breast", "quantity": "2", "unit": "lbs"},
  {"name": "garlic", "quantity": "3", "unit": "cloves"},
  {"name": "olive oil", "quantity": "2", "unit": "tbsp"}
]"""


def parse_ingredients(recipe_text: str) -> list[dict]:
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": recipe_text}],
    )
    raw = message.content[0].text.strip()
    return json.loads(raw)


# ── Streamlit UI ──────────────────────────────────────────────────────────────

st.set_page_config(page_title="Recipe → Instacart", page_icon="🛒", layout="centered")
st.title("🛒 Recipe to Instacart")
st.caption("Paste a recipe, let Claude parse the ingredients, then add them all to your Instacart cart automatically.")

# Session state defaults
if "ingredients" not in st.session_state:
    st.session_state.ingredients = []
if "log_lines" not in st.session_state:
    st.session_state.log_lines = []
if "running" not in st.session_state:
    st.session_state.running = False

# ── Step 1: Recipe input ──────────────────────────────────────────────────────
st.subheader("Step 1 — Paste your recipe")
recipe_text = st.text_area(
    "Recipe or ingredient list",
    height=200,
    placeholder="Paste the full recipe text or a list of ingredients here...",
)

if st.button("Parse Ingredients", disabled=not recipe_text.strip()):
    with st.spinner("Claude is parsing your recipe..."):
        try:
            st.session_state.ingredients = parse_ingredients(recipe_text)
            st.session_state.log_lines = []
        except Exception as e:
            st.error(f"Failed to parse ingredients: {e}")

# ── Step 2: Review ingredient list ───────────────────────────────────────────
if st.session_state.ingredients:
    st.subheader("Step 2 — Review ingredients")
    st.caption("Edit quantities or names below, then remove any items you don't want.")

    updated = []
    for i, item in enumerate(st.session_state.ingredients):
        cols = st.columns([3, 1, 1, 0.4])
        name = cols[0].text_input("Name", value=item["name"], key=f"name_{i}", label_visibility="collapsed")
        qty = cols[1].text_input("Qty", value=item["quantity"], key=f"qty_{i}", label_visibility="collapsed")
        unit = cols[2].text_input("Unit", value=item["unit"], key=f"unit_{i}", label_visibility="collapsed")
        keep = cols[3].checkbox("", value=True, key=f"keep_{i}")
        if keep:
            updated.append({"name": name, "quantity": qty, "unit": unit})

    st.caption(f"{len(updated)} ingredient(s) selected")

    # ── Step 3: Add to Instacart ──────────────────────────────────────────────
    st.subheader("Step 3 — Add to Instacart")
    st.info(
        "Clicking the button below will open an Instacart browser window. "
        "If you're not already logged in, sign in when prompted — the app will wait up to 60 seconds."
    )

    if st.button("🛒 Add to Instacart Cart", disabled=st.session_state.running or not updated):
        st.session_state.running = True
        st.session_state.log_lines = []
        log_placeholder = st.empty()

        def run_bot():
            def append_log(msg):
                st.session_state.log_lines.append(msg)

            add_ingredients_to_cart(updated, log_callback=append_log)
            st.session_state.running = False

        thread = threading.Thread(target=run_bot, daemon=True)
        thread.start()

        # Stream log output while bot is running
        while st.session_state.running or thread.is_alive():
            log_placeholder.code("\n".join(st.session_state.log_lines) or "Starting...", language=None)
            threading.Event().wait(0.5)

        log_placeholder.code("\n".join(st.session_state.log_lines), language=None)
        st.success("Done! Check the browser window (or your Instacart cart).")
        st.session_state.running = False

    if st.session_state.log_lines and not st.session_state.running:
        st.code("\n".join(st.session_state.log_lines), language=None)
