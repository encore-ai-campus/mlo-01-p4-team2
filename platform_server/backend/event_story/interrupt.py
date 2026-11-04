from django.utils import timezone
from .models import GamePlay

def interrupt_running():
    """Called within the shared freeze transaction; settled stages remain untouched."""
    now=timezone.now()
    for play in GamePlay.objects.select_for_update().filter(status='RUNNING'):
        play.status='INTERRUPTED';play.ended_at=now
        play.duration_ms=max(0,int((now-play.started_at).total_seconds()*1000))
        play.save(update_fields=['status','ended_at','duration_ms'])
