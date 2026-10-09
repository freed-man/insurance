
import re

from playwright.sync_api import (
    sync_playwright,
    TimeoutError as PlaywrightTimeoutError,
)

URL = "https://enquiry.navigate.mib.org.uk/checkyourvehicle"

PAGE_TIMEOUT_MS = 30_000
RESULT_TIMEOUT_MS = 60_000


def normalize_registration(value):
    """Normalize a registration for comparison."""
    return re.sub(r"[^A-Z0-9]", "", value.upper())


def validate_registration(value):
    """Perform basic input validation before opening the browser."""
    value = value.strip().upper()

    if not value:
        return None

    if not re.fullmatch(r"[A-Z0-9 -]{2,13}", value):
        return None

    normalized = normalize_registration(value)

    if not 2 <= len(normalized) <= 13:
        return None

    return value


def extract_result(result_text, requested_registration):
    """
    Return a verified result only when the expected status wording
    and returned registration are both present and consistent.
    """
    status_match = re.search(
        r"This vehicle is showing as\s+"
        r"(UNINSURED|INSURED)\s+in Navigate today",
        result_text,
        re.IGNORECASE,
    )

    registration_match = re.search(
        r"Vehicle Registration Number:\s*\n\s*([A-Z0-9 ]+)",
        result_text,
        re.IGNORECASE,
    )

    if not status_match or not registration_match:
        return {
            "status": "unable_to_verify",
            "reason": "The result did not match the expected format.",
        }

    returned_registration = normalize_registration(
        registration_match.group(1)
    )
    requested_normalized = normalize_registration(
        requested_registration
    )

    if returned_registration != requested_normalized:
        return {
            "status": "unable_to_verify",
            "reason": "The returned registration did not match.",
        }

    status = status_match.group(1).upper()

    return {
        "status": status,
        "registration": returned_registration,
        "reason": None,
    }


def main():
    entered_registration = input(
        "Enter your own vehicle registration: "
    )

    registration = validate_registration(entered_registration)

    if registration is None:
        print("Invalid input. Check the registration and try again.")
        return

    browser = None
    context = None

    try:
        with sync_playwright() as playwright:
            print("Step 1: Opening MIB checker...")

            browser = playwright.chromium.launch(headless=False)

            # Each run gets a fresh, isolated browser session.
            context = browser.new_context()
            page = context.new_page()

            page.set_default_timeout(PAGE_TIMEOUT_MS)
            page.set_default_navigation_timeout(PAGE_TIMEOUT_MS)

            page.goto(URL, wait_until="domcontentloaded")

            print("Step 2: Selecting Personal check...")

            page.get_by_test_id("personal_check_card").click()
            page.get_by_test_id("continueBtn").click()

            print("Step 3: Waiting for terms page...")

            page.get_by_role(
                "heading",
                name="Use & Terms",
            ).wait_for(state="visible")

            checkbox = page.get_by_role("checkbox")

            if checkbox.get_attribute("aria-checked") != "true":
                checkbox.click()

            page.get_by_role(
                "button",
                name="Agree and continue",
            ).click()

            print("Step 4: Entering registration...")

            vrm = page.get_by_test_id("vrm_searchtext")
            vrm.wait_for(state="visible")
            vrm.fill(registration)

            # Dismiss the optional cookie banner using its normal UI.
            print("Step 5: Checking cookie banner...")

            cookie_banner = page.get_by_test_id(
                "userCookiesBanner"
            )

            if (
                cookie_banner.count() > 0
                and cookie_banner.is_visible()
            ):
                reject_button = page.get_by_role(
                    "button",
                    name="Reject analytics cookies",
                )

                if (
                    reject_button.count() > 0
                    and reject_button.is_visible()
                ):
                    reject_button.click()

            print("Step 6: Waiting for submit button...")

            submit_button = page.get_by_test_id("continueBtn")

            page.wait_for_function(
                """() => {
                    const button = document.querySelector(
                        '[data-testid="continueBtn"]'
                    );
                    return button && !button.disabled;
                }""",
                timeout=PAGE_TIMEOUT_MS,
            )

            print("Step 7: Submitting the check...")

            submit_button.click()

            print("Step 8: Waiting for result...")

            result_panel = page.get_by_test_id("VRNResultPage")

            result_panel.wait_for(
                state="visible",
                timeout=RESULT_TIMEOUT_MS,
            )

            result_text = result_panel.inner_text()

            result = extract_result(
                result_text,
                registration,
            )

            print("\n" + "=" * 45)
            print("MIB VEHICLE CHECK")
            print("=" * 45)

            if result["status"] == "unable_to_verify":
                print("Status: UNABLE TO VERIFY")
                print("Reason:", result["reason"])

            else:
                print("Registration:", result["registration"])
                print("Status:", result["status"])

            print("\nMIB result details:")
            print(result_text)

            print(
                "\nImportant: MIB states that this check is "
                "not proof of insurance status."
            )

            input("\nPress Enter to close the browser...")

    except PlaywrightTimeoutError:
        print(
            "\nStatus: UNABLE TO VERIFY "
            "(the page or result timed out)."
        )

    except Exception as exc:
        # Avoid printing page contents or a full traceback by default.
        print("\nStatus: UNABLE TO VERIFY.")
        print("Unexpected error type:", type(exc).__name__)

    finally:
        if context is not None:
            try:
                context.close()
            except Exception:
                pass

        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass


if __name__ == "__main__":
    main()