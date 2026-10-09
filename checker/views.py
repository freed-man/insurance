
import re
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST


def home(request):
    return render(request, "checker/index.html")


@require_POST
def check_insurance(request):
    registration = request.POST.get("registration", "").strip().upper()
    registration = re.sub(r"\s+", "", registration)

    # Basic format/empty-input validation only.
    # The live insurance lookup will be connected later.
    if not registration:
        return JsonResponse({
            "status": "invalid",
            "message": "Please enter a vehicle registration."
        }, status=400)

    if not re.fullmatch(r"[A-Z0-9]{2,8}", registration):
        return JsonResponse({
            "status": "invalid",
            "message": "Please check the registration and try again."
        }, status=400)

    return JsonResponse({
        "status": "unknown",
        "registration": registration,
        "message": (
            "The live insurance lookup is not connected yet. "
            "No insurance status has been confirmed."
        )
    })