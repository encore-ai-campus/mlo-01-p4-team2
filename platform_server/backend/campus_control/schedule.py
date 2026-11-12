"""Daily KST operating hours. All intervals include the start and exclude the end."""
from datetime import datetime,time,timedelta
from zoneinfo import ZoneInfo
from django.utils import timezone
TZ=ZoneInfo('Asia/Seoul')
LESSONS=((9,0,9,50),(10,0,10,50),(11,0,11,50),(12,0,12,50),
         (14,0,14,50),(15,0,15,50),(16,0,16,50),(17,0,17,50))

def snapshot(now=None):
 now=(now or timezone.now()).astimezone(TZ)
 def at(h,m,days=0):return datetime.combine(now.date()+timedelta(days=days),time(h,m),TZ)
 if now<at(8,30):
  resume=at(8,30);phase='outside'
 elif now>=at(22,0):
  resume=at(8,30,1);phase='outside'
 else:
  for h,m,eh,em in LESSONS:
   if at(h,m)<=now<at(eh,em):
    resume=at(eh,em);phase='class';break
  else:
   boundary=next((at(h,m) for h,m,_,_ in LESSONS if at(h,m)>now),at(22,0))
   return dict(paused=False,phase='open',message='캠퍼스를 이용할 수 있어요.',nextResumeAt=None,nextChangeAt=boundary.isoformat())
 label=resume.strftime('%H:%M') if resume.date()==now.date() else resume.strftime('%m월 %d일 %H:%M')
 message=(f'강의 시간에는 이용할 수 없습니다. {label}부터 다시 이용할 수 있어요.' if phase=='class' else
          f'캠퍼스 운영 시간은 매일 08:30~22:00입니다. {label}에 다시 열려요.')
 return dict(paused=True,phase=phase,message=message,nextResumeAt=resume.isoformat(),nextChangeAt=resume.isoformat())
