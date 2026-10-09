
import re

from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST
from playwright.sync_api import (
    sync_playwright,
    TimeoutError as PlaywrightTimeoutError,
)

MIB_URL = "https://enquiry.navigate.mib.org.uk/checkyourvehicle"

PAGE_TIMEOUT_MS = 30_000
RESULT_TIMEOUT_MS = 60_000


def home(request):
    return render(request, "checker/index.html")


def normalize_registration(value):
    return re.sub(r"[^A-Z0-9]", "", value.upper())


def error_response(message, status="unknown", http_status=200):
    return JsonResponse(
        {
            "status": status,
            "message": message,
        },
        status=http_status,
    )


def check_mib(registration):
    browser = None
    context = None

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context()
            page = context.new_page()

            page.set_default_timeout(PAGE_TIMEOUT_MS)
            page.set_default_navigation_timeout(PAGE_TIMEOUT_MS)

            # Open the official MIB personal vehicle checker.
            page.goto(
                MIB_URL,
                wait_until="domcontentloaded",
            )

            # Select the personal vehicle check.
            page.get_by_test_id("personal_check_card").click()
            page.get_by_test_id("continueBtn").click()

            # Accept the site's terms to continue the requested check.
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

            # Enter the registration.
            vrm = page.get_by_test_id("vrm_searchtext")
            vrm.wait_for(state="visible")
            vrm.fill(registration)
            vrm.press("Tab")

            # Use the site's normal cookie preference control if needed.
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

            # Wait for the site's submit button to become enabled.
            page.wait_for_function(
                """() => {
                    const button = document.querySelector(
                        '[data-testid="continueBtn"]'
                    );
                    return button && !button.disabled;
                }""",
                timeout=PAGE_TIMEOUT_MS,
            )

            page.get_by_test_id("continueBtn").click()

            # Wait for the actual result, not an intermediate loading page.
            result_panel = page.get_by_test_id("VRNResultPage")
            result_panel.wait_for(
                state="visible",
                timeout=RESULT_TIMEOUT_MS,
            )

            result_text = result_panel.inner_text()

            # Confirm that the returned registration matches the request.
            registration_match = re.search(
                r"Vehicle Registration Number:\s*([A-Z0-9 ]+)",
                result_text,
                re.IGNORECASE,
            )

            if not registration_match:
                return {
                    "status": "unknown",
                    "message": (
                        "MIB returned a page, but the vehicle "
                        "registration could not be verified."
                    ),
                }

            returned_registration = normalize_registration(
                registration_match.group(1)
            )

            if returned_registration != normalize_registration(
                registration
            ):
                return {
                    "status": "unknown",
                    "message": (
                        "The registration returned by MIB did not "
                        "match the requested registration."
                    ),
                }

            # Only report insured when the exact expected wording
            # appears on the verified result page.
            insured_match = re.search(
                r"This vehicle is showing as\s+INSURED\s+"
                r"in Navigate today",
                result_text,
                re.IGNORECASE,
            )

            if insured_match:
                vehicle_match = re.search(
                    r"Make and model:\s*([^\n]+)",
                    result_text,
                    re.IGNORECASE,
                )

                make_and_model = (
                    vehicle_match.group(1).strip()
                    if vehicle_match
                    else None
                )

                return {
                    "status": "insured",
                    "registration": returned_registration,
                    "make_and_model": make_and_model,
                    "message": (
                        "MIB currently shows this vehicle as insured "
                        "in Navigate. This check is not proof of "
                        "insurance status. Updates may take 7 days "
                        "or longer."
                    ),
                }

            # Do not infer that a vehicle is uninsured just because
            # the expected insured wording was not found.
            return {
                "status": "unknown",
                "registration": returned_registration,
                "message": (
                    "MIB returned a result, but this application "
                    "could not safely interpret its insurance status. "
                    "Please check directly with MIB or your insurer."
                ),
            }

    except PlaywrightTimeoutError:
        return {
            "status": "unknown",
            "message": (
                "The MIB check timed out. Unable to verify the "
                "vehicle at this time. Please try again later."
            ),
        }

    except Exception:
        # Avoid returning internal error details or page contents
        # to the website visitor.
        return {
            "status": "unknown",
            "message": (
                "The MIB check could not be completed. "
                "Please try again later."
            ),
        }

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


@require_POST
def check_insurance(request):
    registration = request.POST.get(
        "registration", ""
    ).strip().upper()

    registration = re.sub(r"\s+", "", registration)

    if not registration:
        return error_response(
            "Please enter a vehicle registration.",
            status="invalid",
            http_status=400,
        )

    if not re.fullmatch(r"[A-Z0-9]{2,13}", registration):
        return error_response(
            "Please check the registration and try again.",
            status="invalid",
            http_status=400,
        )

    result = check_mib(registration)

    result.setdefault("registration", registration)

    return JsonResponse(result)