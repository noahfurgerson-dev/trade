import time
import urllib.parse
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

INSTACART_HOME = "https://www.instacart.com"
INSTACART_SEARCH = "https://www.instacart.com/store/s?k={}"

# The add button on search results has aria-label starting with "Add "
# and does NOT start with "Add to" (which appears on other UI elements).
ADD_BTN_SELECTOR = 'button[aria-label^="Add "]'


def add_ingredients_to_cart(ingredients: list[dict], log_callback=None) -> dict:
    """
    Opens Instacart in a visible browser, waits for the user to log in if needed,
    then searches each ingredient and adds the first result to cart.

    Args:
        ingredients: list of dicts with keys "name", "quantity", "unit"
        log_callback: optional callable(str) for progress messages

    Returns:
        dict with keys "added" (list), "failed" (list)
    """
    def log(msg: str):
        if log_callback:
            log_callback(msg)

    results = {"added": [], "failed": []}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=200)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()

        log("Opening Instacart...")
        page.goto(INSTACART_HOME)

        log("Please log in to Instacart in the browser window if prompted. Waiting up to 60 seconds...")
        try:
            # Cart button is present on all pages once a store is selected / logged in
            page.wait_for_selector('button[aria-label^="View Cart"]', timeout=60_000)
            log("Ready. Starting to add ingredients...")
        except PlaywrightTimeoutError:
            log("Could not confirm login state — attempting to continue anyway...")

        for item in ingredients:
            name = item.get("name", "")
            qty = item.get("quantity", "")
            unit = item.get("unit", "")
            display = f"{qty} {unit} {name}".strip()

            try:
                log(f"Searching: {display}")
                _add_single_item(page, name, display, log)
                results["added"].append(display)
            except Exception as e:
                log(f"  Failed to add '{display}': {e}")
                results["failed"].append(display)

        log(f"\nDone! Added {len(results['added'])}, failed {len(results['failed'])}.")
        log("You can review your cart in the browser. Closing in 10 seconds...")
        time.sleep(10)
        browser.close()

    return results


def _add_single_item(page, search_term: str, display: str, log):
    """Navigate directly to Instacart search results and click the first Add button."""
    url = INSTACART_SEARCH.format(urllib.parse.quote_plus(search_term))
    page.goto(url)
    page.wait_for_load_state("domcontentloaded", timeout=15_000)

    # Wait for at least one Add button to appear
    try:
        page.wait_for_selector(ADD_BTN_SELECTOR, timeout=10_000)
    except PlaywrightTimeoutError:
        raise RuntimeError("No 'Add' buttons found on search results page")

    add_btn = page.locator(ADD_BTN_SELECTOR).first
    add_btn.scroll_into_view_if_needed()
    add_btn.click()
    log(f"  Added: {display}")
    time.sleep(0.3)
