"""按 F 开始战斗: 挑战/副本里按下物理 F 后, 不等锁到目标或血条出现就直接进战斗。

原来是插件 f_start_combat (给 CombatCheck/AutoCombatTask 打补丁), coco-ww 并进主程序:
- 设置在「自动战斗」卡片里 (AutoCombatTask.F_START_KEY / F_DELAY_KEY / F_GRACE_KEY)
- 判定在 AutoCombatTask.should_start_combat_by_key / start_combat_by_key
- 没锁到目标的保护期在 CombatCheck.f_grace_hold
这里放两样东西: 读物理 F 键的后台线程, 和给旧队伍代码用的兼容壳 FStartCombatTask。
"""
import ctypes
import threading
import time

from ok import Logger, TriggerTask

logger = Logger.get_logger(__name__)

F_VK = 0x46
F_POLL_INTERVAL = 0.05
# 前台输入 (SendInput) 下 coco-ww 自己按的 F 也会改按键状态: 自己按着和松开后这么久之内的上升沿不算
SELF_F_GRACE = 0.2
# 按下后超过「等待时间 + 这个数」还没用上就作废: 跑图、整理背包时按的 F 不该过一会儿突然开打
F_EXTRA_LIFETIME = 10.0
GRACE_LOG_INTERVAL = 10.0
GRACE_TARGET_INTERVAL = 0.3


class FKeyWatcher:
    """记下游戏在前台时物理 F 键最近一次按下的时刻。整个进程只起一个线程。

    只读 GetAsyncKeyState 的高位: 低位整个进程共享、读一次就清零, 和别的读者会互相吃掉。
    后台输入 (PostMessage) 不改按键状态, 拾取、处决等脚本按的 F 本来就读不到。
    前台输入 (SendInput) 会改按键状态, 所以它每按一次 F 都先报到 note_self_key, 那段时间里的上升沿不算。
    """

    def __init__(self):
        self.last_press = -1.0
        self.is_game_foreground = None
        self._thread = None
        self._lock = threading.Lock()
        self._self_held = False
        self._self_until = 0.0

    @staticmethod
    def _is_f(key):
        return str(key).lower() == 'f'

    def note_self_key(self, key, down_time=None):
        """coco-ww 自己要按 F 了 (发键之前调)。down_time=None 表示按住, 等 note_self_key_up 再放。"""
        if not self._is_f(key):
            return
        with self._lock:
            if down_time is None:
                self._self_held = True
            else:
                self._self_until = max(self._self_until, time.monotonic() + float(down_time) + SELF_F_GRACE)

    def note_self_key_up(self, key):
        """coco-ww 自己按住的 F 松开了 (发键之后调)。"""
        if not self._is_f(key):
            return
        with self._lock:
            self._self_held = False
            self._self_until = max(self._self_until, time.monotonic() + SELF_F_GRACE)

    def _self_pressing(self):
        with self._lock:
            return self._self_held or time.monotonic() < self._self_until

    def start(self, is_game_foreground):
        with self._lock:
            self.is_game_foreground = is_game_foreground
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._watch, name='f_key_start_watch', daemon=True)
            self._thread.start()

    def _watch(self):
        try:
            get_key_state = ctypes.windll.user32.GetAsyncKeyState
            get_key_state.restype = ctypes.c_short
        except Exception as e:
            logger.error(f'F 开始战斗: 读不了键盘 ({e}), 按 F 开打不可用')
            return
        was_down = False
        while True:
            try:
                down = bool(get_key_state(F_VK) & 0x8000)
                was_down = self.on_sample(down, was_down)
            except Exception as e:
                logger.error(f'F 开始战斗: 读键线程出错, 停止 ({e})')
                return
            time.sleep(F_POLL_INTERVAL)

    def on_sample(self, down, was_down):
        """只认上升沿; 返回这次的按键状态给下一轮比较。"""
        if down and not was_down and self._foreground():
            if self._self_pressing():
                logger.debug('F pressed by coco-ww itself, ignored')
            else:
                self.last_press = time.time()
                logger.debug('physical F pressed')
        return down

    def _foreground(self):
        try:
            return bool(self.is_game_foreground and self.is_game_foreground())
        except Exception:
            return False


f_key_watcher = FKeyWatcher()


def _filter_self_f(original, watcher):
    """包一层 GetAsyncKeyState: coco-ww 自己正按着 F 时, F 读成没按。别的键、别的时候原样返回。"""

    def GetAsyncKeyState(vk):
        state = original(vk)
        if state and getattr(vk, 'value', vk) == F_VK and watcher._self_pressing():
            return 0
        return state

    GetAsyncKeyState.coco_self_f_filter = True
    return GetAsyncKeyState


def install_self_f_filter():
    """让进程里所有 ctypes.windll.user32.GetAsyncKeyState 都看不到 coco-ww 自己按的 F。

    20 多份持宏者队伍代码和 AxisWatch 都有自己的读 F 线程, 前提是「宏自己按的 F 走 PostMessage, 读不到」。
    前台输入 (SendInput) 下这个前提不成立, 它们会把拾取、破韧按的 F 当成玩家按的 (实机 2026-11-20 03:11 见过)。
    它们都是每次调用时从共享的 ctypes.windll.user32 上取这个函数, 所以在这里换掉一次就都管到了, 队伍代码不用改。
    后台 PostMessage 下没人报「自己在按 F」, 这层包装什么都不做。
    """
    try:
        user32 = ctypes.windll.user32
        current = user32.GetAsyncKeyState
    except Exception as e:
        logger.warning(f'F 键过滤装不上 ({e}), 前台输入下自己按的 F 可能被当成玩家按的')
        return False
    if getattr(current, 'coco_self_f_filter', False):
        return True
    current.restype = ctypes.c_short
    current.argtypes = (ctypes.c_int,)
    user32.GetAsyncKeyState = _filter_self_f(current, f_key_watcher)
    return True


install_self_f_filter()


def float_config(config, key, upper):
    """界面上的秒数: 夹到 [0, upper], 填坏了当 0。"""
    try:
        return min(upper, max(0.0, float(config.get(key, 0) or 0)))
    except (TypeError, ValueError):
        return 0.0


class FStartCombatTask(TriggerTask):
    """兼容壳, 任务页上不显示。

    持宏者的队伍代码(本机 20 多份, 工坊里的也一样)按类名 'FStartCombatTask' 在执行器里找这张卡片,
    读 enabled 和 f_start_delay 决定「按 F 重跑启动轴」要不要生效、等多久; 找不到就当成 (开, 0 秒)。
    卡片并进自动战斗之后留这个壳把设置转过去, 那些队伍代码一行不用改。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = 'F 开始战斗 (设置在自动战斗里)'
        self.description = '兼容旧队伍代码用, 设置项在「自动战斗」里'
        self.default_config['_enabled'] = True
        self.trigger_interval = 60
        self.visible = False

    def _auto_combat(self):
        from src.task.AutoCombatTask import AutoCombatTask
        try:
            return self.get_task_by_class(AutoCombatTask)
        except Exception:
            return None

    @property
    def enabled(self):
        task = self._auto_combat()
        return bool(task is not None and task.f_start_enabled)

    @property
    def f_start_delay(self):
        task = self._auto_combat()
        return task.f_start_delay if task is not None else 0.0

    @property
    def grace_limit(self):
        task = self._auto_combat()
        return task.f_grace_limit if task is not None else 0.0

    def run(self):
        # 必须返回假值: 返回真会让执行器重排触发任务并 reset_scene
        return None
