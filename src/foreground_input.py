"""前台输入 (游戏窗口在前台时才能用), 设置里「交互方式」选:

- SendInput: coco-ww 自己写的, 直接调 user32.SendInput。试用中, 稳了就把 Pynput 撤掉。
- Pynput: 上游 ok-ww v3.8.3 的 pynput 实现, 先留着对比。

和默认的后台 PostMessage 比:
- 键鼠走系统真实输入流, 游戏看到的和物理键盘一样 (按键状态是真的), 不用再发 WM_ACTIVATE:
  按住左键时照样能点 E、闪避, 不会把按住打断; 按下沿落在大招演出里也不丢, 演出完还算按着 (预输入)
- 只能在游戏窗口在前台时用: 切到别的窗口就暂停截图和操作; 会占用真实的键盘和鼠标
- 要管理员权限 (游戏本身是管理员运行的, 启动 coco-ww.bat 已经会提权)

SendInput 比 Pynput 多做的:
- 游戏切到后台、换交互方式、退出时, 把还按着的键全部松开。
  Pynput 在游戏不在前台时连松键都不发, 长按中切走窗口, 那个键会在整个系统里一直按着

「还按着的键」不只是后端自己按的: 导入本模块时给 ctypes.windll.user32.SendInput 套一层 (见 _SendInputHook),
本进程里谁调它都记账, 包括队伍代码自己写的 SendInput 左键 (奥古斯塔、心、秧秧这些) 和 pynput。
coco-ww 暂停 (F9 / 停止按钮) 和退出时, 不管用哪种交互方式都把这份账上的键松掉。
- 键名和后台 PostMessage 共用 vk_key_dict, 带扫描码; 方向键、右 Ctrl、Insert 这类扩展键带扩展标志;
  单个字符按当前键盘布局取虚拟键, 大写字母也按普通字母键发 (Pynput 会发成 Unicode 包)
- input_text 真的会打字 (刷4C 搜 Boss 名用; Pynput 后端这个方法是空的)
- 每次都现取 ctypes.windll.user32.SendInput, 跑轴记录换上的 SendInput 记录器能记到 (si=1 那一层)

两种都会在自己按 F 之前告诉「按 F 开始战斗」的读键线程, 免得把拾取、处决按的 F 当成玩家按的。
类名就是设置界面显示、配置里保存的名字。
"""
import atexit
import ctypes
import threading
import time

import win32api
import win32con

from ok import Logger
from ok.core.events import communicate
from ok.device.interaction_methods.base import BaseInteraction
from ok.device.interaction_methods.keys import vk_key_dict, is_mouse_button, get_mouse_button
from ok.device.interaction_methods.pynput import PynputInteraction
from ok.util.process import is_admin
from src.combat.FKeyStart import f_key_watcher

logger = Logger.get_logger(__name__)

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_SCANCODE = 0x0008

MOUSEEVENTF_XDOWN = 0x0080
MOUSEEVENTF_XUP = 0x0100
MOUSEEVENTF_WHEEL = 0x0800
WHEEL_DELTA = 120
XBUTTON1 = 0x0001
XBUTTON2 = 0x0002

# 鼠标键: 名字 -> (按下标志, 抬起标志, mouseData)。mouse4/mouse5 走 XDOWN/XUP, 见 resolve_button
MOUSE_BUTTONS = {
    'left': (0x0002, 0x0004, 0),
    'right': (0x0008, 0x0010, 0),
    'middle': (0x0020, 0x0040, 0),
}
# 按下标志 -> 抬起标志 (记账用, 含侧键)
MOUSE_DOWN_UP = {down: up for down, up, _ in MOUSE_BUTTONS.values()}
MOUSE_DOWN_UP[MOUSEEVENTF_XDOWN] = MOUSEEVENTF_XUP
MOUSE_UP_DOWN = {up: down for down, up in MOUSE_DOWN_UP.items()}

# vk_key_dict 里没有、但 validate_key 认的键名
EXTRA_VK = {
    'ESCAPE': win32con.VK_ESCAPE,
    'ALT_GR': win32con.VK_RMENU,
    **{f'NUM{i}': win32con.VK_NUMPAD0 + i for i in range(10)},
}

# 扩展键: 不带 KEYEVENTF_EXTENDEDKEY 的话, 方向键/Home/End 会被当成小键盘上的, 右 Ctrl/Alt 会变成左边的
EXTENDED_VK = frozenset({
    win32con.VK_RCONTROL, win32con.VK_RMENU,
    win32con.VK_INSERT, win32con.VK_DELETE, win32con.VK_HOME, win32con.VK_END,
    win32con.VK_PRIOR, win32con.VK_NEXT,
    win32con.VK_LEFT, win32con.VK_UP, win32con.VK_RIGHT, win32con.VK_DOWN,
    win32con.VK_NUMLOCK, win32con.VK_SNAPSHOT, win32con.VK_DIVIDE,
    win32con.VK_LWIN, win32con.VK_RWIN, win32con.VK_APPS,
})


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [('wVk', ctypes.c_ushort), ('wScan', ctypes.c_ushort), ('dwFlags', ctypes.c_ulong),
                ('time', ctypes.c_ulong), ('dwExtraInfo', ctypes.c_size_t)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [('dx', ctypes.c_long), ('dy', ctypes.c_long), ('mouseData', ctypes.c_ulong),
                ('dwFlags', ctypes.c_ulong), ('time', ctypes.c_ulong), ('dwExtraInfo', ctypes.c_size_t)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [('uMsg', ctypes.c_ulong), ('wParamL', ctypes.c_ushort), ('wParamH', ctypes.c_ushort)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [('ki', KEYBDINPUT), ('mi', MOUSEINPUT), ('hi', HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [('type', ctypes.c_ulong), ('u', _INPUTUNION)]


def resolve_key(key):
    """键名 -> (虚拟键, 扫描码, 是不是扩展键); 认不出返回 None。

    命名键和后台 PostMessage 查同一张 vk_key_dict; 单个字符按当前键盘布局取虚拟键, 不管大小写和 Shift。
    """
    name = str(key).strip()
    upper = name.upper()
    vk = vk_key_dict.get(upper) or EXTRA_VK.get(upper)
    if vk is None:
        if len(name) != 1:
            return None
        scanned = win32api.VkKeyScan(name)
        if scanned == -1 or scanned & 0xFF == 0xFF:
            return None
        vk = scanned & 0xFF
    scan = win32api.MapVirtualKey(vk, 0)
    return vk, scan, vk in EXTENDED_VK


def resolve_button(key):
    """'left' / 'right' / 'middle' / 'mouse4' / 'mouse5' -> (按下标志, 抬起标志, mouseData); 别的都当左键。"""
    if is_mouse_button(key):
        return MOUSEEVENTF_XDOWN, MOUSEEVENTF_XUP, get_mouse_button(key)
    return MOUSE_BUTTONS.get(str(key).strip().lower(), MOUSE_BUTTONS['left'])


def key_input(vk, scan, extended, up):
    flags = (KEYEVENTF_EXTENDEDKEY if extended else 0) | (KEYEVENTF_KEYUP if up else 0)
    inp = INPUT(type=INPUT_KEYBOARD)
    inp.u.ki = KEYBDINPUT(vk, scan, flags, 0, 0)
    return inp


def unicode_input(code_unit, up):
    inp = INPUT(type=INPUT_KEYBOARD)
    inp.u.ki = KEYBDINPUT(0, code_unit, KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if up else 0), 0, 0)
    return inp


def mouse_input(flags, data=0):
    inp = INPUT(type=INPUT_MOUSE)
    inp.u.mi = MOUSEINPUT(0, 0, data & 0xFFFFFFFF, flags, 0, 0)
    return inp


_BUTTON_NAMES = {(down, data): name for name, (down, _, data) in MOUSE_BUTTONS.items()}
_BUTTON_NAMES.update({(MOUSEEVENTF_XDOWN, 1): 'mouse4', (MOUSEEVENTF_XDOWN, 2): 'mouse5'})
_VK_NAMES = {}
for _name, _vk in list(vk_key_dict.items()) + list(EXTRA_VK.items()):
    _VK_NAMES.setdefault(_vk, _name.lower())


def token_label(token):
    """记账用的 token -> 日志里看的键名。"""
    if token[0] == 'mouse':
        return _BUTTON_NAMES.get(token[1:], f'mouse{token[1:]}')
    if token[0] == 'scan':
        return f'sc{token[1]:02x}'
    vk = token[1]
    if 0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A:
        return chr(vk).lower()
    return _VK_NAMES.get(vk, f'vk{vk:02x}')


def utf16_units(text):
    data = str(text).encode('utf-16-le')
    return [int.from_bytes(data[i:i + 2], 'little') for i in range(0, len(data), 2)]


# ---- 本进程用 SendInput 按下、还没松开的键 (谁按的都算) ----

def _input_address(pointer):
    """SendInput 第二个参数 (byref / 指针 / 数组 / 结构) -> 地址; 认不出返回 None。"""
    obj = getattr(pointer, '_obj', None)  # ctypes.byref(...)
    if obj is not None:
        return ctypes.addressof(obj)
    if isinstance(pointer, ctypes._Pointer):
        return ctypes.cast(pointer, ctypes.c_void_p).value
    if isinstance(pointer, (ctypes.Structure, ctypes.Array)):
        return ctypes.addressof(pointer)
    return None


def _input_events(raw, size):
    """INPUT 数组的原始字节 -> 一串 (token, 是不是按下, 松开用的 INPUT)。

    按的什么结构都行 (队伍代码有自己的 _INPUT), 只要布局和 Win32 INPUT 一样。
    Unicode 包 (打字) 不改按键状态, 纯移动、滚轮没有按住这回事, 都不记。
    """
    union_offset = 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 4
    for start in range(0, len(raw) - size + 1, size):
        chunk = raw[start:start + size]
        kind = int.from_bytes(chunk[0:4], 'little')
        body = chunk[union_offset:]
        if kind == INPUT_KEYBOARD and len(body) >= 8:
            vk = int.from_bytes(body[0:2], 'little')
            scan = int.from_bytes(body[2:4], 'little')
            flags = int.from_bytes(body[4:8], 'little')
            if flags & KEYEVENTF_UNICODE:
                continue
            extended = bool(flags & KEYEVENTF_EXTENDEDKEY)
            if flags & KEYEVENTF_SCANCODE:  # 带这个标志时系统只看扫描码
                token = ('scan', scan, extended)
            elif vk:
                token = ('key', vk)
            else:
                continue
            release = INPUT(type=INPUT_KEYBOARD)
            release.u.ki = KEYBDINPUT(vk, scan, (flags & (KEYEVENTF_EXTENDEDKEY | KEYEVENTF_SCANCODE)) | KEYEVENTF_KEYUP,
                                      0, 0)
            yield token, not flags & KEYEVENTF_KEYUP, release
        elif kind == INPUT_MOUSE and len(body) >= 16:
            data = int.from_bytes(body[8:12], 'little')
            flags = int.from_bytes(body[12:16], 'little')
            for down, up in MOUSE_DOWN_UP.items():
                if not flags & (down | up):
                    continue
                buttons = [b for b in (XBUTTON1, XBUTTON2) if data & b] if down == MOUSEEVENTF_XDOWN else [0]
                for button in buttons:
                    token = ('mouse', down, button)
                    if flags & down:
                        yield token, True, mouse_input(up, button)
                    if flags & up:
                        yield token, False, None


class InjectedInputs:
    """本进程用 SendInput 按下、还没松开的键和鼠标键。

    token 和 SendInput 后端记账用的一样: ('key', 虚拟键) / ('scan', 扫描码, 扩展键) / ('mouse', 按下标志, mouseData)。
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._held = {}  # token -> 松开用的 INPUT

    def note_raw(self, raw, size):
        with self._lock:
            for token, down, release in _input_events(raw, size):
                if down:
                    self._held[token] = release
                else:
                    self._held.pop(token, None)

    def note_call(self, args, sent):
        """SendInput(个数, 数组, 结构大小) 发出去了前 sent 个: 记账。认不出参数就不记。"""
        if len(args) < 3:
            return
        count, size = int(args[0]), int(args[2])
        sent = min(int(sent or 0), count)
        if sent <= 0 or not 0 < size <= 64:
            return
        address = _input_address(args[1])
        if address:
            self.note_raw(ctypes.string_at(address, sent * size), size)

    def is_held(self, token):
        with self._lock:
            return token in self._held

    def held(self):
        with self._lock:
            return list(self._held)

    def take_all(self):
        """清空账本, 返回 [(token, 松开用的 INPUT)]。"""
        with self._lock:
            held = list(self._held.items())
            self._held.clear()
            return held

    def clear(self):
        with self._lock:
            self._held.clear()


# send_inputs 发的时候, 记账那一层记过了就置 True; 没置说明那一层不在 (被别的代码换掉了), 自己补记
_local = threading.local()


class _SendInputHook:
    """套在 ctypes.windll.user32.SendInput 外面: 照常转发, 顺手把发出去的按下/抬起记到 InjectedInputs。

    argtypes/restype 这类属性读写都转给原函数, 别处设置它们不受影响 (pynput 导入时会设)。
    跑轴记录的 _SendInputTap 是同一个套法, 两层谁在外面都行。
    """

    def __init__(self, original, tracker):
        object.__setattr__(self, '_original', original)
        object.__setattr__(self, '_tracker', tracker)

    def __call__(self, *args):
        result = self._original(*args)
        _local.noted = True
        try:
            self._tracker.note_call(args, result)
        except Exception:
            pass
        return result

    def __getattr__(self, name):
        return getattr(self._original, name)

    def __setattr__(self, name, value):
        setattr(self._original, name, value)


def _layers(func):
    """SendInput 外面套的一层层 (_SendInputTap / _SendInputHook), 从外往里, 最后一个是原函数。"""
    layers = [func]
    while len(layers) < 8:
        try:
            inner = vars(layers[-1]).get('_original')
        except TypeError:
            inner = None
        if inner is None:
            break
        layers.append(inner)
    return layers


def install_send_input_hook(tracker):
    """给 ctypes.windll.user32.SendInput 套上记账那一层, 返回那一层; 已经套过 (模块重新导入) 就用原来那层。

    跑轴记录已经先套了的话插到它里面、紧贴原函数; 它之后卸下时会把 SendInput 还原成这一层。
    """
    user32 = ctypes.windll.user32
    layers = _layers(user32.SendInput)
    for layer in layers:
        if type(layer).__name__ == '_SendInputHook':
            return layer
    hook = _SendInputHook(layers[-1], tracker)
    if len(layers) == 1:
        user32.SendInput = hook
    else:
        object.__setattr__(layers[-2], '_original', hook)
    return hook


_hook = install_send_input_hook(InjectedInputs())
injected = object.__getattribute__(_hook, '_tracker')


def send_inputs(*inputs):
    """发一串 INPUT, 返回发出去几个。按下/抬起都会记到 injected。"""
    count = len(inputs)
    if not count:
        return 0
    array = (INPUT * count)(*inputs)
    _local.noted = False
    try:
        # 每次现取: 跑轴记录会把 user32.SendInput 换成记录器
        sent = ctypes.windll.user32.SendInput(count, ctypes.byref(array), ctypes.sizeof(INPUT))
        if not _local.noted:
            injected.note_call((count, array, ctypes.sizeof(INPUT)), sent)
    finally:
        _local.noted = False
    return sent


def release_injected(reason=''):
    """松开本进程用 SendInput 按着的所有键和鼠标键 (游戏在不在前台都发, 不然会在整个系统里一直按着)。返回松了几个。"""
    held = injected.take_all()
    if not held:
        return 0
    failed = []
    for token, release in held:
        if send_inputs(release) != 1:
            failed.append(token)
        if token == ('key', ord('F')):
            f_key_watcher.note_self_key_up('f')
    names = ', '.join(token_label(token) for token, _ in held)
    logger.info(f'SendInput: {reason}, 松开还按着的 {len(held)} 个键: {names}')
    if failed:
        logger.error(f'SendInput: 松开 {", ".join(token_label(t) for t in failed)} 没发出去 — 如果卡住了, 手动按一下')
    return len(held)


def _on_executor_paused(paused):
    # 不管用哪种交互方式: 队伍代码自己用 SendInput 按住的 (比如后台输入下奥古斯塔的左键) 也松掉
    if paused:
        release_injected('coco-ww 暂停')


communicate.executor_paused.connect(_on_executor_paused)


def _release_at_exit():
    try:
        release_injected('coco-ww 退出')
    except Exception:
        pass


atexit.register(_release_at_exit)


class SendInput(BaseInteraction):
    """前台输入, 直接调 user32.SendInput。用法和别的交互后端一样, 见模块说明。"""

    # 设置界面「交互方式」的标题和说明 (界面翻译表里没有这个名字, 见 ok/ui/qt/shell/connection.py)
    ui_label = 'SendInput - 前台，coco-ww 自己发系统输入，切出游戏自动松键'
    # 队伍代码可以看这个: True 表示框架自己的 mouse_down/send_key 就是系统输入, 不用再自己调 SendInput
    system_input = True
    BACKGROUND_LOG_INTERVAL = 2.0

    def __init__(self, capture, hwnd_window):
        super().__init__(capture)
        self.hwnd_window = hwnd_window
        self.check_clickable = True
        self._last_background_log = 0.0
        if not is_admin():
            logger.error('前台输入 SendInput 要管理员权限: 游戏是管理员运行的, 没提权发的键会被系统拦掉')
        monitors = getattr(hwnd_window, 'visible_monitors', None)
        if monitors is not None:
            # 换交互方式时旧实例收不到 on_destroy: 在这里松掉它按着的键、摘掉它的回调
            for old in [m for m in monitors if isinstance(m, SendInput)]:
                old.release_all('换了交互方式')
                monitors.remove(old)
            monitors.append(self)

    # ---- 前台判断 / 生命周期 ----

    def clickable(self):
        if not self.check_clickable:
            return True
        try:
            return bool(self.hwnd_window.is_foreground())
        except Exception:
            return False

    def should_capture(self):
        return self.clickable()

    def on_run(self):
        self.hwnd_window.bring_to_front()

    def on_visible(self, visible):
        """HwndWindow 发现游戏切到前台/后台时调。"""
        if not visible:
            self.release_all('游戏切到后台')

    def on_destroy(self):
        self.release_all('coco-ww 退出')
        monitors = getattr(self.hwnd_window, 'visible_monitors', None)
        if monitors and self in monitors:
            monitors.remove(self)

    @staticmethod
    def held_inputs():
        """本进程用 SendInput 按着的键 (token 列表), 包括队伍代码自己调 SendInput 按的。"""
        return injected.held()

    @staticmethod
    def release_all(reason=''):
        """松开本进程用 SendInput 按着的所有键和鼠标键, 包括队伍代码自己按的。返回松了几个。"""
        return release_injected(reason)

    # ---- 底层 ----

    @staticmethod
    def _send(*inputs):
        count = len(inputs)
        sent = send_inputs(*inputs)
        if sent != count:
            logger.error(f'SendInput 只发出 {sent}/{count} 个事件 (没有管理员权限, 或者被安全软件拦了)')
            return False
        return True

    def _foreground_or_log(self, what):
        if self.clickable():
            return True
        now = time.monotonic()
        if now - self._last_background_log >= self.BACKGROUND_LOG_INTERVAL:
            self._last_background_log = now
            logger.info(f'游戏不在前台, 不发{what}')
        return False

    def _press(self, token, press, release, what):
        """按下 (记账在 send_inputs 里); 游戏不在前台就不按。返回按没按。"""
        if not self._foreground_or_log(what):
            return False
        return self._send(press)

    def _release(self, token, release):
        """松开。账上按着的一定发, 哪怕游戏已经不在前台 (不然这个键会在整个系统里一直按着);
        账上没有的 (已经松过了, 或者是保险起见的松键) 只在游戏在前台时发。"""
        if not injected.is_held(token) and not self.clickable():
            return False
        return self._send(release)

    def _key_inputs(self, key):
        """键 -> (记账用的 token, 按下 INPUT, 抬起 INPUT); 认不出返回 None。"""
        if is_mouse_button(key):
            return self._button_inputs(key)
        resolved = resolve_key(key)
        if resolved is None:
            logger.error(f'SendInput: 认不出键 {key!r}, 没发')
            return None
        vk, scan, extended = resolved
        return ('key', vk), key_input(vk, scan, extended, False), key_input(vk, scan, extended, True)

    @staticmethod
    def _button_inputs(key):
        down, up, data = resolve_button(key)
        return ('mouse', down, data), mouse_input(down, data), mouse_input(up, data)

    def _move_to(self, x, y):
        abs_x, abs_y = self.capture.get_abs_cords(x, y)
        ctypes.windll.user32.SetCursorPos(int(abs_x), int(abs_y))

    def _tap(self, token, press, release, down_time, what):
        if not self._press(token, press, release, what):
            return False
        try:
            time.sleep(down_time)
        finally:
            self._release(token, release)
        return True

    # ---- 键盘 ----

    def send_key(self, key, down_time=0.01):
        super().send_key(key, down_time)
        f_key_watcher.note_self_key(key, down_time)
        inputs = self._key_inputs(key)
        if inputs is not None:
            self._tap(*inputs, down_time, f'键 {key}')

    def send_key_down(self, key, activate=True):
        # activate 只有 PostMessage 用得上; 收下这个参数, 写了 activate=False 的队伍代码换到前台也能跑
        f_key_watcher.note_self_key(key)
        inputs = self._key_inputs(key)
        if inputs is not None:
            self._press(*inputs, f'键 {key}')

    def send_key_up(self, key):
        try:
            inputs = self._key_inputs(key)
            if inputs is not None:
                token, _, release = inputs
                self._release(token, release)
        finally:
            f_key_watcher.note_self_key_up(key)

    def input_text(self, text):
        if not self._foreground_or_log('打字'):
            return
        for unit in utf16_units(text):
            self._send(unicode_input(unit, False), unicode_input(unit, True))
            time.sleep(0.01)

    # ---- 鼠标 ----

    def move(self, x, y):
        if self._foreground_or_log('鼠标移动'):
            self._move_to(x, y)

    def click(self, x=-1, y=-1, move_back=False, name=None, down_time=0.01, move=False, key="left"):
        what = f'{key} 点击'
        if not self._foreground_or_log(what):
            return
        old_pos = None
        if move_back:
            try:
                old_pos = win32api.GetCursorPos()
            except Exception:
                old_pos = None
        if x != -1 and y != -1:
            self._move_to(x, y)
            time.sleep(0.02)  # 和 Pynput 一样: 先让游戏看到鼠标到位再按
        self._tap(*self._button_inputs(key), down_time, what)
        if old_pos is not None:
            ctypes.windll.user32.SetCursorPos(int(old_pos[0]), int(old_pos[1]))

    def mouse_down(self, x=-1, y=-1, name=None, key="left"):
        what = f'{key} 按下'
        if not self._foreground_or_log(what):
            return
        if x != -1 and y != -1:
            self._move_to(x, y)
            time.sleep(0.02)
        self._press(*self._button_inputs(key), what)

    def mouse_up(self, key="left"):
        token, _, release = self._button_inputs(key)
        self._release(token, release)

    def scroll(self, x, y, scroll_amount):
        if not self._foreground_or_log('滚轮'):
            return
        if x > 0 and y > 0:
            self._move_to(x, y)
            time.sleep(0.001)
        sign = 1 if scroll_amount > 0 else -1
        for _ in range(abs(int(scroll_amount))):
            self._send(mouse_input(MOUSEEVENTF_WHEEL, sign * WHEEL_DELTA))
            time.sleep(0.001)
        time.sleep(0.02)

    def swipe(self, x1, y1, x2, y2, duration, settle_time=0):
        """duration 单位毫秒。步数照搬 PostMessage/Pynput (duration/100 步、每步 10ms)。"""
        if not self._foreground_or_log('拖动'):
            return
        self._move_to(x1, y1)
        time.sleep(0.1)
        token, press, release = self._button_inputs('left')
        if not self._press(token, press, release, '拖动'):
            return
        try:
            steps = max(1, int(duration / 100))
            for i in range(steps):
                self._move_to(x1 + (x2 - x1) * i / steps, y1 + (y2 - y1) * i / steps)
                time.sleep(0.01)
            self._move_to(x2, y2)
            if settle_time > 0:
                time.sleep(settle_time)
        finally:
            self._release(token, release)


class Pynput(PynputInteraction):

    def send_key(self, key, down_time=0.01):
        f_key_watcher.note_self_key(key, down_time)
        super().send_key(key, down_time)

    def send_key_down(self, key, activate=True):
        # activate 只有 PostMessage 用得上; 收下这个参数, 写了 activate=False 的队伍代码换到前台也能跑
        f_key_watcher.note_self_key(key)
        super().send_key_down(key)

    def send_key_up(self, key):
        try:
            super().send_key_up(key)
        finally:
            f_key_watcher.note_self_key_up(key)
