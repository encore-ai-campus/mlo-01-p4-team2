from django.urls import path

from . import views

app_name = "community"
urlpatterns = [
    path("meal-comments/", views.meal_comments, name="meal-comments"),
    path("meal-comments/<int:comment_id>/", views.meal_comment_detail, name="meal-comment-detail"),
    path("", views.metadata, name="metadata"),
    path("posts/", views.posts, name="posts"),
    path("posts/<int:post_id>/", views.post_detail, name="post-detail"),
    path("posts/<int:post_id>/comments/", views.comments, name="comments"),
    path("comments/<int:comment_id>/", views.comment_detail, name="comment-detail"),
]
