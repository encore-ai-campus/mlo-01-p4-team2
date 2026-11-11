from django.urls import path
from . import main_views as views
urlpatterns = [path('status/', views.status), path('ranking/', views.ranking), path('action/', views.action)]
