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
# 按下后超过「等待时间 + 这个数」还没用上就作废: 跑图、整理背包时按的 F 不该过一会儿突然开打
F_EXTRA_LIFETIME = 10.0
GRACE_LOG_INTERVAL = 10.0
GRACE_TARGET_INTERVAL = 0.3


class FKeyWatcher:
    """记下游戏在前台时物理 F 键最近一次按下的时刻。整个进程只起一个线程。

    只读 GetAsyncKeyState 的高位: 低位整个进程共享、读一次就清零, 和别的读者会互相吃掉。
    coco-ww 自己发的键走 PostMessage, 不改变按键状态, 所以拾取、处决等脚本按的 F 不会记进来。
    """

    def __init__(self):
        self.last_press = -1.0
        self.is_game_foreground = None
        self._thread = None
        self._lock = threading.Lock()

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
            self.last_press = time.time()
            logger.debug('physical F pressed')
        return down

    def _foreground(self):
        try:
            return bool(self.is_game_foreground and self.is_game_foreground())
        except Exception:
            return False


f_key_watcher = FKeyWatcher()


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
