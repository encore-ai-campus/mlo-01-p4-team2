"""Step 3-2d 광고 상세(광고그룹): 구성별 결과, 입찰 이력과 새 입찰(낙관적 잠금 충돌 안내), 이름·구성·켜기/끄기, 이미지 고르기·올리기(6-11).
소재의 제목·주소 수정과 이미지 교체는 소재 상세에서만 한다(시험은 `test_creative_pages`).

완료 조건(계획 3-2d): 입찰은 추가만, 충돌 안내. 동작마다 작은 폼이라 한 칸의 제출이 다른 칸을 덮지 않는다.
World: 구성(하한 100·기준 200·상한 500, 노출 98%), 입찰 300.
"""
import re
from unittest import mock

from django.test import TestCase, tag
from django.urls import reverse

from campaigns import api as campaigns
from masters import api as masters
from tests.support.factories import World, make_slot, png_file
from tests.support.cases import Cases


@tag("security")
class AdGroupPageBase(TestCase):
    def setUp(self):
        self.w = World(ends_in=None)
        self.gid = self.w.group.ad_group_id
        self.crid = self.w.creative.creative_id
        self.client.force_login(self.w.adv)
        self.detail = reverse("ad_group_detail", args=[self.gid])

    def html(self):
        return self.client.get(self.detail).content.decode()

    def group(self):
        return campaigns.get_ad_group(self.w.adv, self.gid)

    def seen(self):
        """화면이 싣는 광고그룹의 마지막 변경 시각."""
        return self.group().updated_at.isoformat()


class DetailTests(Cases, AdGroupPageBase):
    def case_shows_bid_effects_history_slots_and_the_creative(self):
        html = self.html()
        self.assertIn(f'<a href="{reverse("campaign_detail", args=[self.w.campaign.campaign_id])}">캠페인</a> / 광고 상세', html)  # 속한 캠페인으로
        self.assertIn('<p class="now"><strong>300 P</strong>', html)
        self.assertIn('그대로 적용</td><td class="num">300 P</td><td class="num">294 / 3 / 3 P</td>', html)  # 위치별 예상 과금(노출 / 클릭 / 전환, 6-10d)
        self.assertIn("노출 98% · 클릭 1% · 전환 1%", html)  # 고를 수 있는 위치의 비율
        self.assertIn(reverse("creative_image_file", args=[self.crid]), html)
        self.assertRegex(html, r'<span class="url">https://example\.com[^<]*</span>')  # 현재 노출 소재의 이동 주소
        self.assertRegex(html, rf'value="{self.w.slot.slot_id}" checked')
        campaigns.add_bid(self.w.adv, self.gid, 350, 1)
        self.assertRegex(self.html(), r'(?s)입찰 변경 이력</caption>.*?<td>v2 <span class="status active">현재</span></td><td>350 P</td>.*?<td>v1</td><td>300 P</td>')  # 최신부터

    def case_slots_show_their_display_and_frames_use_the_current_creative(self):
        """구성 표시 정보(설계 §10 "구성 표시 정보"): 고를 수 있는 구성마다 권장 비율, 이 광고의 구성마다 틀 — 처음은 지금 소재, 올리려고 고른 이미지로 바뀐다(frames.js)."""
        masters.set_slot_display(self.w.admin, self.w.slot.slot_id, 3, 2, "cover")
        html = self.html()
        self.assertIn('<span class="muted display">권장 비율 3:2(예: 1200×800) · 판을 채우고 넘치는 곳은 잘림</span>', html)
        self.assertIn(f'data-source="{reverse("creative_image_file", args=[self.crid])}" data-input="id_new-image" data-pick=""', html)
        self.assertIn(f'<figure class="frame" data-slot="{self.w.slot.slot_id}" data-fit="cover" data-width="3" data-height="2">', html)
        self.assertIn('id="id_new-image"', html)  # 틀이 듣는 파일 칸
        self.assertIn("console/frames.js", html)

    def case_the_page_reads_slot_policies_once(self):
        """계획 3-4g B1: 위치 정보와 위치별 결과가 같은 읽기를 본다(전에는 슬롯 정책을 두 번 읽었다). 헤더 알림 표시(6-21d)가 따로 한 번 더 읽는다."""
        with mock.patch.object(masters, "get_slots_with_policy", wraps=masters.get_slots_with_policy) as read:
            self.html()
        self.assertEqual(read.call_count, 2)

    def case_user_text_is_escaped(self):
        campaigns.update_ad_group(self.w.adv, self.gid, {"name": "<b>그룹</b>"})
        campaigns.update_creative(self.w.adv, self.crid, {"title": "<script>x</script>"})
        html = self.html()
        self.assertNotIn("<b>그룹</b>", html)
        self.assertNotIn("<script>x</script>", html)
        self.assertIn("&lt;script&gt;x&lt;/script&gt;", html)

    def test_detail(self):
        self.run_cases()


class BidTests(Cases, AdGroupPageBase):
    def case_a_new_bid_is_added_as_a_new_version(self):
        self.assertRedirects(self.client.post(reverse("ad_group_bid", args=[self.gid]), {"amount": 600, "expected_version": 1}), self.detail)
        self.assertEqual([b.amount for b in campaigns.list_bids(self.w.adv, self.gid)], [600, 300])  # 추가만
        html = self.html()
        self.assertRegex(html, r'상한으로 적용</td><td class="num">500 P</td>')
        self.assertRegex(html, r'name="expected_version" value="2"')

    def case_a_concurrent_bid_is_explained_and_nothing_is_overwritten(self):
        campaigns.add_bid(self.w.adv, self.gid, 400, expected_version=1)  # 다른 탭에서 먼저
        r = self.client.post(reverse("ad_group_bid", args=[self.gid]), {"amount": 600, "expected_version": 1})
        self.assertContains(r, "다른 곳에서 먼저 바뀌었습니다(현재 버전 2)", status_code=409)  # 다른 낙관적 잠금 충돌과 같은 안내
        self.assertEqual([b.amount for b in campaigns.list_bids(self.w.adv, self.gid)], [400, 300])

    def case_bad_amount_and_ended_campaign_are_refused_by_the_service(self):
        self.assertEqual(self.client.post(reverse("ad_group_bid", args=[self.gid]), {"amount": 0, "expected_version": 1}).status_code, 400)
        campaigns.end_campaign(self.w.adv, self.w.campaign.campaign_id)
        r = self.client.post(reverse("ad_group_bid", args=[self.gid]), {"amount": 600, "expected_version": 1})
        self.assertIn(r.status_code, (400, 409))
        self.assertEqual(len(campaigns.list_bids(self.w.adv, self.gid)), 1)

    def test_bid(self):
        self.run_cases()


class GroupEditTests(Cases, AdGroupPageBase):
    def case_name_slots_and_toggle_are_separate_and_do_not_touch_each_other(self):
        other_slot = make_slot(self.w.admin, name="둘째 위치")
        self.client.post(reverse("ad_group_slots", args=[self.gid]), {"slot_ids": [str(self.w.slot.slot_id), str(other_slot.slot_id)], "updated_at": self.seen()})
        self.assertEqual(set(self.group().slot_ids), {self.w.slot.slot_id, other_slot.slot_id})
        self.client.post(reverse("ad_group_name", args=[self.gid]), {"name": "새 이름", "updated_at": self.seen()})
        self.client.post(reverse("ad_group_enabled", args=[self.gid]), {"enabled": "0"})
        group = self.group()
        self.assertEqual((group.name, group.enabled, len(group.slot_ids)), ("새 이름", False, 2))  # 앞의 변경이 남아 있다

    def case_slot_rules_are_shown(self):
        click = make_slot(self.w.admin, unit="CLICK", name="클릭 위치")
        self.assertIn(str(click.slot_id), self.html())  # 과금 단위가 없어 비율이 다른 위치도 고를 수 있다(6-10d)
        masters.set_slot_active(self.w.admin, click.slot_id, False)
        self.assertNotIn(str(click.slot_id), self.html())  # 꺼진 위치는 고를 수 없다
        self.assertEqual(self.client.post(reverse("ad_group_slots", args=[self.gid]), {"slot_ids": [str(click.slot_id)], "updated_at": self.seen()}).status_code, 400)
        r = self.client.post(reverse("ad_group_slots", args=[self.gid]), {"updated_at": self.seen()})
        self.assertContains(r, "구성을 하나 이상 고르세요", status_code=400)
        self.assertEqual(self.group().slot_ids, (self.w.slot.slot_id,))

    def case_a_linked_shared_slot_is_shown_as_retired_and_not_offered(self):
        """이미 고른 일괄 구성(TG-S4): 켜져 있으면 꺼진 구성 문구가 아니라 "일괄(사용 중지)"로 보이고, 선택지에 없고, 보내도 400이다.
        끄면(매체가 개인 송출로 바꾼 뒤 운영자가 끔 — Q10) 다른 꺼진 구성처럼 보인다(리뷰 R1). 구성을 다시 저장하면 빠진다."""
        shared = masters.create_slot(self.w.admin, self.w.slot.media_id, "옥상 전광판", 100, 200, 500, "초기", audience="shared", impression_share=98, click_share=1, conversion_share=1)
        both = {self.w.slot.slot_id, shared.slot_id}
        campaigns.update_ad_group(self.w.adv, self.gid, {"slot_ids": list(both)})  # 서비스는 그대로 받는다(화면만 막음)
        html = self.html()
        self.assertIn("일괄(사용 중지) 구성: ", html)
        self.assertIn("옥상 전광판", html)
        self.assertIn("<td>일괄(사용 중지)</td>", html)  # 구성별 결과 표의 유형 칸(문서 27 D9)
        self.assertNotIn(f'name="slot_ids" value="{shared.slot_id}"', html)
        self.assertNotIn("지금 꺼진 구성", html)
        sent = self.client.post(reverse("ad_group_slots", args=[self.gid]), {"slot_ids": [str(s) for s in both], "updated_at": self.seen()})
        self.assertEqual(sent.status_code, 400)  # 화면에서 일괄을 직접 보내도(리뷰 R2)
        self.assertEqual(set(self.group().slot_ids), both)
        masters.set_slot_active(self.w.admin, shared.slot_id, False)
        html = self.html()
        self.assertNotIn("일괄(사용 중지) 구성: ", html)  # 꺼진 일괄은 "그대로 나간다"고 말하지 않는다
        self.assertRegex(html, r"지금 꺼진 구성: [^<]*옥상 전광판")
        self.client.post(reverse("ad_group_slots", args=[self.gid]), {"slot_ids": [str(self.w.slot.slot_id)], "updated_at": self.seen()})
        self.assertEqual(self.group().slot_ids, (self.w.slot.slot_id,))

    def case_a_linked_shared_slot_whose_media_is_off_is_shown_as_off(self):
        """일괄 구성의 매체를 끄는 것 = 구성을 끄는 것(TG-S4 리뷰 M11): 켜짐은 `list_slots(active_only=True)` 와 같은 기준(구성과 매체가 모두 켜짐)이라,
        구성은 켜져 있어도 매체가 꺼지면 "일괄(사용 중지)"(그대로 나간다)가 아니라 꺼진 구성으로 보인다. 다른 매체에 두어 개인 구성은 켜진 채로 둔다."""
        other_media = masters.create_media(self.w.admin, "옥외 매체")
        shared = masters.create_slot(self.w.admin, other_media.media_id, "옥상 전광판", 100, 200, 500, "초기", audience="shared", impression_share=98, click_share=1, conversion_share=1)
        campaigns.update_ad_group(self.w.adv, self.gid, {"slot_ids": [self.w.slot.slot_id, shared.slot_id]})
        self.assertIn("일괄(사용 중지) 구성: ", self.html())
        masters.set_media_active(self.w.admin, other_media.media_id, False)
        html = self.html()
        self.assertNotIn("일괄(사용 중지) 구성: ", html)
        self.assertRegex(html, r"지금 꺼진 구성: [^<]*옥상 전광판")

    def case_an_inactive_linked_slot_is_explained(self):
        masters.set_slot_active(self.w.admin, self.w.slot.slot_id, False)
        html = self.html()
        self.assertIn("지금 꺼진 구성", html)
        self.assertIn("꺼진 구성 — 노출 안 됨", html)

    def test_group_edit(self):
        self.run_cases()


@tag("security")
class SegmentSectionTests(Cases, AdGroupPageBase):
    """TG-S2 광고 상세의 "대상 그룹": 고른 구성의 매체에 속한 살아 있는 그룹 + 이미 연결된 그룹(보관 표시)을 고르고, 경고 둘을 보인다."""

    def setUp(self):
        super().setUp()
        self.mlo = masters.create_segment(self.w.admin, self.w.slot.media_id, "mlo", "<b>MLO</b>")
        self.skn = masters.create_segment(self.w.admin, self.w.slot.media_id, "skn", "SKN")

    def save(self, *segment_ids):
        return self.client.post(reverse("ad_group_segments", args=[self.gid]), {"segment_ids": [str(s) for s in segment_ids], "updated_at": self.seen()})

    def case_choices_are_live_groups_of_the_chosen_slot_media_and_saving_links_them(self):
        elsewhere = masters.create_segment(self.w.admin, make_slot(self.w.admin).media_id, "aio", "AIO")
        html = self.html()
        self.assertIn(f'name="segment_ids" value="{self.mlo.segment_id}"', html)
        self.assertNotIn(str(elsewhere.segment_id), html)  # 고른 구성에 없는 매체의 그룹
        self.assertNotIn("<b>MLO</b>", html)  # 이름은 이스케이프된다
        r = self.save(self.mlo.segment_id)
        self.assertRedirects(r, self.detail, fetch_redirect_response=False)  # 안내 메시지는 다음 화면에서 읽는다
        self.assertEqual(self.group().segment_ids, (self.mlo.segment_id,))
        self.assertContains(self.client.get(self.detail), "대상 그룹을 바꿨습니다.")
        self.assertRedirects(self.save(), self.detail)  # 모두 풀면 전체가 대상
        self.assertEqual(self.group().segment_ids, ())

    def case_an_archived_linked_group_is_shown_and_warned_and_cannot_be_chosen_anew(self):
        self.save(self.mlo.segment_id)
        masters.update_segment(self.w.admin, self.mlo.segment_id, 1, is_active=False)
        html = self.html()
        self.assertIn("보관됨", html)
        self.assertIn("고른 대상 그룹이 모두 보관됐습니다", html)
        self.assertRedirects(self.save(self.mlo.segment_id, self.skn.segment_id), self.detail)  # 연결된 보관 그룹은 남는다
        self.assertNotIn("모두 보관됐습니다", self.html())
        self.save(self.skn.segment_id)
        r = self.save(self.mlo.segment_id)  # 뺀 뒤 다시 고르기 — 보관돼 고를 수 없다(화면에 없는 값을 보냄)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.group().segment_ids, (self.skn.segment_id,))

    def case_a_slot_media_without_any_chosen_group_is_warned(self):
        other = make_slot(self.w.admin, name="다른 매체 위치")
        self.client.post(reverse("ad_group_slots", args=[self.gid]), {"slot_ids": [str(self.w.slot.slot_id), str(other.slot_id)], "updated_at": self.seen()})
        self.assertNotIn("대상 그룹이 없는 매체", self.html())  # 대상 그룹을 고르지 않았으면 전체가 대상 — 경고 없음
        self.save(self.mlo.segment_id)
        warning = f"대상 그룹이 없는 매체: {masters.get_slot(other.slot_id).media_name}"
        self.assertIn(warning, self.html())
        aio = masters.create_segment(self.w.admin, other.media_id, "aio", "AIO")
        self.save(self.mlo.segment_id, aio.segment_id)
        self.assertNotIn("대상 그룹이 없는 매체", self.html())
        masters.update_segment(self.w.admin, aio.segment_id, 1, is_active=False)  # 그 매체의 연결 그룹이 보관되면 다시 경고(모두 보관은 아니라 다른 경고는 없다 — 리뷰 R1)
        html = self.html()
        self.assertIn(warning, html)
        self.assertNotIn("모두 보관됐습니다", html)

    def case_a_stale_form_is_refused(self):
        seen = self.seen()
        self.save(self.mlo.segment_id)
        r = self.client.post(reverse("ad_group_segments", args=[self.gid]), {"segment_ids": [str(self.skn.segment_id)], "updated_at": seen})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(self.group().segment_ids, (self.mlo.segment_id,))

    def test_segment_section(self):
        self.run_cases()


class ImageSectionTests(Cases, AdGroupPageBase):
    def case_the_image_section_only_selects_and_the_upload_section_only_uploads(self):
        """6-11(사용자 요청): 광고 상세의 "이미지"는 내 이미지(보관하지 않은 것 전부) 중 하나를 고르기만 하고, "이미지 업로드"는 올리기만 한다.
        제목·주소 수정과 이미지 교체는 소재 상세에만 있다."""
        mine = campaigns.create_creative(self.w.adv, None, "목록의 이미지", "https://example.com/2", png_file((9, 9, 9)))  # 아직 이 광고에 연결되지 않음
        gone = campaigns.create_creative(self.w.adv, None, "지운 이미지", "https://example.com/3", png_file((8, 8, 8)))
        campaigns.archive_creative(self.w.adv, gone.creative_id)
        html = self.html()
        image, upload = html.split('aria-label="이미지"', 1)[1].split('aria-label="이미지 업로드"', 1)
        self.assertNotIn("소재 더하기", html)
        self.assertEqual(re.findall(r'name="creative_id" value="([^"]+)"', image), [str(mine.creative_id)])  # 고른 것은 버튼이 없고, 지운 것은 보이지 않는다
        self.assertIn("선택됨", image)
        self.assertNotIn("나가는 중", image)
        self.assertIn('aria-label="이 이미지로 바꾸기: 목록의 이미지"', image)  # 이미지마다 버튼 이름이 다르다(R8)
        self.assertNotIn("지운 이미지", image)
        self.assertNotIn(f'action="{reverse("creative_edit", args=[self.crid])}"', html)  # 이 화면에서는 고치지 않는다
        self.assertNotIn(f'action="{reverse("creative_image", args=[self.crid])}"', html)
        self.assertEqual(re.findall(r'<input type="file"[^>]*name="([^"]+)"', html), ["new-image"])  # 파일 칸은 올리기 하나
        self.assertNotIn('name="creative_id"', upload)

        self.assertRedirects(self.client.post(reverse("ad_group_creative_select", args=[self.gid]), {"creative_id": str(mine.creative_id)}), self.detail)
        self.assertEqual(self.group().creative_id, mine.creative_id)  # 연결되지 않았던 것도 고르면 연결과 선택이 함께
        self.assertIn(mine.creative_id, {c.creative_id for c in campaigns.list_creatives(self.w.adv, self.gid)})

        r = self.client.post(reverse("ad_group_creative_upload", args=[self.gid]),
                             {"new-title": "새 이미지", "new-redirect_url": "https://example.com/new", "new-image": png_file((7, 7, 7))})
        self.assertRedirects(r, self.detail)
        uploaded = next(o.creative for o in campaigns.list_owned_creatives(self.w.adv) if o.creative.title == "새 이미지")
        self.assertEqual(self.group().creative_id, mine.creative_id)  # 올리기만 한다 — 선택은 그대로
        self.assertNotIn(uploaded.creative_id, {c.creative_id for c in campaigns.list_creatives(self.w.adv, self.gid)})  # 연결도 하지 않는다
        self.assertIn(f'value="{uploaded.creative_id}"', self.html().split('aria-label="이미지 업로드"', 1)[0])  # 위 목록에서 고를 수 있다


    def case_a_refused_choice_says_why_and_keeps_the_image(self):
        """R13(6-13d): 고르기가 거절되면(끝난 캠페인) 이유를 알리고, 나가는 이미지는 그대로다."""
        mine = campaigns.create_creative(self.w.adv, None, "목록의 이미지", "https://example.com/2", png_file((9, 9, 9)))
        campaigns.end_campaign(self.w.adv, self.w.campaign.campaign_id)
        r = self.client.post(reverse("ad_group_creative_select", args=[self.gid]), {"creative_id": str(mine.creative_id)}, follow=True)
        self.assertRedirects(r, self.detail)
        self.assertContains(r, "종료된 캠페인은 수정할 수 없습니다")
        self.assertEqual(self.group().creative_id, self.crid)

    def case_each_slot_can_show_its_own_image(self):
        """구성별 노출 소재(설계 §10 "구성별 노출 소재" ⑤): 구성마다 '기본 소재' 또는 내 이미지(지운 것 제외)를 고르고, 틀은 구성마다 그 구성의 소재로 그린다."""
        other = make_slot(self.w.admin)
        campaigns.update_ad_group(self.w.adv, self.gid, {"slot_ids": [self.w.slot.slot_id, other.slot_id]})
        mine = campaigns.create_creative(self.w.adv, None, "가로 이미지", "https://example.com/2", png_file((9, 9, 9)))
        gone = campaigns.create_creative(self.w.adv, None, "지운 이미지", "https://example.com/3", png_file((8, 8, 8)))
        campaigns.archive_creative(self.w.adv, gone.creative_id)
        post = lambda slot_id, creative_id: self.client.post(reverse("ad_group_slot_creative", args=[self.gid]), {"slot_id": slot_id, "creative_id": creative_id}, follow=True)  # noqa: E731
        section = lambda html: html.split('aria-labelledby="slot-creatives-title"', 1)[1].split("</section>", 1)[0]  # noqa: E731

        before = section(self.html())
        self.assertEqual(sorted(re.findall(r'name="slot_id" value="([^"]+)"', before)), sorted([str(self.w.slot.slot_id), str(other.slot_id)]))
        self.assertEqual(re.findall(r'<option value="([^"]*)"', before), ["", str(self.crid), str(mine.creative_id)] * 2)  # 지운 이미지는 없다
        self.assertEqual(before.count('<option value="" selected>기본 소재</option>'), 2)

        r = post(str(other.slot_id), str(mine.creative_id))
        self.assertRedirects(r, self.detail)
        self.assertContains(r, "이 구성에 나갈 이미지를 바꿨습니다.")
        self.assertEqual((self.group().creative_id, self.group().slot_creative_ids), (self.crid, ((other.slot_id, mine.creative_id),)))
        html = r.content.decode()
        self.assertIn(f'<option value="{mine.creative_id}" selected>가로 이미지</option>', section(html))
        self.assertIn(f'<figure class="frame" data-slot="{other.slot_id}" data-src="{reverse("creative_image_file", args=[mine.creative_id])}">', html)
        self.assertIn(f'<figure class="frame" data-slot="{self.w.slot.slot_id}">', html)  # 지정하지 않은 구성은 처음 이미지(data-source = 기본 소재)

        r = post(str(World(ends_in=None).slot.slot_id), str(mine.creative_id))
        self.assertContains(r, "이 광고의 구성이 아닙니다.")
        self.assertRedirects(post(str(other.slot_id), ""), self.detail)  # 빈 값 = 기본 소재로
        self.assertEqual(self.group().slot_creative_ids, ())

    def case_a_creative_not_in_use_can_be_deleted_from_the_choice_list(self):
        """설계 §10 "광고주 화면 정리" ①: '다른 소재 선택'에서도 삭제(보관)한다 — 쓰지 않는 소재만 확인 창과 함께 버튼, 쓰는 소재는 '사용 중'. 끝나면 광고 상세로."""
        free = campaigns.create_creative(self.w.adv, None, "안 쓰는 이미지", "https://example.com/2", png_file((9, 9, 9)))
        busy = campaigns.create_creative(self.w.adv, None, "다른 광고의 이미지", "https://example.com/3", png_file((8, 8, 8)))
        other_ad = campaigns.create_ad_group(self.w.adv, self.w.campaign.campaign_id, "다른 광고", [self.w.slot.slot_id])
        campaigns.link_creative(self.w.adv, other_ad.ad_group_id, busy.creative_id, select=True)
        archive = lambda c: reverse("ad_group_creative_archive", args=[self.gid, c.creative_id])  # noqa: E731
        html = self.html()
        self.assertIn("console/confirm.js", html)  # 확인 창(없으면 누르자마자 삭제)
        image = html.split('aria-label="이미지"', 1)[1].split('aria-labelledby="slot-creatives-title"', 1)[0]
        self.assertIn(f'action="{archive(free)}" data-confirm="‘안 쓰는 이미지’ 소재를 삭제할까요?"', image)
        self.assertNotIn(archive(busy), image)
        self.assertIn("사용 중", image)
        r = self.client.post(archive(free), follow=True)
        self.assertRedirects(r, self.detail)
        self.assertContains(r, "소재를 삭제했습니다.")
        self.assertTrue(campaigns.get_creative(self.w.adv, free.creative_id).archived)
        r = self.client.post(archive(busy), follow=True)  # 화면에 없는 버튼을 만들어 보내도 서비스가 막는다
        self.assertRedirects(r, self.detail)
        self.assertContains(r, "삭제할 수 없습니다")
        self.assertFalse(campaigns.get_creative(self.w.adv, busy.creative_id).archived)

    def case_the_title_is_called_image_name_on_the_screens(self):
        """설계 §10 "광고주 화면 정리" ②: 소재의 `title` 은 화면에서 "이미지 이름"이다 — 입력 칸·소재 상세 제목줄·서비스 오류 문구."""
        self.assertIn('<label for="id_new-title">이미지 이름</label>', self.html())
        self.assertIn("<h2>이미지 이름·이동 주소</h2>", self.client.get(reverse("creative_detail", args=[self.crid])).content.decode())
        r = self.client.post(reverse("ad_group_creative_upload", args=[self.gid]),
                             {"new-title": "   ", "new-redirect_url": "https://example.com/x", "new-image": png_file((6, 6, 6))})
        self.assertContains(r, "이미지 이름은(는) 1~200자의 보이는 문자여야 합니다.", status_code=400)

    def case_an_archived_image_shows_only_where_it_is_still_chosen(self):
        """끝난 캠페인에서는 구성별로 쓰던 이미지도 지울 수 있다 — 화면은 그 이미지를 그 구성의 선택으로만 보이고, 다른 구성에서는 고를 수 없다."""
        other = make_slot(self.w.admin)
        campaigns.update_ad_group(self.w.adv, self.gid, {"slot_ids": [self.w.slot.slot_id, other.slot_id]})
        mine = campaigns.create_creative(self.w.adv, None, "가로 이미지", "https://example.com/2", png_file((9, 9, 9)))
        campaigns.select_slot_creative(self.w.adv, self.gid, other.slot_id, mine.creative_id)
        campaigns.end_campaign(self.w.adv, self.w.campaign.campaign_id)
        campaigns.archive_creative(self.w.adv, mine.creative_id)
        section = self.html().split('aria-labelledby="slot-creatives-title"', 1)[1].split("</section>", 1)[0]
        rows = dict(re.findall(r'name="slot_id" value="([^"]+)">\s*<select[^>]*>(.*?)</select>', section, re.S))
        self.assertEqual(re.findall(r'<option value="([^"]*)"', rows[str(other.slot_id)]), ["", str(mine.creative_id), str(self.crid)])  # 지운 채 지정된 것은 맨 앞(기본 소재와 같다)
        self.assertIn(f'<option value="{mine.creative_id}" selected>가로 이미지</option>', rows[str(other.slot_id)])
        self.assertEqual(re.findall(r'<option value="([^"]*)"', rows[str(self.w.slot.slot_id)]), ["", str(self.crid)])  # 지운 이미지는 새로 고를 수 없다
        image = self.html().split('aria-label="이미지"', 1)[1].split('aria-labelledby="slot-creatives-title"', 1)[0]
        self.assertEqual((image.count('<span class="muted">삭제한 이미지</span>'), image.count("사용 중")), (1, 0))  # '다른 소재 선택'에서도 사실대로(CUX-1 리뷰 N1)

    def test_image_section(self):
        self.run_cases()


class IsolationTests(AdGroupPageBase):
    def test_other_advertisers_group_and_creative_are_404_everywhere(self):
        other = World(ends_in=None)
        gid, crid = other.group.ad_group_id, other.creative.creative_id
        self.assertEqual(self.client.get(reverse("ad_group_detail", args=[gid])).status_code, 404)
        posts = (("ad_group_bid", gid, {"amount": 1, "expected_version": 1}), ("ad_group_name", gid, {"name": "x"}),
                 ("ad_group_slots", gid, {"slot_ids": [str(other.slot.slot_id)]}), ("ad_group_enabled", gid, {"enabled": "0"}),
                 ("creative_edit", crid, {}), ("creative_image", crid, {"image": png_file()}),
                 ("ad_group_creative_upload", gid, {"new-title": "남의 화면", "new-redirect_url": "https://example.com/x", "new-image": png_file()}),
                 ("ad_group_slot_creative", gid, {"slot_id": str(other.slot.slot_id), "creative_id": ""}))
        for name, target, data in posts:
            self.assertEqual(self.client.post(reverse(name, args=[target]), data).status_code, 404, name)
        self.assertEqual(self.client.get(reverse("creative_image_file", args=[crid])).status_code, 404)
        group = campaigns.get_ad_group(other.adv, gid)
        self.assertEqual((group.name, group.enabled, group.current_bid.amount), ("그룹", True, 300))
        self.assertEqual(campaigns.get_creative(other.adv, crid).title, "제목")
        self.assertEqual([o.creative.title for o in campaigns.list_owned_creatives(self.w.adv)], ["제목"])  # 남의 광고 화면으로는 올라가지 않는다
        self.assertEqual(self.client.post(reverse("ad_group_creative_select", args=[gid]), {"creative_id": str(self.crid)}).status_code, 404)  # 남의 광고에 내 이미지를 고를 수 없다
        self.assertEqual(self.client.post(reverse("ad_group_creative_select", args=[self.gid]), {"creative_id": str(crid)}).status_code, 404)  # 내 광고에 남의 이미지도
        self.assertEqual(self.client.post(reverse("ad_group_slot_creative", args=[gid]), {"slot_id": str(other.slot.slot_id), "creative_id": str(self.crid)}).status_code, 404)
        self.assertEqual(self.client.post(reverse("ad_group_slot_creative", args=[self.gid]), {"slot_id": str(self.w.slot.slot_id), "creative_id": str(crid)}).status_code, 404)  # 구성별로도
        self.assertEqual(campaigns.get_ad_group(other.adv, gid).creative_id, other.creative.creative_id)
        self.assertEqual(self.group().creative_id, self.crid)
        self.assertEqual((self.group().slot_creative_ids, campaigns.get_ad_group(other.adv, gid).slot_creative_ids), ((), ()))
        self.assertEqual(self.client.post(reverse("ad_group_creative_archive", args=[gid, self.crid])).status_code, 404)  # 남의 광고 경로로 내 소재를
        self.assertEqual(self.client.post(reverse("ad_group_creative_archive", args=[self.gid, crid])).status_code, 404)  # 내 광고 경로로 남의 소재를
        self.assertEqual((self.group().creative_id, campaigns.get_creative(other.adv, crid).archived), (self.crid, False))


class StaleSameFieldTests(Cases, AdGroupPageBase):
    """외부 검토(41d2288) P3: 작은 폼은 다른 칸을 덮지 않지만, 열어 둔 폼이 **같은 칸**의 최신 변경을 덮었다(S1 → S1+S2 뒤 옛 폼의 S1 이 S2 를 지움).
    이름·구성도 화면이 본 변경 시각과 잠금 안에서 대조한다(이미지 교체는 소재 상세 — `test_creative_pages`)."""

    def case_a_stale_slots_form_does_not_remove_a_slot_added_elsewhere(self):
        seen = self.seen()  # 옛 탭이 본 시각
        second = make_slot(self.w.admin, name="둘째 위치")
        both = [str(self.w.slot.slot_id), str(second.slot_id)]
        self.assertRedirects(self.client.post(reverse("ad_group_slots", args=[self.gid]), {"slot_ids": both, "updated_at": seen}), self.detail)
        r = self.client.post(reverse("ad_group_slots", args=[self.gid]), {"slot_ids": [str(self.w.slot.slot_id)], "updated_at": seen})
        self.assertContains(r, "다른 곳에서 먼저 바뀌었습니다", status_code=409)
        self.assertEqual(set(self.group().slot_ids), {self.w.slot.slot_id, second.slot_id})

    def case_a_stale_name_form_is_refused(self):
        seen = self.seen()
        campaigns.update_ad_group(self.w.adv, self.gid, {"name": "다른 탭"})
        self.assertContains(self.client.post(reverse("ad_group_name", args=[self.gid]), {"name": "옛 탭", "updated_at": seen}),
                            "다른 곳에서 먼저 바뀌었습니다", status_code=409)
        self.assertEqual(self.client.post(reverse("ad_group_name", args=[self.gid]), {"name": "", "updated_at": self.seen()}).status_code, 400)  # 폼이 거른 입력
        self.assertEqual(self.group().name, "다른 탭")

    def test_stale_same_field(self):
        self.run_cases()
