"""광고주 대시보드(Step 3-2a). 기간의 일별 성과(`records.daily_report`)·상위 캠페인·알림(`console/queries`)·포인트(`billing.get_wallet`)를 부르고 그리기만 한다.

기간은 **오늘을 포함한 최근 N일**(예산 일 경계)이고 비교는 바로 앞의 같은 길이 구간이다(설계 D3-5). 오늘 칸은 아직 집계 중이라 표시한다.
"""
from datetime import timedelta

from django.utils import timezone

from billing import api as billing
from campaigns import api as campaigns
from records import api as records

from ..forms import METRICS, PeriodForm
from ..queries.alerts import alerts_for, wallet_or_none
from ..queries.dashboard import TOP, ad_rows, top_campaigns
from .common import present


def _now():
    return timezone.now()


def _period(request):
    """주소의 기간·그래프 지표(틀리면 기본값)와 그 기간: (고른 값, 다시 그릴 폼, 지금, 오늘 예산 일, 시작, 끝). 시작·끝은 오늘을 포함한 최근 N일의 예산 일 경계."""
    chosen = PeriodForm(request.GET or None).values()
    # 선택 상자는 실제로 쓴 값을 보인다 — 주소에 없거나 틀린 칸도 기본값으로(그대로 그리면 selected 가 없어 브라우저가 첫 선택지 '오늘'을 보인다, 6-23e)
    now = _now()
    today = billing.budget_day_of(now)
    return chosen, PeriodForm(initial=chosen), now, today, billing.day_start(today - timedelta(days=chosen["days"] - 1)), billing.day_start(today + timedelta(days=1))


def advertiser_home(request):
    chosen, form, now, today, start, end = _period(request)
    report = records.daily_report(request.user, start, end)
    # '오늘'(1일)은 그래프·표에 어제도 그려 둘을 나란히 비교한다(6-23c) — 카드의 비교(오늘 대 어제)는 그대로 report 가 한다
    chart_rows = report.rows if chosen["days"] > 1 else records.daily_report(request.user, billing.day_start(today - timedelta(days=1)), end).rows
    wallet = wallet_or_none(request.user, now)
    camps = campaigns.list_campaigns(request.user, now=now)  # 지갑과 함께 상위 캠페인 표·알림이 같은 읽기를 본다
    alerts = alerts_for(request.user, now, camps, wallet)
    return present(request, "console/advertiser_home.html", {
        "form": form, "days": chosen["days"], "metric": chosen["metric"], "metric_label": dict(METRICS)[chosen["metric"]],
        "metrics": METRICS, "report": report, "chart_rows": chart_rows, "today": today, "wallet": wallet, "top": top_campaigns(request.user, start, end, camps), "top_limit": TOP,
        "alerts": alerts, "has_alerts": bool(alerts),  # 헤더 종의 점 — 패널과 같은 계산(다른 화면은 views/common.py 가 계산)
        "campaign_count": len(camps),  # 0 이면 처음 사용 화면(시작하기 3단계 — 6-21c)
        "daily_budget_total": sum(c.daily_budget for c in camps if c.status == "ACTIVE"),  # 지갑 칸의 '일예산 합계 대비 오늘 소진'(6-21c) — 같은 읽기, 질의 없음
    })


def ad_report(request):
    """광고별 성과(대시보드 "전체 보기", 설계 §10 "광고주 화면 정리" ③): 대시보드와 같은 기간의 광고마다 노출·클릭·전환. 소진 포인트는 캠페인 단위로만 본다."""
    chosen, form, now, _today, start, end = _period(request)
    rows = ad_rows(request.user, start, end, campaigns.list_campaigns(request.user, now=now))
    total = {name: sum(getattr(t, name) for _g, _c, t in rows) for name in ("impressions", "clicks", "conversions", "converted_clicks")}
    return present(request, "console/ad_report.html", {
        "form": form, "days": chosen["days"], "metric": chosen["metric"], "start": start, "end": end, "rows": rows, "total": records.ReportTotals(spent=None, **total),
    })
