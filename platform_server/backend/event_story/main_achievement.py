"""Personal main-quest badge, derived from the existing final delivery receipt."""
import uuid
from .models import EventProgress

FINAL_ID = uuid.uuid5(uuid.NAMESPACE_URL, 'playdata/pepero-2026/v3/6')
IMAGE = '/static/campus/assets/achievements/kkuwang-honorary-assistant-v1.webp'

def snapshot(user):
    progress = EventProgress.objects.filter(user=user, event_id=FINAL_ID,
        status=EventProgress.Status.COMPLETED, completed_at__isnull=False).first()
    return from_completion(user, progress.completed_at if progress else None)

def from_completion(user, completed_at):
    """Reuse a loaded final receipt when rendering the main status response."""
    return dict(event_uuid=str(FINAL_ID), name='꾸왕이의 명예 조수',
        kind='MAIN_MISSION', achieved=completed_at is not None,
        can_preview=bool(user.is_staff or user.is_superuser),
        completed_at=completed_at.isoformat() if completed_at else None,
        image_url=IMAGE)
