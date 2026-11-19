"""刷4C「画面里有声骸就算打完」的判定(不起游戏、不起界面)。

在 coco-ww 根目录跑:  python\\python.exe -X utf8 tests\\test_farm_echo_combat_end.py
"""
import os
import sys
import unittest

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ok import Box, color_range_to_bound
from src.combat.CombatCheck import boss_health_color
from src.task.FarmEchoTask import FarmEchoTask


class FakeScene:
    def __init__(self):
        self.combat = None

    def in_combat(self):
        return None

    def set_in_combat(self):
        self.combat = True
        return True

    def set_not_in_combat(self):
        self.combat = False
        return False

    def reset_abyss_start(self):
        pass


def make_task(target=False, health=False, boss_bar=False, echo=False, retarget=False):
    """不走 __init__ (那要执行器和截图), 只摆出判定用得到的属性。已经在战斗里。"""
    task = object.__new__(FarmEchoTask)
    task.__dict__.update(
        config={'Auto Target': True}, scene=FakeScene(), _in_combat=True, _in_liberation=False,
        _abyss_force_combat_until=0, _abyss_combat_active=False, _f_grace_since=-1.0, target_enemy_time_out=3,
        target=target, health=health, boss_bar=boss_bar, echo=echo, retarget=retarget, logs=[],
    )
    task.combat_end_condition = lambda: task.echo
    task.find_target = lambda double_check=False: task.target
    task.check_health_bar = lambda: task.health or task.boss_bar
    task.boss_still_alive = lambda: task.boss_bar
    task.target_enemy = lambda *a, **k: task.retarget
    task.find_one = lambda *a, **k: None
    task.check_f_break = lambda: None
    task.get_current_char = lambda *a, **k: None
    task.on_combat_check = lambda: True
    task.should_check_monthly_card = lambda: False
    task.log_info = lambda message, *a, **k: task.logs.append(message)
    return task


class EndConditionTest(unittest.TestCase):
    def test_echo_needs_a_second_look(self):
        task = make_task(echo=True)
        self.assertTrue(task.in_combat(), '第一次看到声骸先不收手')
        task._end_seen_since -= 1
        self.assertFalse(task.in_combat(), '隔一次判定还在才算打完')
        self.assertEqual(task.out_of_combat_reason, 'end condition reached')
        self.assertEqual(task._end_seen_since, 0.0)

    def test_one_frame_echo_is_forgotten(self):
        task = make_task(echo=True, retarget=True)
        task.in_combat()
        task.echo = False
        self.assertTrue(task.in_combat(), '误报的那一帧过去了, 照常找目标')
        self.assertEqual(task._end_seen_since, 0.0)

    def test_target_back_cancels_confirm(self):
        task = make_task(echo=True)
        task.in_combat()
        task.target = True
        self.assertTrue(task.in_combat())
        self.assertEqual(task._end_seen_since, 0.0)

    def test_echo_with_boss_bar_keeps_fighting(self):
        task = make_task(echo=True, boss_bar=True, retarget=True)
        task._end_seen_since = 1.0  # 就算之前看到过, Boss 还有血也不收手
        self.assertTrue(task.in_combat())
        self.assertTrue(task._in_combat)


class FrameTask(FarmEchoTask):
    frame = None  # 盖掉基类从执行器取帧的属性


# 实机 2K 截图里 Boss 血条那一块(约 47% 血), 贴回黑屏原位; 1K 用整帧缩小模拟
BAR_IMAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'images', 'boss_bar_2560x1440_780_30.png')
BAR_X, BAR_Y = 780, 30
# 2K 下血条从 906 到 1666, 行 75~87; 血色从左端尖头开始
BAR_X0, BAR_X1, BAR_Y0, BAR_Y1 = 906, 1666, 75, 88
EMPTY_SLOT = (10, 14, 22)


def boss_frame(hp_percent=None, size=(2560, 1440)):
    """hp_percent=None 用原图的血量; 给数就把血条右边涂成空槽, 只留左端 hp_percent%。"""
    frame = np.zeros((1440, 2560, 3), dtype=np.uint8)
    bar = cv2.imread(BAR_IMAGE)
    frame[BAR_Y:BAR_Y + bar.shape[0], BAR_X:BAR_X + bar.shape[1]] = bar
    if hp_percent is not None:
        keep = BAR_X0 + int((BAR_X1 - BAR_X0) * hp_percent / 100)
        region = frame[BAR_Y0:BAR_Y1, keep:BAR_X1]
        lower, upper = color_range_to_bound(boss_health_color)
        region[cv2.inRange(region, lower, upper) > 0] = EMPTY_SLOT
    if size != (2560, 1440):
        frame = cv2.resize(frame, size, interpolation=cv2.INTER_AREA)
    return frame


def split_fill_frame():
    """被特效切成两段的血条(合成), 2K。"""
    frame = boss_frame(hp_percent=30)
    frame[BAR_Y0:BAR_Y1, BAR_X0 + 60:BAR_X0 + 66] = EMPTY_SLOT
    return frame


def make_frame_task(frame, boss_icon=False):
    task = object.__new__(FrameTask)
    task.__dict__.update(_in_combat=True, boss_health_box=None, boss_health=None)
    task.frame = frame
    h, w = frame.shape[:2]
    task.width_of_screen = lambda r: r * w
    task.height_of_screen = lambda r: r * h
    task.boss_health_area = lambda: Box(int(w * 1269 / 3840), int(h * 58 / 2160),
                                        to_x=int(w * 2533 / 3840), to_y=int(h * 200 / 2160))
    task.is_boss = lambda: boss_icon
    task.draw_boxes = lambda *a, **k: None
    return task


RESOLUTIONS = (('2K', (2560, 1440)), ('1K', (1920, 1080)))


class BossStillAliveTest(unittest.TestCase):
    def test_real_bar_alive_both_resolutions(self):
        for res, size in RESOLUTIONS:
            with self.subTest(res=res):
                self.assertTrue(make_frame_task(boss_frame(size=size)).boss_still_alive())

    def test_low_hp_tip_alive_both_resolutions(self):
        # 血条左端是尖头, 残血只剩尖头时原来的 has_boss_health_bar(要块占满 95%) 认不到
        for res, size in RESOLUTIONS:
            for hp in (5, 2, 1):
                with self.subTest(res=res, hp=hp):
                    task = make_frame_task(boss_frame(hp, size))
                    self.assertTrue(task.boss_still_alive())
                    if hp <= 5:
                        self.assertFalse(task.has_boss_health_bar(), '原判断在这个血量就漏了')

    def test_empty_bar_is_dead_both_resolutions(self):
        for res, size in RESOLUTIONS:
            with self.subTest(res=res):
                self.assertFalse(make_frame_task(boss_frame(0, size)).boss_still_alive())

    def test_split_fill_counts_as_alive(self):
        self.assertTrue(make_frame_task(split_fill_frame()).boss_still_alive())

    def test_break_icon_counts_as_alive(self):
        self.assertTrue(make_frame_task(boss_frame(0), boss_icon=True).boss_still_alive())


if __name__ == '__main__':
    unittest.main(verbosity=1)
