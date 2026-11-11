from django.conf import settings
from django.db import models
from django.db.models.expressions import RawSQL
from django.utils import timezone
import uuid


class Post(models.Model):
    class Category(models.TextChoices):
        NOTICE = "notice", "공지사항"
        FREE = "free", "자유게시판"
        IT = "it", "IT정보"
        PROJECT = "project", "프로젝트 공유·피드백"
        CAREER = "career", "취업·진로"
        STUDY = "study", "학습자료 공유"
        QUESTION = "question", "질문·답변"

    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="community_posts")
    post_uuid = models.UUIDField(default=uuid.uuid4, primary_key=True, editable=False)
    id = models.BigIntegerField(unique=True, editable=False, db_default=RawSQL("nextval('community_post_display_seq')", []))
    revision = models.PositiveIntegerField(default=1)
    deleted_at = models.DateTimeField(null=True, blank=True)
    category = models.CharField(max_length=12, choices=Category.choices)
    title = models.CharField(max_length=140)
    body = models.TextField(max_length=20000)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        indexes = [models.Index(fields=["category", "-created_at"], name="community_category_date")]


class Comment(models.Model):
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name="comments", db_column="post_uuid")
    parent = models.ForeignKey(
        "self", to_field="comment_uuid", db_column="parent_comment_uuid",
        null=True, blank=True, on_delete=models.CASCADE, related_name="replies",
    )
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="community_comments")
    comment_uuid = models.UUIDField(default=uuid.uuid4, primary_key=True, editable=False)
    id = models.BigIntegerField(unique=True, editable=False, db_default=RawSQL("nextval('community_comment_display_seq')", []))
    revision = models.PositiveIntegerField(default=1)
    deleted_at = models.DateTimeField(null=True, blank=True)
    body = models.TextField(max_length=2000)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at", "pk"]


class PostReadLog(models.Model):
    read_uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    post = models.ForeignKey(Post, db_column="post_uuid", on_delete=models.PROTECT)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, db_column="user_uuid", on_delete=models.PROTECT)
    platform_session = models.ForeignKey("eventing.PlatformSession", db_column="session_uuid", on_delete=models.PROTECT)
    revision = models.PositiveIntegerField()
    occurred_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "community_post_read_log"
