
import logging
import os
import re

from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST

from playwright.sync_api import (
    sync_playwright,
    TimeoutError as PlaywrightTimeoutError,
)

logger = logging.getLogger(__name__)

MIB_URL = "https://enquiry.navigate.mib.org.uk/checkyourvehicle"

PAGE_TIMEOUT_MS = 15_000
RESULT_TIMEOUT_MS = 60_000

# Set MIB_HEADLESS=false locally to see the browser.
# Defaults to headless mode for deployment.
MIB_HEADLESS = (
    os.environ.get("MIB_HEADLESS", "true").strip().lower() == "true"
)


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
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=MIB_HEADLESS
            )

            try:
                context = browser.new_context()

                try:
                    page = context.new_page()
                    page.set_default_timeout(PAGE_TIMEOUT_MS)
                    page.set_default_navigation_timeout(
                        PAGE_TIMEOUT_MS
                    )

                    logger.info(
                        "Opening MIB checker. Headless=%s",
                        MIB_HEADLESS,
                    )

                    page.goto(
                        MIB_URL,
                        wait_until="domcontentloaded",
                    )

                    logger.info(
                        "MIB landing page: title=%s url=%s",
                        page.title(),
                        page.url,
                    )

                    personal_card = page.get_by_test_id(
                        "personal_check_card"
                    )

                    try:
                        personal_card.wait_for(
                            state="visible",
                            timeout=PAGE_TIMEOUT_MS,
                        )
                    except PlaywrightTimeoutError:
                        body_text = page.locator(
                            "body"
                        ).inner_text()

                        logger.warning(
                            "Personal check card not found. "
                            "Title=%s URL=%s Page text=%s",
                            page.title(),
                            page.url,
                            body_text[:2500],
                        )

                        return {
                            "status": "unknown",
                            "message": (
                                "The MIB page did not display the "
                                "expected personal check option. "
                                "Please try again later."
                            ),
                        }

                    personal_card.click()
                    page.get_by_test_id("continueBtn").click()

                    page.get_by_role(
                        "heading",
                        name="Use & Terms",
                    ).wait_for(state="visible")

                    checkbox = page.get_by_role("checkbox")

                    if (
                        checkbox.get_attribute("aria-checked")
                        != "true"
                    ):
                        checkbox.click()

                    page.get_by_role(
                        "button",
                        name="Agree and continue",
                    ).click()

                    vrm = page.get_by_test_id("vrm_searchtext")
                    vrm.wait_for(state="visible")
                    vrm.fill(registration)
                    vrm.press("Tab")

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

                    result_panel = page.get_by_test_id(
                        "VRNResultPage"
                    )

                    result_panel.wait_for(
                        state="visible",
                        timeout=RESULT_TIMEOUT_MS,
                    )

                    result_text = result_panel.inner_text()

                    logger.info("MIB result page reached.")

                    registration_match = re.search(
                        r"Vehicle Registration Number:\s*"
                        r"([A-Z0-9 ]+)",
                        result_text,
                        re.IGNORECASE,
                    )

                    if not registration_match:
                        logger.warning(
                            "MIB result did not contain a "
                            "recognisable registration."
                        )

                        return {
                            "status": "unknown",
                            "message": (
                                "MIB returned a page, but the "
                                "registration could not be verified."
                            ),
                        }

                    returned_registration = normalize_registration(
                        registration_match.group(1)
                    )

                    if returned_registration != normalize_registration(
                        registration
                    ):
                        logger.warning(
                            "MIB registration did not match request."
                        )

                        return {
                            "status": "unknown",
                            "message": (
                                "The registration returned by MIB "
                                "did not match the requested "
                                "registration."
                            ),
                        }

                    # Check NOT INSURED first. This prevents the
                    # word INSURED inside NOT INSURED being matched.
                    not_insured_match = re.search(
                        r"This vehicle is showing as\s+"
                        r"NOT\s+INSURED\s+"
                        r"in Navigate today",
                        result_text,
                        re.IGNORECASE,
                    )

                    if not_insured_match:
                        logger.info(
                            "MIB reports NOT INSURED in Navigate."
                        )

                        return {
                            "status": "uninsured",
                            "registration": returned_registration,
                            "message": (
                                "MIB currently shows this vehicle "
                                "as NOT INSURED in Navigate. This "
                                "is not definitive proof that the "
                                "vehicle has no insurance. Updates "
                                "may take 7 days or longer. If you "
                                "believe the vehicle is insured, "
                                "contact your insurer and keep your "
                                "policy details."
                            ),
                        }

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

                        logger.info(
                            "MIB reports INSURED in Navigate."
                        )

                        return {
                            "status": "insured",
                            "registration": returned_registration,
                            "make_and_model": make_and_model,
                            "message": (
                                "MIB currently shows this vehicle "
                                "as insured in Navigate. This check "
                                "is not proof of insurance status. "
                                "Updates may take 7 days or longer."
                            ),
                        }

                    logger.warning(
                        "MIB result did not match a recognised "
                        "insurance status."
                    )

                    return {
                        "status": "unknown",
                        "registration": returned_registration,
                        "message": (
                            "MIB returned a result, but this "
                            "application could not identify the "
                            "insurance status. Please check directly "
                            "with MIB or your insurer."
                        ),
                    }

                finally:
                    context.close()

            finally:
                browser.close()

    except PlaywrightTimeoutError:
        logger.exception("MIB lookup timed out.")

        return {
            "status": "unknown",
            "message": (
                "The MIB check timed out. Please try again later."
            ),
        }

    except Exception:
        logger.exception("Unexpected error during MIB lookup.")

        return {
            "status": "unknown",
            "message": (
                "The MIB check could not be completed. "
                "Please try again later."
            ),
        }


@require_POST
def check_insurance(request):
    registration = normalize_registration(
        request.POST.get("registration", "")
    )

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

    logger.info("Received a vehicle check request.")

    result = check_mib(registration)
    result.setdefault("registration", registration)

    return JsonResponse(result)
