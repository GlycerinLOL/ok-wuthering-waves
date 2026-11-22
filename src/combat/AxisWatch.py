"""持宏者队伍代码共用的两个监视线程。

原来每份持宏者代码都自带一份逐字相同的实现(20 多份), coco-ww 收到这里, 队伍代码优先从这里导入:
    try:
        from src.combat.AxisWatch import f_settings, start_enable_watch, start_f_watch
    except ImportError:
        ...自带的同一份...
分享出去有人用原版 OKWW(没有这个模块), 所以队伍代码必须带 except ImportError 的兜底, 不能裸导入。
改这里的逻辑时, 队伍代码里那份兜底不会跟着变(只在原版上跑), 要一致就一起改。
函数名、参数、写到 task 上的属性名都和原来一样, 所以还自带旧实现的队伍代码(比如工坊里别人传的)
可以和这里混用: 线程按 task 上的标记只起一次, 谁先调用谁起。

写到 task 上的属性:
    _ww_task_restarted_at  自动战斗开关从关到开的时刻
    _f_last_press          物理 F 最近一次按下的时刻, 启动轴靠它重新武装
    _f_pressed_at          同一时刻, 给「按 F 开始战斗」(AutoCombatTask.last_f_press) 用
"""
import ctypes
import threading
import time

F_VK = 0x46              # 'F'
F_POLL_INTERVAL = 0.05   # 一次按键按住的时间远大于这个, 不会漏采
F_LOG_DEBOUNCE = 1.0     # 按住不放时别刷日志
F_TASK_NAME = 'FStartCombatTask'
ENABLE_POLL_INTERVAL = 0.2
# 队伍代码跑宏时会把其中一个置真: 宏正在跑 = 正在战斗, 这时按的 F 是玩家手动处决, 不是重开副本
MACRO_BUSY_FLAGS = ('_three_fire_macro_busy', '_ww_macro_busy')


def f_settings(task):
    """读界面上的(开关, F 之后等待秒数)。找不到那张卡片就返回默认值(开, 0 秒)。"""
    executor = getattr(task, 'executor', None)
    for group in ('trigger_tasks', 'onetime_tasks'):
        for other in getattr(executor, group, None) or ():
            if type(other).__name__ == F_TASK_NAME:
                try:
                    return bool(other.enabled), float(getattr(other, 'f_start_delay', 0) or 0)
                except Exception:
                    return True, 0.0
    return True, 0.0


def start_enable_watch(task, logger=None):
    """盯 task.enabled 的 False->True 跳变, 把时刻记到 task._ww_task_restarted_at。每个 task 只启一次。

    玩家手动开关自动战斗必须从启动轴重跑, 而手动停任务不走 combat_end(), 只有独立线程看得到这次跳变。
    """
    if getattr(task, '_ww_enable_watch_started', False):
        return
    task._ww_enable_watch_started = True
    if not hasattr(task, '_ww_task_restarted_at'):
        task._ww_task_restarted_at = -1

    def watch():
        was_on = None
        while True:
            try:
                now_on = bool(getattr(task, 'enabled', False))
                if was_on is False and now_on:
                    task._ww_task_restarted_at = time.time()
                    if logger:
                        logger.info('auto combat re-enabled -> opener re-armed')
                was_on = now_on
                time.sleep(ENABLE_POLL_INTERVAL)
            except Exception:
                return

    threading.Thread(target=watch, name='coco_ww_enable_watch', daemon=True).start()


def start_f_watch(task, logger=None):
    """把【物理】F 键按下的墙钟时间记到 task._f_last_press。每个 task 只启一次。"""
    if getattr(task, '_f_watch_started', False):
        return
    task._f_watch_started = True
    if not hasattr(task, '_f_last_press'):
        # -1 = 从没按过, 跟 last_opener 的"没跑过"对齐(给 0 的话开局第一次判定恒成立)
        task._f_last_press = -1

    def watch():
        try:
            user32 = ctypes.windll.user32
            user32.GetAsyncKeyState.restype = ctypes.c_short
        except Exception as e:
            task._f_watch_started = False
            if logger:
                logger.error(f'f watch: GetAsyncKeyState unavailable ({e}), '
                             f'the "press F to re-arm the opener" shortcut stays off')
            return
        last_log = 0
        was_down = False
        while True:
            try:
                # 只读高位: 低位读一次就清而且整个进程共享, 会和别的读者互相吃掉
                down = bool(user32.GetAsyncKeyState(F_VK) & 0x8000)
                if down and not was_down and f_settings(task)[0]:
                    now = time.time()
                    if any(getattr(task, flag, False) for flag in MACRO_BUSY_FLAGS):
                        if logger and now - last_log >= F_LOG_DEBOUNCE:
                            last_log = now
                            logger.info('physical F pressed while the macro is running '
                                        '(manual execution) - opener NOT re-armed')
                        was_down = down
                        time.sleep(F_POLL_INTERVAL)
                        continue
                    task._f_last_press = now
                    task._f_pressed_at = now
                    if logger and now - last_log >= F_LOG_DEBOUNCE:
                        last_log = now
                        logger.info('physical F pressed, opener re-armed')
                was_down = down
                time.sleep(F_POLL_INTERVAL)
            except Exception:
                return

    threading.Thread(target=watch, name='coco_ww_f_watch', daemon=True).start()
