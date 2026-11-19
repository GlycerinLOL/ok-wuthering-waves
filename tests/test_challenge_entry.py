"""刷4C 进本：等级页「单人挑战」→ 队伍页「开启挑战」→ 读条 → 副本，点击丢了要补点(不起游戏)。

2026-11-19 实机：周本点完「单人挑战」停在等级页，旧代码只点一次、干等 120 秒卡住。

在 coco-ww 根目录跑:  python\\python.exe -X utf8 tests\\test_challenge_entry.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ok import Box
import src.task.BaseWWTask as base_ww_module
from src.task.FarmEchoTask import FarmEchoTask

BUTTON_TEXT = {'level': '单人挑战', 'team': '开启挑战'}
NEXT_PAGE = {'level': 'team', 'team': 'loading'}


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def time(self):
        return self.now


class FakeEntryTask(FarmEchoTask):
    """不走 __init__；画面是个小状态机，drop 里的第 n 次点击(从 1 数)被游戏吞掉。"""
    width = 2560
    height = 1440

    @classmethod
    def make(cls, clock, page='level', drop=(), dialog_after_solo=False, loading_frames=3):
        task = object.__new__(cls)
        task.clock = clock
        task.page = page
        task.drop = set(drop)
        task.dialog = False
        task.dialog_after_solo = dialog_after_solo
        task.loading_left = loading_frames
        task.clicks = []
        task.logs = []
        return task

    def next_frame(self):
        if self.page == 'loading':
            self.loading_left -= 1
            if self.loading_left <= 0:
                self.page = 'world'

    def in_team_and_world(self):
        return self.page == 'world'

    def click_skip_dialog_confirm(self):
        if not self.dialog:
            return False
        self.dialog = False
        self.page = 'team'
        self.clicks.append('dialog')
        return True

    def ocr(self, *args, **kwargs):
        if self.page in BUTTON_TEXT and not self.dialog:
            return [Box(2000, 1300, 200, 50, name=BUTTON_TEXT[self.page])]
        return []

    def find_one(self, *args, **kwargs):
        return None

    def click_box(self, box, *args, after_sleep=0, **kwargs):
        self.clicks.append(box.name)
        if len(self.clicks) not in self.drop and self.page in NEXT_PAGE:
            if self.page == 'level' and self.dialog_after_solo:
                self.dialog = True
            else:
                self.page = NEXT_PAGE[self.page]
        self.sleep(after_sleep)

    def sleep(self, seconds=0, *args, **kwargs):
        self.clock.now += max(seconds, 0.05)  # 每轮 OCR 也要花时间

    def tr(self, message):
        return message

    def log_info(self, message, *args, **kwargs):
        self.logs.append(message)


class ChallengeEntryTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.old_time = base_ww_module.time
        base_ww_module.time = self.clock

    def tearDown(self):
        base_ww_module.time = self.old_time

    def test_normal_two_clicks(self):
        task = FakeEntryTask.make(self.clock)
        self.assertTrue(task.click_challenge_until_in_world())
        self.assertEqual(task.clicks, ['单人挑战', '开启挑战'])
        self.assertFalse(any('click again' in m for m in task.logs))

    def test_lost_solo_click_is_retried(self):
        # 实机卡住那次：单人挑战点了没反应
        task = FakeEntryTask.make(self.clock, drop={1})
        self.assertTrue(task.click_challenge_until_in_world())
        self.assertEqual(task.clicks, ['单人挑战', '单人挑战', '开启挑战'])
        self.assertTrue(any('click again' in m for m in task.logs))

    def test_lost_start_click_is_retried(self):
        task = FakeEntryTask.make(self.clock, drop={2, 3})
        self.assertTrue(task.click_challenge_until_in_world())
        self.assertEqual(task.clicks, ['单人挑战', '开启挑战', '开启挑战', '开启挑战'])

    def test_no_retry_during_loading(self):
        # 读条慢(10 秒)也不该乱点
        task = FakeEntryTask.make(self.clock, loading_frames=40)
        self.assertTrue(task.click_challenge_until_in_world())
        self.assertEqual(task.clicks, ['单人挑战', '开启挑战'])

    def test_crystal_dialog_after_solo(self):
        task = FakeEntryTask.make(self.clock, dialog_after_solo=True)
        self.assertTrue(task.click_challenge_until_in_world())
        self.assertEqual(task.clicks, ['单人挑战', 'dialog', '开启挑战'])

    def test_direct_challenge_starts_on_team_page(self):
        task = FakeEntryTask.make(self.clock, page='team')
        self.assertTrue(task.click_challenge_until_in_world())
        self.assertEqual(task.clicks, ['开启挑战'])

    def test_gives_up_after_timeout(self):
        task = FakeEntryTask.make(self.clock, drop=set(range(1, 1000)))
        start = self.clock.now
        with self.assertRaises(Exception):
            task.click_challenge_until_in_world(time_out=30)
        self.assertLessEqual(self.clock.now - start, 32)
        self.assertTrue(5 <= len(task.clicks) <= 8, task.clicks)  # 约 4 秒补点一次，不是狂点


if __name__ == '__main__':
    unittest.main()
