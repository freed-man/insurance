"""Looks a registration up on MIB's Check Your Vehicle page."""

import logging
import os
import re
import threading

from playwright.sync_api import sync_playwright

logger = logging.getLogger(__name__)

MIB_URL = os.environ.get(
    "MIB_URL", "https://enquiry.navigate.mib.org.uk/checkyourvehicle"
)
# Set MIB_HEADLESS=false to run the browser with a window.
HEADLESS = os.environ.get("MIB_HEADLESS", "true").strip().lower() != "false"

# One browser at a time, so two checks together can't exhaust the memory.
_one_at_a_time = threading.Lock()

_SUBMIT_ENABLED_JS = """() => {
    const button = document.querySelector('[data-testid="continueBtn"]');
    return button && !button.disabled;
}"""


def clean_registration(raw):
    """'ab12 cde' -> 'AB12CDE'; None if it can't be a UK registration."""
    registration = re.sub(r"[\s-]", "", raw or "")
    if not re.fullmatch(r"[A-Za-z0-9]{2,7}", registration):
        return None
    return registration.upper()


def lookup(registration):
    """Return {"status": "insured" | "uninsured" | "unknown"}, plus the
    vehicle when MIB shows one."""
    try:
        with _one_at_a_time:
            text = _read_result_page(registration)
    except Exception:
        logger.exception("MIB lookup failed.")
        return {"status": "unknown"}
    result = _parse(text, registration)
    logger.info("MIB lookup finished: %s", result["status"])
    return result


def _read_result_page(registration):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=HEADLESS)
        try:
            page = browser.new_page()
            page.set_default_timeout(15_000)

            page.goto(MIB_URL, wait_until="domcontentloaded")
            card = page.get_by_test_id("personal_check_card")
            card.wait_for(state="visible")
            card.click()
            page.get_by_test_id("continueBtn").click()

            page.get_by_role("heading", name="Use & Terms").wait_for(
                state="visible"
            )
            checkbox = page.get_by_role("checkbox")
            if checkbox.get_attribute("aria-checked") != "true":
                checkbox.click()
            page.get_by_role("button", name="Agree and continue").click()

            vrm = page.get_by_test_id("vrm_searchtext")
            vrm.wait_for(state="visible")
            vrm.fill(registration)
            vrm.press("Tab")

            banner = page.get_by_test_id("userCookiesBanner")
            if banner.count() and banner.is_visible():
                reject = page.get_by_role(
                    "button", name="Reject analytics cookies"
                )
                if reject.count() and reject.is_visible():
                    reject.click()

            page.wait_for_function(_SUBMIT_ENABLED_JS)
            page.get_by_test_id("continueBtn").click()

            panel = page.get_by_test_id("VRNResultPage")
            panel.wait_for(state="visible", timeout=25_000)
            return panel.inner_text()
        finally:
            browser.close()


def _parse(text, registration):
    shown = re.search(
        r"Vehicle Registration Number:\s*([A-Z0-9 ]+)", text, re.IGNORECASE
    )
    if not shown:
        return {"status": "unknown"}
    if re.sub(r"[^A-Z0-9]", "", shown.group(1).upper()) != registration:
        return {"status": "unknown"}

    status = re.search(
        r"This vehicle is showing as\s+(NOT\s+INSURED|UNINSURED|INSURED)\s+"
        r"in Navigate today",
        text,
        re.IGNORECASE,
    )
    if not status:
        return {"status": "unknown"}
    if status.group(1).upper() != "INSURED":
        return {"status": "uninsured"}

    vehicle = re.search(r"Make and model:\s*([^\n]+)", text, re.IGNORECASE)
    return {
        "status": "insured",
        "vehicle": vehicle.group(1).strip() if vehicle else "",
    }
