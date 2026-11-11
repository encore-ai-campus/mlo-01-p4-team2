from django.urls import path
from . import views, achievement_views

urlpatterns = [path('recipe/status/', views.recipe_status), path('recipe/action/', views.recipe_action), path('achievements/', achievement_views.achievements), path('plays/<uuid:play_uuid>/result/', achievement_views.game_result), path('status/', views.status), path('action/', views.action)]
