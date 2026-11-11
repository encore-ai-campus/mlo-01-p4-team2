from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone
from tests.factories import create_profile
from .models import Post, PostReadLog


class NoticeNotificationTests(TestCase):
    def setUp(self):
        self.reader = get_user_model().objects.create_user(username='notice-reader')
        self.other = get_user_model().objects.create_user(username='notice-other')
        self.admin = get_user_model().objects.create_user(username='notice-admin', is_staff=True)
        for user in (self.reader, self.other, self.admin):
            create_profile(user=user, nickname=user.username)
        self.client.force_login(self.reader)
        self.url = reverse('community:notice-notifications')

    def notice(self, **extra):
        return Post.objects.create(author=self.admin, category='notice', title='캠퍼스 공지', body='공지 본문', **extra)

    def read(self, post):
        return self.client.get(reverse('community:post-detail', args=[post.id]))

    def count(self):
        return self.client.get(self.url, {'summary':'1'}).json()['unreadCount']

    def test_requires_login_and_completed_profile_and_get_only(self):
        self.assertEqual(Client().get(self.url).status_code, 401)
        self.assertEqual(self.client.post(self.url).status_code, 405)
        self.reader.campus_profile.setup_complete = False
        self.reader.campus_profile.save(update_fields=['setup_complete'])
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_listing_does_not_mark_notices_read(self):
        self.notice(); self.notice()
        response = self.client.get(self.url).json()
        self.assertEqual(response['unreadCount'], 2)
        self.assertTrue(all(not row['isRead'] for row in response['notices']))
        self.assertFalse(PostReadLog.objects.exists())
        self.assertEqual(self.client.get(self.url, {'summary':'1'}).json(), {'unreadCount':2})

    def test_existing_board_reads_decrease_count_once_and_remain_read_after_login(self):
        first = self.notice(); self.notice()
        self.assertEqual(self.count(), 2)
        self.assertEqual(self.read(first).status_code, 200)
        self.assertEqual(self.count(), 1)
        self.read(first)
        self.assertEqual(self.count(), 1)
        self.client.force_login(self.reader)
        self.assertEqual(self.count(), 1)
        rows = self.client.get(self.url).json()['notices']
        self.assertTrue(next(row for row in rows if row['id']==first.id)['isRead'])

    def test_read_state_belongs_to_each_account(self):
        first = self.notice(); self.read(first)
        self.assertEqual(self.count(), 0)
        self.client.force_login(self.other)
        self.assertEqual(self.count(), 1)

    def test_deleted_and_non_notice_posts_are_not_counted(self):
        self.notice(); removed = self.notice()
        Post.objects.filter(pk=removed.pk).update(deleted_at=timezone.now())
        Post.objects.create(author=self.admin, category='free', title='자유글', body='본문')
        self.assertEqual(self.count(), 1)
        self.assertEqual(len(self.client.get(self.url).json()['notices']), 1)

    def test_pagination_keeps_global_unread_count(self):
        for _ in range(10): self.notice()
        first = self.client.get(self.url).json()
        second = self.client.get(self.url, {'page':2}).json()
        self.assertEqual(len(first['notices']), 8)
        self.assertEqual(len(second['notices']), 2)
        self.assertEqual(second['unreadCount'], 10)
        self.assertEqual(self.client.get(self.url, {'page':0}).status_code, 400)

    def test_editing_a_read_notice_does_not_create_an_extra_notification(self):
        first = self.notice(); self.read(first)
        Post.objects.filter(pk=first.pk).update(title='수정 공지', revision=2)
        self.assertEqual(self.count(), 0)

    def test_removing_an_unread_notice_decreases_count_and_deleted_detail_is_not_read(self):
        first = self.notice(); second = self.notice()
        self.read(first)
        Post.objects.filter(pk=second.pk).update(deleted_at=timezone.now())
        self.assertEqual(self.count(), 0)
        before = PostReadLog.objects.count()
        self.assertEqual(self.read(second).status_code, 404)
        self.assertEqual(PostReadLog.objects.count(), before)
