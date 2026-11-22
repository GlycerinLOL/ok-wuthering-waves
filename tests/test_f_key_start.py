"""按 F 开始战斗的判定逻辑(不起游戏、不起界面)。

在 coco-ww 根目录跑:  python\\python.exe -X utf8 tests\\test_f_key_start.py
"""
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.combat import FKeyStart
from src.combat.FKeyStart import F_EXTRA_LIFETIME, FKeyWatcher, f_key_watcher
from src.task.AutoCombatTask import AutoCombatTask


class FakeScene:
    def __init__(self):
        self.combat = None

    def set_in_combat(self):
        self.combat = True
        return True

    def set_not_in_combat(self):
        self.combat = False


class FakeBox:
    def __init__(self, x=100, y=100, width=26, height=26):
        self.x, self.y, self.width, self.height = x, y, width, height


def make_task(enabled=True, delay=0.0, grace=10.0, open_world=False, chars=True):
    """不走 __init__ (那要执行器和截图), 只摆出判定用得到的属性。"""
    task = object.__new__(AutoCombatTask)
    task.__dict__.update(
        config={AutoCombatTask.F_START_KEY: enabled, AutoCombatTask.F_DELAY_KEY: delay,
                AutoCombatTask.F_GRACE_KEY: grace, 'Auto Target': True},
        _in_combat=False, _f_consumed=-1.0, _f_grace_since=-1.0, _f_grace_log=0.0,
        scene=FakeScene(), has_lavitator=False, clicks=[], logs=[], target=False,
        prompt=None, prompt_absorb=False, prompt_reads=0,
    )

    def read_prompt(_f):
        task.prompt_reads += 1
        return task.prompt_absorb

    task.find_f_prompt = lambda: task.prompt
    task.f_prompt_is_absorb = read_prompt
    task.load_chars = lambda: chars
    task.is_open_world_auto_combat = lambda: open_world
    task.find_one = lambda *a, **k: None
    task.middle_click = lambda *a, **k: task.clicks.append(k)
    task.log_info = lambda message, *a, **k: task.logs.append(message)
    task.find_target = lambda double_check=False: task.target
    task.is_browser = lambda: False  # fork: upstream v3.8.5 cloud-game check
    return task


class FKeyStartTest(unittest.TestCase):
    def setUp(self):
        f_key_watcher.last_press = -1.0

    def press(self, seconds_ago=0.0):
        f_key_watcher.last_press = time.time() - seconds_ago

    def test_no_press_no_combat(self):
        self.assertFalse(make_task().should_start_combat_by_key())

    def test_press_starts_combat_once(self):
        task = make_task()
        self.press()
        self.assertTrue(task.should_start_combat_by_key())
        self.assertTrue(task.start_combat_by_key())
        self.assertTrue(task._in_combat)
        self.assertTrue(task.scene.combat)
        self.assertGreater(task._f_grace_since, 0)
        self.assertEqual(len(task.clicks), 1)
        # 同一次按键不会再开第二场
        task._in_combat = False
        self.assertFalse(task.should_start_combat_by_key())

    def test_delay_holds_the_press(self):
        task = make_task(delay=0.4)
        self.press(0.1)
        self.assertFalse(task.should_start_combat_by_key())
        self.press(0.5)
        self.assertTrue(task.should_start_combat_by_key())

    def test_stale_press_is_dropped(self):
        task = make_task(delay=0.4)
        self.press(0.4 + F_EXTRA_LIFETIME + 1)
        self.assertFalse(task.should_start_combat_by_key())
        self.assertEqual(task._f_consumed, f_key_watcher.last_press)

    def test_switch_off_swallows_the_press(self):
        task = make_task(enabled=False)
        self.press()
        self.assertFalse(task.should_start_combat_by_key())
        task.config[AutoCombatTask.F_START_KEY] = True
        self.assertFalse(task.should_start_combat_by_key(), '开关关着时按的 F, 打开后不该生效')

    def test_open_world_press_is_ignored(self):
        task = make_task(open_world=True)
        self.press()
        self.assertFalse(task.should_start_combat_by_key())
        self.assertIn('大世界', task.logs[-1])

    def test_loading_screen_keeps_the_press(self):
        task = make_task(chars=False)
        self.press()
        self.assertFalse(task.should_start_combat_by_key())
        task.load_chars = lambda: True
        self.assertTrue(task.should_start_combat_by_key(), '读条时读不到队伍, 这次按键要留到读到为止')

    def test_team_code_press_counts(self):
        task = make_task()
        task._f_pressed_at = time.time()
        self.assertTrue(task.should_start_combat_by_key())

    def test_press_before_enable_or_combat_end_is_dropped(self):
        task = make_task()
        self.press(0.05)
        task.drop_pending_f_press()
        self.assertFalse(task.should_start_combat_by_key())

    def test_grace_holds_until_target_or_limit(self):
        task = make_task(grace=10.0)
        self.press()
        task.should_start_combat_by_key()
        task.start_combat_by_key()
        self.assertTrue(task.target_enemy(wait=True), '保护期内没锁到目标也放行')
        task._f_grace_since = time.time() - 11
        self.assertFalse(task.f_grace_hold(), '撑过上限退出保护期')
        self.assertLessEqual(task._f_grace_since, 0)

    def test_target_found_ends_grace(self):
        task = make_task()
        self.press()
        task.should_start_combat_by_key()
        task.start_combat_by_key()
        task.target = True
        self.assertTrue(task.has_target())
        self.assertLessEqual(task._f_grace_since, 0)

    def test_grace_zero_means_no_limit(self):
        task = make_task(grace=0.0)
        task._in_combat = True
        task._f_grace_since = time.time() - 3600
        self.assertTrue(task.f_grace_hold())

    def test_absorb_press_is_ignored(self):
        task = make_task()
        task.prompt, task.prompt_absorb = FakeBox(), True
        self.assertFalse(task.should_start_combat_by_key(), '还没按, 只是看到了吸收提示')
        # 按下去提示就没了
        task.prompt = None
        self.press()
        self.assertFalse(task.should_start_combat_by_key())
        self.assertIn('吸收', task.logs[-1])
        self.assertFalse(task.should_start_combat_by_key(), '这次按键已经用掉')

    def test_other_prompt_press_still_starts(self):
        task = make_task()
        task.prompt, task.prompt_absorb = FakeBox(), False
        task.should_start_combat_by_key()
        self.press()
        self.assertTrue(task.should_start_combat_by_key(), '领奖/重新挑战/开始挑战的 F 照旧开打')

    def test_absorb_seen_long_ago_does_not_block(self):
        task = make_task()
        task._absorb_prompt_at = time.time() - 5
        self.press()
        self.assertTrue(task.should_start_combat_by_key())

    def test_prompt_text_read_only_when_new_or_stale(self):
        task = make_task()
        task.prompt = FakeBox()
        task._f_consumed = time.time() + 60  # 不让判定往下走, 只看读字次数
        for _ in range(5):
            task.should_start_combat_by_key()
        self.assertEqual(task.prompt_reads, 1, '同一个提示挂着不每帧 OCR')
        task.prompt = FakeBox(y=200)
        task.should_start_combat_by_key()
        self.assertEqual(task.prompt_reads, 2, '换了位置(列表换选中项)重读')
        task._f_prompt_read_at -= 1
        task.should_start_combat_by_key()
        self.assertEqual(task.prompt_reads, 3, '挂久了重读')

    def test_switch_off_skips_prompt_reading(self):
        task = make_task(enabled=False)
        task.prompt = FakeBox()
        task.should_start_combat_by_key()
        self.assertEqual(task.prompt_reads, 0)


class FKeyWatcherTest(unittest.TestCase):
    def test_only_rising_edge_in_foreground_counts(self):
        watcher = FKeyWatcher()
        watcher.is_game_foreground = lambda: False
        self.assertTrue(watcher.on_sample(True, False))
        self.assertEqual(watcher.last_press, -1.0, '游戏不在前台不记')
        watcher.is_game_foreground = lambda: True
        watcher.on_sample(True, True)
        self.assertEqual(watcher.last_press, -1.0, '按住不放不是新的一次')
        watcher.on_sample(True, False)
        self.assertGreater(watcher.last_press, 0)

    def test_self_sent_f_is_ignored(self):
        """前台输入下 coco-ww 自己按的 F 会改按键状态, 不能当成玩家按的。"""
        watcher = FKeyWatcher()
        watcher.is_game_foreground = lambda: True
        watcher.note_self_key('F', 0.02)
        watcher.on_sample(True, False)
        self.assertEqual(watcher.last_press, -1.0, '自己点一下 F')
        watcher.note_self_key('e', 0.02)
        watcher._self_until = 0.0
        watcher.on_sample(True, False)
        self.assertGreater(watcher.last_press, 0, '别的键不影响, 宽限过了照常认')

    def test_self_held_f_is_ignored_until_grace_ends(self):
        watcher = FKeyWatcher()
        watcher.is_game_foreground = lambda: True
        watcher.note_self_key('f')
        watcher._self_until = 0.0
        watcher.on_sample(True, False)
        self.assertEqual(watcher.last_press, -1.0, '自己按住 F 期间')
        watcher.note_self_key_up('f')
        watcher.on_sample(True, False)
        self.assertEqual(watcher.last_press, -1.0, '刚松开还在宽限里')
        watcher._self_until = 0.0
        watcher.on_sample(True, False)
        self.assertGreater(watcher.last_press, 0, '宽限过了玩家按 F 照常认')


class SelfFFilterTest(unittest.TestCase):
    def test_filter_hides_only_self_pressed_f(self):
        watcher = FKeyWatcher()
        pressed = -32768  # c_short 的高位
        reads = FKeyStart._filter_self_f(lambda vk: pressed, watcher)
        self.assertEqual(reads(FKeyStart.F_VK), pressed, '没在自己按 F: 原样返回')
        watcher.note_self_key('f', 0.05)
        self.assertEqual(reads(FKeyStart.F_VK), 0, '自己按 F 期间读成没按')
        self.assertEqual(reads(0x45), pressed, '别的键不管')
        watcher._self_until = 0.0
        self.assertEqual(reads(FKeyStart.F_VK), pressed, '宽限过了恢复')

    def test_team_code_style_reader_goes_through_filter(self):
        """队伍代码的写法: 每次从 ctypes.windll.user32 上取 GetAsyncKeyState, 还会给它设 restype。"""
        import ctypes
        self.assertTrue(FKeyStart.install_self_f_filter())
        self.assertTrue(FKeyStart.install_self_f_filter(), '重复装不会包两层')
        user32 = ctypes.windll.user32
        self.assertTrue(getattr(user32.GetAsyncKeyState, 'coco_self_f_filter', False))
        user32.GetAsyncKeyState.restype = ctypes.c_short
        self.assertIsInstance(user32.GetAsyncKeyState(0x46) & 0x8000, int)


class ForegroundInputTest(unittest.TestCase):
    def test_pynput_reports_f_before_sending(self):
        from ok.device.interaction_methods.pynput import PynputInteraction
        from src.foreground_input import Pynput
        sent = []
        originals = {name: getattr(PynputInteraction, name) for name in ('send_key', 'send_key_down', 'send_key_up')}
        PynputInteraction.send_key = lambda self, key, down_time=0.01: sent.append(
            ('key', key, f_key_watcher._self_pressing()))
        PynputInteraction.send_key_down = lambda self, key: sent.append(('down', key, f_key_watcher._self_pressing()))
        PynputInteraction.send_key_up = lambda self, key: sent.append(('up', key, f_key_watcher._self_pressing()))
        try:
            f_key_watcher._self_held, f_key_watcher._self_until = False, 0.0
            interaction = object.__new__(Pynput)
            self.assertEqual(Pynput.__name__, 'Pynput', '设置里显示和保存的名字')
            interaction.send_key('f', 0.01)
            interaction.send_key_down('f', activate=False)
            interaction.send_key_up('f')
            self.assertEqual(sent, [('key', 'f', True), ('down', 'f', True), ('up', 'f', True)],
                             '发 F 之前读键线程就要知道')
            self.assertFalse(f_key_watcher._self_held)
        finally:
            for name, method in originals.items():
                setattr(PynputInteraction, name, method)
            f_key_watcher._self_held, f_key_watcher._self_until = False, 0.0

    def test_config_offers_both_input_methods(self):
        from config import config
        from src.foreground_input import Pynput, SendInput
        self.assertEqual(config['windows']['interaction'], ['PostMessage', SendInput, Pynput],
                         '默认仍是后台 PostMessage')


class CompatShellTest(unittest.TestCase):
    def test_shell_forwards_auto_combat_settings(self):
        task = make_task(enabled=True, delay=0.4, grace=7.0)
        shell = object.__new__(FKeyStart.FStartCombatTask)
        shell._auto_combat = lambda: task
        self.assertEqual(type(shell).__name__, 'FStartCombatTask')
        self.assertEqual((shell.enabled, shell.f_start_delay, shell.grace_limit), (True, 0.4, 7.0))
        task.config[AutoCombatTask.F_START_KEY] = False
        self.assertFalse(shell.enabled)


if __name__ == '__main__':
    unittest.main(verbosity=1)
