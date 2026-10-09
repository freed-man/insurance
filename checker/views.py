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
