
from django.urls import path
from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("api/check/", views.check_insurance, name="check_insurance"),
]