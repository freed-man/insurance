"""Writes the simplified insurance checker into this repo.

Save as apply_simple_version.py next to manage.py, then run:

    python apply_simple_version.py
"""

import sys
from pathlib import Path

FILES = {}

FILES["requirements.txt"] = r'''
Django>=5.2,<5.3
gunicorn>=23,<24
playwright==1.63.0  # keep equal to the image tag in the Dockerfile
'''

FILES[".dockerignore"] = r'''
.git
.venv
.env
__pycache__/
*.py[cod]
db.sqlite3
'''

FILES["Dockerfile"] = r'''
# This tag and the playwright pin in requirements.txt must be the same version.
FROM mcr.microsoft.com/playwright/python:v1.63.0-noble

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DISPLAY=:99

WORKDIR /app

# Virtual display for headed Chromium. The socket directory is created here
# because Xvfb is started by a non-root user at runtime.
RUN apt-get update \
    && apt-get install -y --no-install-recommends xvfb \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /tmp/.X11-unix \
    && chmod 1777 /tmp/.X11-unix

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Don't run a browser that renders third-party pages as root.
USER pwuser

# Xvfb runs in the background and exec makes gunicorn PID 1, so it still
# receives SIGTERM and shuts down cleanly. xvfb-run would not pass it on.
CMD ["sh", "-c", "rm -f /tmp/.X99-lock; Xvfb :99 -screen 0 1280x800x24 -nolisten tcp & exec gunicorn config.wsgi:application --bind 0.0.0.0:${PORT:-8000} --worker-class gthread --workers 1 --threads 8 --graceful-timeout 30"]
'''

FILES["config/settings.py"] = r'''
import os
import secrets

DEBUG = os.environ.get("DJANGO_DEBUG", "").strip().lower() == "true"

# Nothing in this app is signed with the key, so a fresh one per start is fine.
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY") or secrets.token_urlsafe(50)

ALLOWED_HOSTS = [
    host.strip()
    for host in os.environ.get(
        "DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost"
    ).split(",")
    if host.strip()
]

INSTALLED_APPS = ["checker"]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "APP_DIRS": True,
    },
]

TIME_ZONE = "UTC"
USE_TZ = True

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}
'''

FILES["config/urls.py"] = r'''
from django.urls import include, path

urlpatterns = [
    path("", include("checker.urls")),
]
'''

FILES["checker/mib.py"] = r'''
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
'''

FILES["checker/views.py"] = r'''
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST

from . import mib


def home(request):
    return render(request, "checker/index.html")


@require_POST
def check_insurance(request):
    registration = mib.clean_registration(request.POST.get("registration"))
    if registration is None:
        return JsonResponse({"status": "invalid"}, status=400)
    return JsonResponse({"registration": registration, **mib.lookup(registration)})
'''

FILES["checker/tests.py"] = r'''
# No tests yet.
'''

FILES["checker/templates/checker/index.html"] = r'''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Check vehicle insurance</title>
    <style>
        body {
            max-width: 440px;
            margin: 20vh auto 0;
            padding: 0 20px;
            font-family: Arial, Helvetica, sans-serif;
            color: #172b4d;
        }

        form {
            display: flex;
            gap: 8px;
        }

        input {
            flex: 1;
            min-width: 0;
            padding: 12px;
            border: 2px solid #ccd5df;
            border-radius: 8px;
            font-size: 20px;
            font-weight: bold;
            text-transform: uppercase;
        }

        button {
            padding: 12px 18px;
            border: 0;
            border-radius: 8px;
            background: #176b50;
            color: white;
            font-size: 16px;
            font-weight: bold;
            cursor: pointer;
        }

        button:disabled {
            opacity: 0.6;
            cursor: wait;
        }

        #result {
            margin-top: 16px;
            font-size: 18px;
        }

        #result.insured { color: #145c35; }
        #result.uninsured { color: #8c211b; }
    </style>
</head>
<body>
    <form id="check-form" method="post">
        <input
            name="registration"
            placeholder="AB12 CDE"
            maxlength="8"
            autocomplete="off"
            autocapitalize="characters"
            aria-label="Vehicle registration"
            required
        >
        <button id="submit-button" type="submit">Check insurance</button>
    </form>

    <p id="result" role="status"></p>

    <script>
        const form = document.getElementById("check-form");
        const button = document.getElementById("submit-button");
        const result = document.getElementById("result");

        const MESSAGES = {
            insured: "showing as insured",
            uninsured: "not showing as insured",
            invalid: "That doesn't look like a UK registration.",
            unknown: "Couldn't check right now. Please try again."
        };

        form.addEventListener("submit", async (event) => {
            event.preventDefault();
            button.disabled = true;
            result.className = "";
            result.textContent = "Checking...";

            let data = {};
            try {
                const response = await fetch("{% url 'check_insurance' %}", {
                    method: "POST",
                    body: new FormData(form)
                });
                data = await response.json();
            } catch (error) {
                // Network failure or a non-JSON error page: shown as "unknown".
            }

            const status = Object.hasOwn(MESSAGES, data.status)
                ? data.status
                : "unknown";

            let text = MESSAGES[status];
            if (status === "insured" || status === "uninsured") {
                text = `${data.registration}: ${text}`;
                if (data.vehicle) {
                    text += ` (${data.vehicle})`;
                }
            }

            result.className = status;
            result.textContent = text;
            button.disabled = false;
        });
    </script>
</body>
</html>
'''

if not Path("manage.py").exists():
    sys.exit("Run this from the repo root, next to manage.py.")

for name, content in FILES.items():
    path = Path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.lstrip("\n"), encoding="utf-8")
    print("wrote", name)