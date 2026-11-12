from django.urls import path

from . import views

urlpatterns = [
    path("ad-groups", views.list_ad_groups),
    path("profiles/categories", views.write_categories),
    path("profiles/weights", views.write_weights),
]
