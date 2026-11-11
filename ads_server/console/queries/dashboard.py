"""대시보드 화면용 조립(계획 §8.1.1: 여러 모듈의 값을 **합치기만** 한다 — 규칙은 각 서비스 `api` 에 있다)."""
from campaigns import api as campaigns
from records import api as records

TOP = 5


def top_campaigns(user, start, end, camps, limit=TOP):
    """기간에 성과가 있는 캠페인을 소진 포인트 → 노출 → 이름 순으로 `limit` 개. `[(CampaignView, ReportTotals)]`. `camps` 는 화면이 읽은 캠페인 목록."""
    totals = records.campaign_totals(user, start, end)
    named = {c.campaign_id: c for c in camps}
    ranked = sorted(((named[cid], t) for cid, t in totals.items() if cid in named), key=lambda pair: (-pair[1].spent, -pair[1].impressions, pair[0].name))
    return ranked[:limit]


def ad_rows(user, start, end, camps):
    """광고별 성과(설계 §10 "광고주 화면 정리" ③): `[(AdGroupEffects, CampaignView, ReportTotals)]`. 끝나지 않은 캠페인의 광고는 반응이 없어도(0) 모두,
    끝난 캠페인의 광고는 기간에 반응이 있을 때만. 노출 → 클릭 많은 순, 같으면 이름 순. 소진 포인트는 없다(`spent=None`). `camps` 는 화면이 읽은 캠페인 목록."""
    totals = records.ad_group_totals(user, start, end)
    named = {c.campaign_id: c for c in camps}
    zero = records.ReportTotals(impressions=0, clicks=0, conversions=0, converted_clicks=0, spent=None)
    rows = [(g, named[g.campaign_id], totals.get(g.ad_group_id, zero)) for g in campaigns.ad_group_effects(user)
            if g.campaign_id in named and (named[g.campaign_id].status != campaigns.CampaignStatus.ENDED or g.ad_group_id in totals)]
    return sorted(rows, key=lambda row: (-row[2].impressions, -row[2].clicks, row[0].name))
