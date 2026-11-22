"""前台输入 SendInput 后端(不起游戏、不起界面, 也不真的往系统里发键: SendInput/SetCursorPos 都换成假的)。

在 coco-ww 根目录跑:  python\\python.exe -X utf8 tests\\test_foreground_input.py
"""
import ctypes
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import win32con

from ok.core.events import communicate
from src import foreground_input as fi
from src.combat.FKeyStart import f_key_watcher
from src.foreground_input import INPUT, SendInput, resolve_key


class FakeUser32:
    """把 ctypes.windll.user32 上的 SendInput / SetCursorPos 换成假的, 按发出的顺序记下事件。

    hooked: 假的 SendInput 外面照样套记账那一层 (和实际运行一样, 队伍代码直接调 SendInput 也记得到);
    False = 那一层被别的代码换掉了, 只剩后端自己补记。
    """

    NAMES = ('SendInput', 'SetCursorPos')

    def __init__(self, fail=False, hooked=True):
        self.fail = fail
        self.hooked = hooked
        self.events = []
        self.raw = []
        self.cursor = []

    def __enter__(self):
        user32 = ctypes.windll.user32
        self._saved = {name: user32.__dict__.get(name) for name in self.NAMES}
        fi.injected.clear()
        user32.SendInput = fi._SendInputHook(self._send_input, fi.injected) if self.hooked else self._send_input
        user32.SetCursorPos = self._set_cursor
        return self

    def __exit__(self, *exc):
        user32 = ctypes.windll.user32
        for name, value in self._saved.items():
            if value is None:
                delattr(user32, name)
            else:
                setattr(user32, name, value)

    def _set_cursor(self, x, y):
        self.cursor.append((x, y))
        return 1

    def _send_input(self, count, pointer, size):
        self.assertions(count, size)
        raw = ctypes.string_at(fi._input_address(pointer), count * size)
        self.raw.append((raw, size))
        if self.fail:
            return 0
        for i in range(count):
            inp = INPUT.from_buffer_copy(raw[i * size:(i + 1) * size])
            if inp.type == fi.INPUT_KEYBOARD:
                ki = inp.u.ki
                up = bool(ki.dwFlags & fi.KEYEVENTF_KEYUP)
                if ki.dwFlags & fi.KEYEVENTF_UNICODE:
                    self.events.append(('uu' if up else 'ud', chr(ki.wScan)))
                else:
                    self.events.append(('ku' if up else 'kd', ki.wVk, ki.wScan,
                                        bool(ki.dwFlags & fi.KEYEVENTF_EXTENDEDKEY)))
            else:
                mi = inp.u.mi
                self.events.append(('mouse', mi.dwFlags, mi.mouseData))
        return count

    @staticmethod
    def assertions(count, size):
        assert size == ctypes.sizeof(INPUT), size
        assert 0 < count <= 16, count


class FakeWindow:
    def __init__(self, foreground=True):
        self.foreground = foreground
        self.visible_monitors = []
        self.brought = 0

    def is_foreground(self):
        return self.foreground

    def bring_to_front(self):
        self.brought += 1


class FakeCapture:
    def get_abs_cords(self, x, y):
        return 1000 + x, 500 + y


def make(foreground=True, window=None):
    window = window or FakeWindow(foreground)
    return SendInput(FakeCapture(), window), window


VK_Q = ord('Q')
VK_E = ord('E')
LEFT_DOWN, LEFT_UP = 0x0002, 0x0004
RIGHT_DOWN, RIGHT_UP = 0x0008, 0x0010


class KeyTableTest(unittest.TestCase):
    def test_struct_matches_win32_layout(self):
        # 64 位 INPUT 是 40 字节, union 从第 8 字节开始; 跑轴记录按这个布局解码
        if ctypes.sizeof(ctypes.c_void_p) == 8:
            self.assertEqual(ctypes.sizeof(INPUT), 40)
            self.assertEqual(INPUT.u.offset, 8)
        else:
            self.assertEqual(ctypes.sizeof(INPUT), 28)

    def test_letters_digits_and_case(self):
        self.assertEqual(resolve_key('q')[:2], (VK_Q, 0x10))
        self.assertEqual(resolve_key('Q')[0], VK_Q, '大写字母也按普通字母键发, 不发 Unicode 包')
        self.assertEqual(resolve_key('1')[0], 0x31)
        self.assertFalse(resolve_key('q')[2])

    def test_named_keys_share_postmessage_table(self):
        self.assertEqual(resolve_key('lshift')[:2], (win32con.VK_LSHIFT, 0x2A))
        self.assertEqual(resolve_key('space')[0], win32con.VK_SPACE)
        self.assertEqual(resolve_key('f2')[0], win32con.VK_F2)
        self.assertEqual(resolve_key('esc')[0], win32con.VK_ESCAPE)
        self.assertEqual(resolve_key('tab')[0], win32con.VK_TAB)

    def test_keys_missing_from_vk_table(self):
        self.assertEqual(resolve_key('num0')[0], win32con.VK_NUMPAD0)
        self.assertEqual(resolve_key('num9')[0], win32con.VK_NUMPAD9)
        self.assertEqual(resolve_key('alt_gr')[0], win32con.VK_RMENU)

    def test_extended_keys(self):
        for name in ('left', 'up', 'right', 'down', 'insert', 'delete', 'home', 'end', 'pageup', 'pagedown',
                     'rctrl', 'ralt', 'win'):
            self.assertTrue(resolve_key(name)[2], name)
        for name in ('lshift', 'lctrl', 'lalt', 'space', 'e', 'f1'):
            self.assertFalse(resolve_key(name)[2], name)

    def test_unknown_key(self):
        self.assertIsNone(resolve_key('nosuchkey'))


class SendInputTest(unittest.TestCase):
    def setUp(self):
        f_key_watcher._self_held, f_key_watcher._self_until = False, 0.0

    def tearDown(self):
        f_key_watcher._self_held, f_key_watcher._self_until = False, 0.0

    def test_name_and_label(self):
        self.assertEqual(SendInput.__name__, 'SendInput', '设置里显示和保存的名字')
        title, _, description = SendInput.ui_label.partition(' - ')
        self.assertEqual(title, 'SendInput')
        self.assertTrue(description)

    def test_send_key_down_then_up(self):
        with FakeUser32() as user32:
            interaction, _ = make()
            interaction.send_key('e', 0)
        self.assertEqual(user32.events, [('kd', VK_E, 0x12, False), ('ku', VK_E, 0x12, False)])
        self.assertEqual(interaction.held_inputs(), [])

    def test_background_press_is_dropped(self):
        with FakeUser32() as user32:
            interaction, _ = make(foreground=False)
            interaction.send_key('e', 0)
            interaction.send_key_down('q')
            interaction.mouse_down()
            interaction.click(10, 10)
            interaction.input_text('x')
        self.assertEqual(user32.events, [])
        self.assertEqual(user32.cursor, [])

    def test_release_goes_out_even_after_window_lost(self):
        """Pynput 在这里不发松键, 左键会在整个系统里一直按着。"""
        with FakeUser32() as user32:
            interaction, window = make()
            interaction.mouse_down()
            interaction.send_key_down('lshift')
            window.foreground = False
            interaction.mouse_up()
            interaction.send_key_up('lshift')
        self.assertEqual([e[:2] for e in user32.events],
                         [('mouse', LEFT_DOWN), ('kd', win32con.VK_LSHIFT), ('mouse', LEFT_UP),
                          ('ku', win32con.VK_LSHIFT)])
        self.assertEqual(interaction.held_inputs(), [])

    def test_unheld_release_in_background_is_skipped(self):
        with FakeUser32() as user32:
            interaction, _ = make(foreground=False)
            interaction.mouse_up()
            interaction.send_key_up('e')
        self.assertEqual(user32.events, [])

    def test_unheld_release_in_foreground_still_sent(self):
        # 队伍代码自己调 SendInput 按的, 或者保险起见的松键: 前台时照发
        with FakeUser32() as user32:
            interaction, _ = make()
            interaction.mouse_up(key='right')
        self.assertEqual(user32.events, [('mouse', RIGHT_UP, 0)])

    def test_window_to_background_releases_everything_once(self):
        with FakeUser32() as user32:
            interaction, window = make()
            interaction.mouse_down()
            interaction.send_key_down('w')
            interaction.send_key_down('f')
            self.assertTrue(f_key_watcher._self_held)
            window.foreground = False
            for monitor in window.visible_monitors:
                monitor.on_visible(False)
            released = [e[:2] for e in user32.events[3:]]
            for monitor in window.visible_monitors:
                monitor.on_visible(False)
        self.assertEqual(sorted(released, key=str),
                         sorted([('mouse', LEFT_UP), ('ku', ord('W')), ('ku', ord('F'))], key=str))
        self.assertEqual(len(user32.events), 6, '第二次切后台没东西可松')
        self.assertFalse(f_key_watcher._self_held, '松掉的 F 要告诉读键线程')

    def test_pause_releases_held_inputs(self):
        with FakeUser32() as user32:
            interaction, _ = make()
            interaction.mouse_down()
            fi._on_executor_paused(False)
            self.assertEqual(len(user32.events), 1, '恢复运行不松键')
            fi._on_executor_paused(True)
        self.assertEqual(user32.events[-1][:2], ('mouse', LEFT_UP))
        self.assertEqual(interaction.held_inputs(), [])

    def test_pause_hook_is_subscribed(self):
        self.assertIn(fi._on_executor_paused, communicate.executor_paused._subscribers)

    def test_new_instance_takes_over_window_callbacks(self):
        with FakeUser32() as user32:
            old, window = make()
            old.mouse_down()
            new, _ = make(window=window)
        self.assertEqual(window.visible_monitors, [new])
        self.assertEqual(user32.events[-1][:2], ('mouse', LEFT_UP), '换实例时旧实例按着的键要松掉')

    def test_destroy_releases_and_unsubscribes(self):
        with FakeUser32() as user32:
            interaction, window = make()
            interaction.send_key_down('e')
            interaction.on_destroy()
        self.assertEqual(user32.events[-1][:2], ('ku', VK_E))
        self.assertEqual(window.visible_monitors, [])

    def test_activate_kwarg_and_f_notes(self):
        with FakeUser32():
            interaction, _ = make()
            interaction.send_key_down('f', activate=False)
            self.assertTrue(f_key_watcher._self_held, '按 F 之前读键线程就要知道')
            interaction.send_key_up('f')
            self.assertFalse(f_key_watcher._self_held)
            interaction.send_key('f', 0)
            self.assertTrue(f_key_watcher._self_pressing())

    def test_click_moves_then_clicks_and_moves_back(self):
        with FakeUser32() as user32:
            interaction, _ = make()
            original = fi.win32api.GetCursorPos
            fi.win32api.GetCursorPos = lambda: (7, 8)
            try:
                interaction.click(100, 200, move_back=True, down_time=0, key='right')
            finally:
                fi.win32api.GetCursorPos = original
        self.assertEqual(user32.cursor, [(1100, 700), (7, 8)])
        self.assertEqual(user32.events, [('mouse', RIGHT_DOWN, 0), ('mouse', RIGHT_UP, 0)])

    def test_click_without_position_does_not_move(self):
        with FakeUser32() as user32:
            interaction, _ = make()
            interaction.click(down_time=0)
        self.assertEqual(user32.cursor, [])
        self.assertEqual(user32.events, [('mouse', LEFT_DOWN, 0), ('mouse', LEFT_UP, 0)])

    def test_side_buttons(self):
        with FakeUser32() as user32:
            interaction, _ = make()
            interaction.send_key('mouse5', 0)
        self.assertEqual(user32.events, [('mouse', fi.MOUSEEVENTF_XDOWN, 2), ('mouse', fi.MOUSEEVENTF_XUP, 2)])

    def test_scroll_down(self):
        with FakeUser32() as user32:
            interaction, _ = make()
            interaction.scroll(50, 60, -2)
        self.assertEqual(user32.cursor, [(1050, 560)])
        wheel = (-fi.WHEEL_DELTA) & 0xFFFFFFFF
        self.assertEqual(user32.events, [('mouse', fi.MOUSEEVENTF_WHEEL, wheel)] * 2)

    def test_input_text_types_unicode(self):
        with FakeUser32() as user32:
            interaction, _ = make()
            interaction.input_text('达妮娅a')
        typed = ''.join(e[1] for e in user32.events if e[0] == 'ud')
        self.assertEqual(typed, '达妮娅a')
        self.assertEqual(len(user32.events), 8)

    def test_swipe_holds_left_and_releases(self):
        with FakeUser32() as user32:
            interaction, _ = make()
            interaction.swipe(0, 0, 100, 0, 300)
        self.assertEqual([e[:2] for e in user32.events], [('mouse', LEFT_DOWN), ('mouse', LEFT_UP)])
        self.assertEqual(user32.cursor[0], (1000, 500))
        self.assertEqual(user32.cursor[-1], (1100, 500))
        self.assertEqual(interaction.held_inputs(), [])

    def test_failed_send_is_not_tracked(self):
        for hooked in (True, False):
            with FakeUser32(fail=True, hooked=hooked):
                interaction, _ = make()
                interaction.mouse_down()
                interaction.send_key_down('e')
            self.assertEqual(interaction.held_inputs(), [], hooked)

    def test_tracks_own_holds_without_hook_layer(self):
        """记账那一层被换掉了 (没有经过它), 后端自己补记, 切后台照样松。"""
        with FakeUser32(hooked=False) as user32:
            interaction, window = make()
            interaction.mouse_down()
            self.assertEqual(interaction.held_inputs(), [('mouse', LEFT_DOWN, 0)])
            window.foreground = False
            interaction.on_visible(False)
        self.assertEqual(user32.events[-1][:2], ('mouse', LEFT_UP))
        self.assertEqual(interaction.held_inputs(), [])

    def test_hold_noted_once_through_hook(self):
        with FakeUser32() as user32:
            interaction, _ = make()
            interaction.mouse_down()
            interaction.send_key_down('e')
            self.assertEqual(sorted(interaction.held_inputs(), key=str),
                             sorted([('mouse', LEFT_DOWN, 0), ('key', VK_E)], key=str))
            self.assertEqual(interaction.release_all('测试'), 2)
        self.assertEqual(len(user32.events), 4)

    def test_unknown_key_sends_nothing(self):
        with FakeUser32() as user32:
            interaction, _ = make()
            interaction.send_key('nosuchkey', 0)
        self.assertEqual(user32.events, [])

    def test_foreground_gates_capture(self):
        with FakeUser32():
            interaction, window = make()
            self.assertTrue(interaction.should_capture())
            window.foreground = False
            self.assertFalse(interaction.should_capture())
            interaction.on_run()
        self.assertEqual(window.brought, 1)


class _TeamKEYBDINPUT(ctypes.Structure):
    _fields_ = [('wVk', ctypes.c_ushort), ('wScan', ctypes.c_ushort), ('dwFlags', ctypes.c_ulong),
                ('time', ctypes.c_ulong), ('dwExtraInfo', ctypes.c_size_t)]


class _TeamMOUSEINPUT(ctypes.Structure):
    _fields_ = [('dx', ctypes.c_long), ('dy', ctypes.c_long), ('mouseData', ctypes.c_ulong),
                ('dwFlags', ctypes.c_ulong), ('time', ctypes.c_ulong), ('dwExtraInfo', ctypes.c_size_t)]


class _TeamHARDWAREINPUT(ctypes.Structure):
    _fields_ = [('uMsg', ctypes.c_ulong), ('wParamH', ctypes.c_ushort), ('wParamL', ctypes.c_ushort)]


class _TeamINPUTUNION(ctypes.Union):
    _fields_ = [('ki', _TeamKEYBDINPUT), ('mi', _TeamMOUSEINPUT), ('hi', _TeamHARDWAREINPUT)]


class _TeamINPUT(ctypes.Structure):
    """照抄队伍代码 (奥古斯塔/心/秧秧) 里自己定义的 _INPUT。"""
    _fields_ = [('type', ctypes.c_ulong), ('u', _TeamINPUTUNION)]


def team_mouse(flags, data=0):
    """和队伍代码的 send_input_left 一样: 自己的结构, 直接调 ctypes.windll.user32.SendInput。"""
    inp = _TeamINPUT(type=0)
    inp.u.mi = _TeamMOUSEINPUT(0, 0, data, flags, 0, 0)
    return ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_TeamINPUT)) == 1


def team_key(vk, scan, flags):
    inp = _TeamINPUT(type=1)
    inp.u.ki = _TeamKEYBDINPUT(vk, scan, flags, 0, 0)
    return ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_TeamINPUT)) == 1


class TeamCodeHoldTest(unittest.TestCase):
    """队伍代码自己调 SendInput 按住的键 (实机 2026-11-22 01:31 那一轮: 奥古斯塔左键按住时切走窗口, 一直没松)。"""

    def setUp(self):
        f_key_watcher._self_held, f_key_watcher._self_until = False, 0.0

    def tearDown(self):
        f_key_watcher._self_held, f_key_watcher._self_until = False, 0.0

    def test_window_to_background_releases_team_hold(self):
        with FakeUser32() as user32:
            interaction, window = make()
            self.assertTrue(team_mouse(LEFT_DOWN))
            self.assertEqual(interaction.held_inputs(), [('mouse', LEFT_DOWN, 0)])
            window.foreground = False
            for monitor in window.visible_monitors:
                monitor.on_visible(False)
        self.assertEqual(user32.events, [('mouse', LEFT_DOWN, 0), ('mouse', LEFT_UP, 0)])
        self.assertEqual(interaction.held_inputs(), [])

    def test_team_release_after_auto_release_is_harmless(self):
        with FakeUser32() as user32:
            interaction, window = make()
            team_mouse(LEFT_DOWN)
            window.foreground = False
            interaction.on_visible(False)
            team_mouse(LEFT_UP)  # 队伍代码按完了自己松, 多一个抬起不要紧
        self.assertEqual([e[1] for e in user32.events], [LEFT_DOWN, LEFT_UP, LEFT_UP])
        self.assertEqual(fi.injected.held(), [])

    def test_pause_releases_team_hold_without_sendinput_backend(self):
        """后台输入 (PostMessage) 下队伍代码也会自己用 SendInput 按左键: 暂停照样松。"""
        with FakeUser32() as user32:
            team_mouse(LEFT_DOWN)
            team_key(ord('W'), 0x11, 0)
            fi._on_executor_paused(True)
        self.assertEqual(sorted(user32.events[2:], key=str),
                         sorted([('mouse', LEFT_UP, 0), ('ku', ord('W'), 0x11, False)], key=str))
        self.assertEqual(fi.injected.held(), [])

    def test_exit_releases_team_hold(self):
        with FakeUser32() as user32:
            team_mouse(fi.MOUSEEVENTF_XDOWN, 1)
            fi._release_at_exit()
        self.assertEqual(user32.events[-1], ('mouse', fi.MOUSEEVENTF_XUP, 1))

    def test_backend_mouse_up_releases_team_hold_in_background(self):
        """账上按着的, 游戏不在前台也照发松键。"""
        with FakeUser32() as user32:
            interaction, window = make()
            team_mouse(LEFT_DOWN)
            window.foreground = False
            interaction.mouse_up()
        self.assertEqual(user32.events[-1], ('mouse', LEFT_UP, 0))

    def test_failed_team_send_not_tracked(self):
        with FakeUser32(fail=True):
            self.assertFalse(team_mouse(LEFT_DOWN))
        self.assertEqual(fi.injected.held(), [])

    def test_move_with_button_down_flag_is_tracked(self):
        with FakeUser32():
            team_mouse(0x0001 | LEFT_DOWN)  # MOUSEEVENTF_MOVE | LEFTDOWN
            self.assertEqual(fi.injected.held(), [('mouse', LEFT_DOWN, 0)])
            team_mouse(fi.MOUSEEVENTF_WHEEL, fi.WHEEL_DELTA)
            self.assertEqual(fi.injected.held(), [('mouse', LEFT_DOWN, 0)], '滚轮不算按住')

    def test_scancode_key_released_by_scancode(self):
        with FakeUser32() as user32:
            team_key(0, 0x2A, fi.KEYEVENTF_SCANCODE)  # 左 Shift, 只给扫描码
            self.assertEqual(fi.injected.held(), [('scan', 0x2A, False)])
            fi.release_injected('测试')
        raw, size = user32.raw[-1]
        release = INPUT.from_buffer_copy(raw[:size])
        self.assertEqual(release.u.ki.wScan, 0x2A)
        self.assertEqual(release.u.ki.dwFlags, fi.KEYEVENTF_SCANCODE | fi.KEYEVENTF_KEYUP)

    def test_extended_key_released_as_extended(self):
        with FakeUser32() as user32:
            team_key(win32con.VK_RIGHT, 0x4D, fi.KEYEVENTF_EXTENDEDKEY)
            fi.release_injected('测试')
        self.assertEqual(user32.events[-1], ('ku', win32con.VK_RIGHT, 0x4D, True))

    def test_unicode_is_not_tracked(self):
        with FakeUser32():
            team_key(0, ord('a'), fi.KEYEVENTF_UNICODE)
        self.assertEqual(fi.injected.held(), [])

    def test_array_argument_like_pynput(self):
        """pynput 直接传数组, 不用 byref。"""
        with FakeUser32():
            inputs = (INPUT * 2)(fi.key_input(VK_Q, 0x10, False, False), fi.mouse_input(RIGHT_DOWN))
            ctypes.windll.user32.SendInput(2, inputs, ctypes.sizeof(INPUT))
        self.assertEqual(sorted(fi.injected.held(), key=str),
                         sorted([('key', VK_Q), ('mouse', RIGHT_DOWN, 0)], key=str))

    def test_partial_send_tracks_only_what_went_out(self):
        tracker = fi.InjectedInputs()
        inputs = (INPUT * 2)(fi.key_input(VK_Q, 0x10, False, False), fi.key_input(VK_E, 0x12, False, False))
        tracker.note_call((2, ctypes.byref(inputs), ctypes.sizeof(INPUT)), 1)
        self.assertEqual(tracker.held(), [('key', VK_Q)])
        tracker.note_call(('bad',), 1)
        tracker.note_call((1, object(), ctypes.sizeof(INPUT)), 1)
        self.assertEqual(tracker.held(), [('key', VK_Q)], '认不出的参数不记、不报错')


class HookInstallTest(unittest.TestCase):
    def test_real_send_input_is_hooked(self):
        layers = fi._layers(ctypes.windll.user32.SendInput)
        self.assertIn(fi._hook, layers)
        self.assertEqual(type(layers[-1]).__name__, '_FuncPtr', '最里面是真的 SendInput')

    def test_hook_forwards_attributes(self):
        class Func:
            argtypes = None

            def __call__(self, *args):
                return 0

        func = Func()
        hook = fi._SendInputHook(func, fi.InjectedInputs())
        hook.argtypes = (ctypes.c_uint,)
        self.assertEqual(func.argtypes, (ctypes.c_uint,), 'pynput 设 argtypes 要设到原函数上')
        self.assertEqual(hook.argtypes, (ctypes.c_uint,))
        self.assertEqual(hook(1, None, 40), 0)

    def test_install_goes_under_recorder_and_is_idempotent(self):
        from src.recorder.recorder import _SendInputTap

        class Recorder:
            run = None

        user32 = ctypes.windll.user32
        saved = user32.__dict__.get('SendInput')
        calls = []

        def real(*args):
            calls.append(args)
            return int(args[0])

        try:
            tap = _SendInputTap(Recorder(), real)
            user32.SendInput = tap
            tracker = fi.InjectedInputs()
            hook = fi.install_send_input_hook(tracker)
            self.assertIs(user32.SendInput, tap, '跑轴记录那层留在外面')
            self.assertEqual(fi._layers(user32.SendInput), [tap, hook, real])
            self.assertIs(fi.install_send_input_hook(fi.InjectedInputs()), hook, '再装一次用原来那层')
            team_mouse(LEFT_DOWN)
            self.assertEqual(len(calls), 1)
            self.assertEqual(tracker.held(), [('mouse', LEFT_DOWN, 0)])
        finally:
            user32.SendInput = saved


class RecorderCompatTest(unittest.TestCase):
    def test_axis_recorder_decodes_what_we_send(self):
        """跑轴记录的 SendInput 记录器 (si=1 那一层) 要能认出这些事件。"""
        from src.recorder.recorder import decode_send_input
        with FakeUser32() as user32:
            interaction, _ = make()
            interaction.send_key('q', 0)
            interaction.click(down_time=0)
            interaction.send_key('space', 0)
        decoded = [event for raw, size in user32.raw for event in decode_send_input(raw, size)]
        self.assertEqual(decoded, [('kd', 'q'), ('ku', 'q'), ('md', 'left'), ('mu', 'left'),
                                   ('kd', 'space'), ('ku', 'space')])


# fork: coco's ConnectionLabelTest dropped; it tests coco's vendored ok/ui/qt/shell/connection.py, PyPI ok-script lacks it


def tearDownModule():
    # 账上留着测试里假按的键的话, 进程退出时 atexit 会真的往系统发松键
    fi.injected.clear()


if __name__ == '__main__':
    unittest.main()
