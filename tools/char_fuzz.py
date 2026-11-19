"""原生角色代码的离线压力测试: 假游戏 + 模拟时钟, 抓死循环/卡住/异常/有人一直轮不到。

不连游戏, 读屏结果全是随机的(会持续一段时间再变), 所以只能验证"不卡、不崩、会切人",
不能验证打得好不好。用法:
    python -X utf8 tools/char_fuzz.py                  # 全部角色 + 常见队伍
    python -X utf8 tools/char_fuzz.py Lucilla Hiyuki   # 只跑含这些角色的队伍
    python -X utf8 tools/char_fuzz.py --team Hiyuki,Lucilla,Suisui --seconds 300 --seeds 5
"""
import argparse
import itertools
import logging
import math
import os
import random
import sys
import traceback
import types

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from src.task.BaseCombatTask import BaseCombatTask, NotInCombatException  # noqa: E402
from src.char import BaseChar as base_char_module  # noqa: E402
from src.char.BaseChar import BaseChar, CharPerformTimeout  # noqa: E402
from src.char.CharFactory import char_dict, _apply_char_config  # noqa: E402

for name in list(logging.root.manager.loggerDict):
    logging.getLogger(name).setLevel(logging.WARNING)
logging.getLogger().setLevel(logging.WARNING)


class Hang(BaseException):
    """模拟时间或调用次数超限: 真游戏里这就是卡死。BaseException 防止被角色代码的 except Exception 吞掉。"""


class Clock:
    def __init__(self):
        self.t = 100000.0

    def time(self):
        return self.t

    def sleep(self, s):
        if s and s > 0:
            self.t += s
        FakeTask.current.tick()


CLOCK = Clock()
fake_time = types.SimpleNamespace(time=CLOCK.time, sleep=CLOCK.sleep, monotonic=CLOCK.time,
                                  perf_counter=CLOCK.time, strftime=lambda *a, **k: '00:00:00')


def patch_time():
    for mod_name, mod in list(sys.modules.items()):
        if mod is None:
            continue
        if mod_name.startswith('src.char.') or mod_name in ('src.task.BaseCombatTask',):
            if getattr(mod, 'time', None) is not None and getattr(mod.time, '__name__', '') == 'time':
                mod.time = fake_time


class FakeBox:
    def __init__(self, name='box', x=100, y=100, w=60, h=30, confidence=0.9):
        self.name = name
        self.x, self.y, self.width, self.height = x, y, w, h
        self.confidence = confidence

    def crop_frame(self, frame):
        rng = FakeTask.current.rng
        return rng.integers(0, 256, size=(max(self.height, 4), max(self.width, 64), 3), dtype=np.uint8)

    def scale(self, *a, **k):
        return self

    def copy(self, *a, **k):
        return self

    def __repr__(self):
        return f'FakeBox({self.name})'


class Flag:
    """一个会持续一段时间再变的随机读屏状态。"""

    def __init__(self, rng, p_on, mean_on, mean_off, frozen=False):
        self.rng = rng
        self.p_on, self.mean_on, self.mean_off = p_on, mean_on, mean_off
        self.value = rng.random() < p_on
        self.until = CLOCK.t + (1e12 if frozen else self._dur())

    def _dur(self):
        mean = self.mean_on if self.value else self.mean_off
        return float(self.rng.exponential(mean)) + 0.05

    def get(self):
        if CLOCK.t >= self.until:
            self.value = self.rng.random() < self.p_on
            self.until = CLOCK.t + self._dur()
        return self.value


class FakeTask:
    current = None
    MAX_CALLS_PER_PERFORM = 400000
    MAX_PERFORM_SECONDS = 150.0

    def __init__(self, seed, combat_seconds, frozen_ratio=0.0):
        FakeTask.current = self
        self.rng = np.random.default_rng(seed)
        self.frozen_ratio = frozen_ratio
        self.flags = {}
        self.chars = [None, None, None]
        self.key_config = {'Resonance Key': 'e', 'Liberation Key': 'r', 'Echo Key': 'q', 'Tool Key': 't'}
        self.char_config = {}
        self.config = {}
        self.debug = False
        self.name = 'fuzz'
        self.in_liberation = False
        self._in_combat = True
        self.skip_combat_check = False
        self.has_lavitator = True
        self.use_liberation = True
        self.can_break = False
        self.perform_watch = None
        self.freeze_durations = []
        self.combat_start = CLOCK.t
        self.combat_end_at = CLOCK.t + combat_seconds
        self.switch_char_time_out = 5
        self.cds = {}
        self.frame = np.zeros((216, 384, 3), dtype=np.uint8)
        self.width = 3840
        self.height = 2160
        self.screen_width = 3840
        self.screen_height = 2160
        self.current_index = 0
        self.pending_switch = None
        self.anim_until = -1
        self.calls = 0
        self.perform_started = CLOCK.t
        self.mouse_is_down = False
        self.keys_down = set()
        self.log = []
        self.in_sleep_check = False

    # ---------- 基础 ----------
    def flag(self, key, p_on=0.35, mean_on=1.2, mean_off=1.5):
        f = self.flags.get(key)
        if f is None:
            f = Flag(self.rng, p_on, mean_on, mean_off, frozen=self.rng.random() < self.frozen_ratio)
            self.flags[key] = f
        return f.get()

    def tick(self, dt=0.0):
        if dt:
            CLOCK.t += dt
        self.calls += 1
        if self.calls > self.MAX_CALLS_PER_PERFORM:
            raise Hang(f'too many calls in one perform ({self.calls})')
        if CLOCK.t - self.perform_started > self.MAX_PERFORM_SECONDS:
            raise Hang(f'perform lasted {CLOCK.t - self.perform_started:.0f}s (fake)')
        if self.pending_switch and CLOCK.t >= self.pending_switch[1]:
            self.current_index = self.pending_switch[0]
            self.pending_switch = None

    def read(self):
        self.tick(0.003)

    def sleep(self, s):
        CLOCK.t += max(0.0, s or 0.0)
        self.tick()
        self.sleep_check()

    def sleep_check(self):
        self.check_perform_watch()
        if self.skip_combat_check:
            return
        if CLOCK.t > self.combat_end_at and not self.in_liberation:
            self.raise_not_in_combat('fake combat end')

    def next_frame(self):
        if not self.in_sleep_check:
            self.check_perform_watch()
        self.tick(1 / 60)
        return self.frame

    def check_combat(self):
        self.check_perform_watch()
        if self.skip_combat_check:
            return
        if CLOCK.t > self.combat_end_at and not self.in_liberation:
            self.raise_not_in_combat('fake combat end')

    def raise_not_in_combat(self, message):
        raise NotInCombatException(message)

    def in_combat(self, target=False):
        return CLOCK.t <= self.combat_end_at

    def wait_until(self, condition, time_out=0, pre_action=None, post_action=None, settle_time=-1,
                   raise_if_not_found=False):
        if time_out == 0:
            time_out = 10
        start = CLOCK.t
        while True:
            if pre_action is not None:
                pre_action()
            self.tick(1 / 60)
            result = condition()
            if result:
                return result
            if post_action is not None:
                post_action()
            if CLOCK.t - start >= time_out:
                return None

    def log_info(self, *a, **k):
        pass

    log_debug = log_error = log_warning = log_info

    def info_set(self, *a, **k):
        pass

    def tr(self, s):
        return s

    def screenshot(self, *a, **k):
        pass

    def draw_boxes(self, *a, **k):
        pass

    # ---------- 输入 ----------
    def send_key(self, key, down_time=0.02, interval=-1, after_sleep=0):
        key = str(key)
        self.tick(0.002)
        if key in ('1', '2', '3'):
            idx = int(key) - 1
            if idx < len(self.chars) and self.chars[idx] is not None and self.anim_until < CLOCK.t:
                self.pending_switch = (idx, CLOCK.t + 0.08)
        elif key == self.key_config['Liberation Key']:
            if self.flag(('lib_anim', self.current_index), 0.6, 2, 2):
                self.anim_until = CLOCK.t + float(self.rng.uniform(1.0, 3.0))
        elif key == self.key_config['Resonance Key']:
            if self.flag(('res_anim', self.current_index), 0.1, 2, 6):
                self.anim_until = CLOCK.t + float(self.rng.uniform(0.5, 1.5))
        if after_sleep:
            self.sleep(after_sleep)
        return True

    def send_key_down(self, key, after_sleep=0):
        self.keys_down.add(str(key))
        self.tick(0.002)
        if after_sleep:
            self.sleep(after_sleep)

    def send_key_up(self, key, after_sleep=0):
        self.keys_down.discard(str(key))
        self.tick(0.002)
        if after_sleep:
            self.sleep(after_sleep)

    def release_held_inputs(self):
        held = (self.mouse_is_down, set(self.keys_down))
        self.mouse_is_down = False
        self.keys_down.clear()
        return held

    def mouse_down(self, *a, **k):
        self.mouse_is_down = True
        self.tick(0.002)

    def mouse_up(self, *a, **k):
        self.mouse_is_down = False
        self.tick(0.002)

    def click(self, *args, **kwargs):
        self.tick(0.01)
        after = kwargs.get('after_sleep', 0)
        if after:
            self.sleep(after)
        return True

    def middle_click(self, *a, **k):
        self.tick(0.01)
        after = k.get('after_sleep', 0)
        if after:
            self.sleep(after)

    def middle_click_relative(self, *a, **k):
        self.tick(0.01)

    def jump(self, after_sleep=0.01):
        self.tick(0.01)
        if after_sleep:
            self.sleep(after_sleep)

    # ---------- 读屏 ----------
    def in_team(self):
        self.read()
        if CLOCK.t < self.anim_until:
            return False, -1, 3
        return True, self.current_index, len([c for c in self.chars if c])

    def in_team_and_world(self):
        return self.in_team()[0]

    def wait_in_team_and_world(self, time_out=10, raise_if_not_found=True):
        return self.wait_until(self.in_team_and_world, time_out=time_out)

    def find_one(self, feature_name=None, box=None, threshold=0.6, template=None, **kwargs):
        self.read()
        key = feature_name if isinstance(feature_name, str) else 'template'
        if self.flag(('find', key, self.current_index)):
            return FakeBox(key, confidence=threshold + 0.05)
        return None

    def find_feature(self, feature_name=None, box=None, threshold=0.6, **kwargs):
        self.read()
        key = feature_name if isinstance(feature_name, str) else 'template'
        n = 0
        if self.flag(('feat', key, self.current_index)):
            n = int(self.rng.integers(1, 4))
        return [FakeBox(key, x=100 + 80 * i) for i in range(n)]

    def find_best_match_in_box(self, box, names, threshold=0.6, **kwargs):
        self.read()
        names = list(names)
        if self.flag(('best', tuple(names), self.current_index), 0.6):
            return FakeBox(names[int(self.rng.integers(0, len(names)))])
        return None

    def calculate_color_percentage(self, color, box):
        self.read()
        name = getattr(box, 'name', 'box')
        if self.flag(('color', name, self.current_index)):
            return float(self.rng.uniform(0.1, 0.9))
        return float(self.rng.uniform(0.0, 0.04))

    def box_of_screen_scaled(self, *args, name='box', **kwargs):
        return FakeBox(name)

    def box_of_screen(self, *args, name='box', **kwargs):
        return FakeBox(name)

    def get_box_by_name(self, name):
        return FakeBox(name)

    def get_feature_by_name(self, name):
        return types.SimpleNamespace(mat=np.zeros((40, 40, 3), dtype=np.uint8), name=name)

    def get_target_names(self):
        return 'has_target', 'no_target'

    def has_target(self, *a, **k):
        return self.flag('has_target', 0.85)

    def has_short_action(self):
        return self.flag('short_action')

    def find_mouse_forte(self):
        self.read()
        return FakeBox('mouse_forte') if self.flag(('mouse_forte', self.current_index), 0.3) else None

    def find_e_forte(self):
        self.read()
        return FakeBox('e_forte') if self.flag(('e_forte', self.current_index), 0.25) else None

    def has_cd(self, box_name, char_index=None):
        self.read()
        idx = self.current_index if char_index is None else char_index
        return self.flag(('cd', box_name, idx), 0.5, 3, 2)

    def get_cd(self, box_name, char_index=None):
        return float(self.rng.uniform(0, 8)) if self.has_cd(box_name, char_index) else 0.0

    def available(self, name, check_color=True, check_cd=True):
        if check_cd and self.has_cd(name):
            return False
        return (not check_color) or self.flag(('avail', name, self.current_index), 0.5)

    def box_highlighted(self, name):
        return 1 if self.flag(('avail', name, self.current_index), 0.5) else 0

    def get_current_con(self):
        self.read()
        if self.flag(('con_full', self.current_index), 0.25, 1.5, 3):
            return 1
        return float(self.rng.uniform(0, 0.95))

    def is_con_full(self):
        return self.get_current_con() == 1

    def check_f_break(self):
        return self.flag('f_break', 0.1, 1, 8)

    def f_break(self):
        if self.check_f_break():
            self.sleep(float(self.rng.uniform(0.5, 2)))
            return True
        return False

    def is_open_world_auto_combat(self):
        return False

    def in_world(self):
        return True

    def has_char(self, char_cls):
        for char in self.chars:
            if isinstance(char, char_cls):
                return char

    def get_current_char(self, raise_exception=False):
        for char in self.chars:
            if char and char.is_current_char:
                return char
        return None

    def _ensure_ring_index(self):
        char = self.get_current_char()
        if char and char.ring_index < 0:
            char.ring_index = 0
        return char.ring_index if char else 0

    def get_known_ring_index(self, char):
        return char.ring_index

    def load_chars(self):
        return True

    def update_lib_portrait_icon(self):
        pass

    def get_resonance_key(self):
        return self.key_config['Resonance Key']

    def get_liberation_key(self):
        return self.key_config['Liberation Key']

    def get_echo_key(self):
        return self.key_config['Echo Key']

    def add_freeze_duration(self, start, duration=-1.0, freeze_time=0.1):
        pass

    def time_elapsed_accounting_for_freeze(self, start, intro_motion_freeze=False):
        if start < 0:
            return 10000
        return CLOCK.t - start


# 从真的 BaseCombatTask 借切人逻辑和硬超时检查
for _name in ['switch_next_char', '_choose_switch_target', '_oldest_switch_target', '_unbuffed_support_target',
              '_switch_rule_3_target', '_target_has_switch_cd', '_log_switch_candidate', '_log_switch_choice',
              '_buff_remaining', '_lowest_buff_remaining_target', '_unbuffed_non_main_target',
              '_choose_intro_switch_target', '_choose_switch_target_by_buff_time', '_apply_intro_flags',
              '_starvation_guard_enabled', '_starving_priority', 'check_perform_watch',
              'STARVE_NORMAL_SECONDS', 'STARVE_MUST_SECONDS', 'STARVE_PRIORITY', 'MAIN_DPS_MAX_WAIT',
              '_off_field_seconds', '_main_dps_waiting_too_long']:
    setattr(FakeTask, _name, BaseCombatTask.__dict__[_name])
FakeTask._log_switch_candidate = lambda self, char, priority: _trace_candidate(self, char, priority)
FakeTask._log_switch_choice = lambda self, c, t, h, r: _trace_choice(self, c, t, h, r)


def make_char(task, cls_name, index):
    info = None
    for key, value in char_dict.items():
        if value['cls'].__name__ == cls_name:
            info = value
            break
    if info is None:
        raise KeyError(cls_name)
    cls = info['cls']
    char = cls(task, index, char_name=info['canonical_name'], confidence=0.95,
               ring_index=info.get('ring_index', -1), char_type=info.get('char_type'),
               buff_time=info.get('buff_time'))
    _apply_char_config(task, char, info)
    return char


TRACE = False


def _trace_candidate(self, char, priority):
    if TRACE:
        print(f'      cand {char.name:<12} prio={int(priority):<4} buff={char.has_buff()} '
              f'off={self._off_field_seconds(char):.0f}s')


def _trace_choice(self, current_char, target, has_intro, reason):
    if TRACE:
        print(f'      pick {target.name} intro={has_intro} reason={reason}')
    return target


def run_team(team, seed, seconds, frozen_ratio=0.0):
    task = FakeTask(seed, seconds, frozen_ratio)
    chars = [make_char(task, name, i) for i, name in enumerate(team)]
    task.chars = chars
    for c in chars:
        c.reset_state()
    chars[0].is_current_char = True
    stats = {c.name: {'performs': 0, 'max_field': 0.0, 'timeouts': 0} for c in chars}
    problems = []
    switches = []
    while CLOCK.t < task.combat_end_at:
        char = task.get_current_char()
        if char is None:
            problems.append(('no current char', ''))
            break
        task.calls = 0
        task.perform_started = CLOCK.t
        before = CLOCK.t
        stats[char.name]['performs'] += 1
        try:
            orig = char.on_perform_timeout

            def flag_timeout(c=char, o=orig):
                stats[c.name]['timeouts'] += 1
                o()

            char.on_perform_timeout = flag_timeout
            char.perform()
        except NotInCombatException:
            break
        except Hang as e:
            problems.append((f'HANG {char.name}', str(e)))
            break
        except Exception as e:
            problems.append((f'EXC {char.name}', ''.join(traceback.format_exception(e)[-6:])))
            break
        finally:
            try:
                del char.on_perform_timeout
            except AttributeError:
                pass
        field = CLOCK.t - before
        stats[char.name]['max_field'] = max(stats[char.name]['max_field'], field)
        now_char = task.get_current_char()
        if TRACE:
            print(f'  {CLOCK.t - task.combat_start:6.1f}s {char.name:<12} field {field:5.1f}s '
                  f'intro={char.has_intro} -> {now_char.name if now_char else None}')
        if now_char is not None and now_char is not char:
            switches.append((CLOCK.t, now_char.name))
        if task.mouse_is_down:
            problems.append((f'MOUSE_LEFT_DOWN {char.name}', 'mouse still down after perform'))
            task.mouse_is_down = False
        stuck_keys = task.keys_down - set()
        if stuck_keys:
            problems.append((f'KEY_LEFT_DOWN {char.name}', str(stuck_keys)))
            task.keys_down.clear()
    # 饿死检查: 每个人最长离场时间
    last_seen = {c.name: task.combat_start for c in chars}
    last_seen[chars[0].name] = task.combat_start
    longest = {c.name: 0.0 for c in chars}
    for t, name in switches:
        for c in chars:
            if c.name != name:
                continue
            longest[name] = max(longest[name], t - last_seen[name])
            last_seen[name] = t
    end = min(CLOCK.t, task.combat_end_at)
    for c in chars:
        longest[c.name] = max(longest[c.name], end - last_seen[c.name] if c.name != (switches[-1][1] if switches else chars[0].name) else 0)
    return stats, problems, longest


def all_native_names():
    names = sorted({v['cls'].__name__ for v in char_dict.values()})
    return names


STANDARD_TEAMS = [
    'Aemeath,Denia,Chisa', 'Aemeath,Linnai,Mornye', 'Hiyuki,Lucilla,Suisui', 'Hiyuki,Lucilla,Chisa',
    'Cartethyia,Ciaccona,Chisa', 'Augusta,Iuno,ShoreKeeper', 'Galbrena,Lupa,Mornye', 'Hsin,Suoming,ShoreKeeper',
    'Cantarella,Mornye,Phrolova', 'Cantarella,Douling,Phrolova', 'Changli,Hiyuki,Jinhsi', 'Qingxiao,Denia,Mornye',
    'Carlotta,Zhezhi,Douling', 'Lucy,Rebecca,ShoreKeeper', 'Lucy,Rebecca,Verina', 'Lucy,Rebecca,Mornye',
    'Iuno,JingRan,ShoreKeeper', 'Changli,Lupa,Brant', 'Zani,Phoebe,Verina', 'Xigelika,Qiuyuan,ShoreKeeper',
    'Galbrena,Qiuyuan,ShoreKeeper', 'YangYangSp,Chisa,Suisui', 'Suoming,Sanhua,Verina', 'Cartethyia,Ciaccona,Rover',
    'Cartethyia,Ciaccona,Youhu', 'Hsin,Douling,Suisui', 'Hsin,Rover,Suisui', 'Hsin,Jinhsi,ShoreKeeper',
    'Changli,Jinhsi,Verina', 'Chixia,Hiyuki,Youhu', 'Douling,Lucy,Rebecca', 'Camellya,Sanhua,ShoreKeeper',
    'Jinhsi,Zhezhi,Verina', 'Jiyan,Mortefi,Verina', 'Encore,Sanhua,Baizhi', 'Roccia,Cantarella,Phrolova',
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('names', nargs='*')
    ap.add_argument('--team', action='append')
    ap.add_argument('--seconds', type=float, default=180)
    ap.add_argument('--seeds', type=int, default=3)
    ap.add_argument('--seed', type=int, default=None, help='只跑这一个 seed')
    ap.add_argument('--trace', action='store_true', help='逐次打印上场/切人')
    ap.add_argument('--solo', action='store_true', help='每个角色配两个普通队友各跑一遍')
    ap.add_argument('--frozen', type=float, default=0.5,
                    help='奇数 seed 里这个比例的读屏结果整局不变(模拟某个判据失灵/一直成立)')
    args = ap.parse_args()
    patch_time()
    teams = []
    if args.team:
        teams = [t.split(',') for t in args.team]
    else:
        teams = [t.split(',') for t in STANDARD_TEAMS]
        fillers = [('Verina', 'Sanhua'), ('ShoreKeeper', 'Mortefi'), ('Baizhi', 'Yangyang')]
        if args.solo or not args.names:
            for i, name in enumerate(all_native_names()):
                a, b = fillers[i % len(fillers)]
                if name in (a, b):
                    a, b = 'Taoqi', 'Danjin'
                teams.append([name, a, b])
        if args.names:
            teams = [t for t in teams if any(n in t for n in args.names)]
    global TRACE
    TRACE = args.trace
    total_problems = 0
    for team in teams:
        for seed in ([args.seed] if args.seed is not None else range(args.seeds)):
            CLOCK.t = 100000.0
            try:
                stats, problems, longest = run_team(team, seed, args.seconds,
                                                    args.frozen if seed % 2 else 0.0)
            except KeyError as e:
                print(f'skip {team}: unknown {e}')
                break
            starving = {k: round(v) for k, v in longest.items() if v > 60}
            tag = 'OK '
            if problems or starving:
                tag = 'BAD'
                total_problems += 1
            summary = ' '.join(f"{n}:{s['performs']}x/max{s['max_field']:.0f}s/to{s['timeouts']}"
                               for n, s in stats.items())
            print(f'{tag} {",".join(team)} seed{seed} | {summary}' + (f' | starving {starving}' if starving else ''))
            for p, detail in problems:
                print(f'    {p}: {detail.strip()[:1500]}')
    print(f'done, {total_problems} bad runs')


if __name__ == '__main__':
    main()
