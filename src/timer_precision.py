"""让本进程的短等待按毫秒走, 不被 Windows 默认 15.6ms 的定时器粒度吞掉。

框架的 sleep 最后落在 threading.Condition.wait -> WaitForSingleObjectEx, 受系统定时器粒度限制。
默认 15.6ms 时实测: sleep(0.01) 睡 15.6ms, sleep(0.02) 睡 31ms, sleep(0.05) 睡 62ms,
轴里的短等待全被拉长 (time.sleep 在 Python 3.11+ 自己用高精度定时器, 不受影响)。

1. timeBeginPeriod(1): 本进程的定时器粒度提到 1ms。
2. Windows 11 起, 窗口被完全挡住或最小化的进程, 系统可以无视它的 timeBeginPeriod 请求。
   打战斗时 coco-ww 正好被全屏游戏挡着, 所以用 SetProcessInformation(ProcessPowerThrottling)
   关掉「忽略定时器精度」这项节能。
3. 顺带关掉 EcoQoS 降速: 被挡住的后台进程在大小核 CPU 上可能被放到小核、低频跑, 识图会变慢。

全部是对本进程的设置, 进程退出就失效, 不改系统设置。
"""
import ctypes
import statistics
import threading
import time

from ok import Logger

logger = Logger.get_logger(__name__)

# PROCESS_INFORMATION_CLASS::ProcessPowerThrottling
_PROCESS_POWER_THROTTLING = 4
_PROCESS_POWER_THROTTLING_CURRENT_VERSION = 1
_PROCESS_POWER_THROTTLING_EXECUTION_SPEED = 0x1
_PROCESS_POWER_THROTTLING_IGNORE_TIMER_RESOLUTION = 0x4

# 自检: 让 Condition.wait 等这么久, 看实际等了多久
_PROBE_WAIT = 0.002
_PROBE_SAMPLES = 5
# 实测超过这个值就当没生效 (1ms 粒度下 wait(2ms) 实测约 2~3.5ms, 15.6ms 粒度下约 15.6ms)
_PROBE_BAD_MS = 6.0
# 进战斗时的自检最多这么久做一次, 免得频繁进出战斗刷日志
_REPORT_MIN_INTERVAL = 60.0


class _PowerThrottlingState(ctypes.Structure):
    _fields_ = [('Version', ctypes.c_ulong),
                ('ControlMask', ctypes.c_ulong),
                ('StateMask', ctypes.c_ulong)]


_lock = threading.Lock()
_applied = False
_last_report = 0.0
# 最近一次实测 (ms, 哪次量的, time.time()), 详细面板显示用
_last_measurement = None


def last_measurement():
    """最近一次实测: (wait(2ms) 实际等了多少 ms, 哪次量的, 时刻), 还没量过返回 None。"""
    return _last_measurement


def _remember(measured, tag):
    global _last_measurement
    _last_measurement = (measured, tag, time.time())


def measure_wait_ms(wait=_PROBE_WAIT, samples=_PROBE_SAMPLES):
    """用框架 sleep 同款的 Condition.wait 量一下实际等待时长, 返回中位数 (ms)。"""
    condition = threading.Condition()
    results = []
    with condition:
        for _ in range(samples):
            start = time.perf_counter()
            condition.wait(wait)
            results.append((time.perf_counter() - start) * 1000)
    return statistics.median(results)


def _opt_out_power_throttling():
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    set_info = kernel32.SetProcessInformation
    set_info.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong]
    set_info.restype = ctypes.c_int
    get_current_process = kernel32.GetCurrentProcess
    get_current_process.restype = ctypes.c_void_p

    # ControlMask 里的位由我们说了算, StateMask 对应位为 0 = 不节流
    state = _PowerThrottlingState(
        _PROCESS_POWER_THROTTLING_CURRENT_VERSION,
        _PROCESS_POWER_THROTTLING_EXECUTION_SPEED | _PROCESS_POWER_THROTTLING_IGNORE_TIMER_RESOLUTION,
        0)
    if set_info(get_current_process(), _PROCESS_POWER_THROTTLING, ctypes.byref(state), ctypes.sizeof(state)):
        return True
    # Windows 10 不认 IGNORE_TIMER_RESOLUTION 这一位 (Win10 本来也不会因窗口被挡而忽略精度), 只关降速
    state.ControlMask = _PROCESS_POWER_THROTTLING_EXECUTION_SPEED
    if set_info(get_current_process(), _PROCESS_POWER_THROTTLING, ctypes.byref(state), ctypes.sizeof(state)):
        return True
    raise OSError(f'SetProcessInformation 失败 ({ctypes.get_last_error()})')


def apply():
    """打开 1ms 定时器并关掉本进程的节能降速。重复调用只生效一次。"""
    global _applied
    with _lock:
        if _applied:
            return
        _applied = True
    before = measure_wait_ms()
    notes = []
    try:
        if ctypes.WinDLL('winmm').timeBeginPeriod(1) != 0:
            notes.append('timeBeginPeriod 失败')
    except Exception as e:
        notes.append(f'timeBeginPeriod 出错: {e}')
    try:
        _opt_out_power_throttling()
    except Exception as e:
        notes.append(f'关节能降速出错: {e}')
    after = measure_wait_ms()
    _remember(after, '启动')
    message =f'计时精度: wait({_PROBE_WAIT * 1000:.0f}ms) 实测 {before:.1f}ms -> {after:.1f}ms'
    if notes:
        message += ' (' + '; '.join(notes) + ')'
    if after > _PROBE_BAD_MS:
        logger.warning(message + ', 短等待仍会被拉长')
    else:
        logger.info(message)


def report_async(tag):
    """后台线程里再量一次 (不占执行器), 用来确认被游戏挡住时精度还在。"""
    global _last_report
    now = time.monotonic()
    with _lock:
        if now - _last_report < _REPORT_MIN_INTERVAL:
            return
        _last_report = now

    def run():
        try:
            measured = measure_wait_ms()
        except Exception as e:
            logger.warning(f'计时精度自检出错 ({tag}): {e}')
            return
        _remember(measured, tag)
        if measured > _PROBE_BAD_MS:
            logger.warning(f'计时精度自检 ({tag}): wait({_PROBE_WAIT * 1000:.0f}ms) 实测 {measured:.1f}ms, '
                           f'短等待会被拉长')
        else:
            logger.info(f'计时精度自检 ({tag}): wait({_PROBE_WAIT * 1000:.0f}ms) 实测 {measured:.1f}ms')

    threading.Thread(target=run, name='timer_precision_probe', daemon=True).start()
