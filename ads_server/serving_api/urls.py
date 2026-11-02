from django.urls import path

from . import views

urlpatterns = [
    path("health", views.health),
    path("decisions", views.create_decision),
    path("decisions/<str:decision_id>", views.get_decision),
    path("decisions/<str:decision_id>/reactions/<str:reaction_id>", views.reaction),  # 형식이 틀린 ID: GET 은 404(없는 것과 같다), PUT 은 400(`requests.path_uuid` — 반응 PUT 은 404 를 선언하지 않는다)
]
