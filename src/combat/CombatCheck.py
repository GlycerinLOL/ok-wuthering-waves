import re
import time

import cv2
import win32api

from ok import find_boxes_by_name, Logger, calculate_color_percentage, TaskDisabledException
from ok import color_range_to_bound, find_color_rectangles, get_mask_in_color_range, is_pure_black
from src import text_white_color
from src.Labels import Labels
from src.char.Roccia import Roccia
from src.combat.FKeyStart import GRACE_LOG_INTERVAL, GRACE_TARGET_INTERVAL
from src.task.BaseWWTask import BaseWWTask

logger = Logger.get_logger(__name__)

# 结束条件(刷4C=画面里有声骸)要隔这么久再成立一次才算打完
END_CONFIRM_SECONDS = 0.3
# 残血 Boss 血条尖头怎样算还有血(占屏高/屏宽). 血条高 2K 13 像素 / 1K 9 像素(约 19/2160);
# 剩 1% 血时尖头 2K 8x13 面积 62 / 1K 5x9 面积 25, 门槛取高过半条、面积过 高x3/3840 屏宽
BOSS_SLIVER_MIN_HEIGHT = 9 / 2160
BOSS_SLIVER_MAX_HEIGHT = 40 / 2160
BOSS_SLIVER_MIN_WIDTH = 3 / 3840


class CombatCheck(BaseWWTask):
    # 第一次看到结束条件的时刻, <=0 表示没在确认
    _end_seen_since = 0.0

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._in_combat = False
        self.skip_combat_check = False
        self.boss_lv_template = None
        self.boss_lv_mask = None
        self._in_liberation = False  # return True
        self.has_count_down = False
        self.sleep_check_interval = 0.4
        self.last_out_of_combat_time = 0
        self.boss_lv_box = None
        self.boss_health_box = None
        self.boss_health = None
        self.last_break_check_time = 0
        self.out_of_combat_reason = ""
        self.last_in_realm_not_combat = 0
        self._last_liberation = 0
        self.target_enemy_time_out = 3
        self.switch_char_time_out = 5
        self.combat_end_condition = None
        self.has_lavitator = False
        self.target_enemy_error_notified = False
        self.cds = {
        }
        self.esc_count = 0
        self.can_break = False
        # 按 F 开战后到第一次锁到目标之前的保护期起点, <=0 表示不在保护期
        self._f_grace_since = -1.0
        self._f_grace_log = 0.0

    @property
    def in_liberation(self):
        return self._in_liberation

    @in_liberation.setter
    def in_liberation(self, value):
        self._in_liberation = value
        if value:
            self._last_liberation = time.time()

    def on_combat_check(self):
        return True

    def reset_to_false(self, reason=""):
        self.out_of_combat_reason = reason
        self.do_reset_to_false()
        return False

    def do_reset_to_false(self):
        self.cds = {}
        self._in_combat = False
        self.skip_combat_check = False
        self.boss_lv_mask = None
        self.esc_count = 0
        self.boss_lv_template = None
        self.in_liberation = False  # return True
        self.has_count_down = False
        self.last_out_of_combat_time = 0
        self.boss_lv_box = None
        self.boss_health = None
        self.boss_health_box = None
        self.last_in_realm_not_combat = 0
        self.has_lavitator = False
        self.can_break = False
        self._f_grace_since = -1.0
        self._end_seen_since = 0.0
        self.scene.set_not_in_combat()
        return False

    def check_f_break(self):
        if self.can_break:
            return True
        if self.find_one(Labels.f_break_full, threshold=0.92):
            self.logger.debug('found f_break_full')
            self.can_break = True
            return True
        if not self.can_break and not self._in_liberation and time.time() - self.last_break_check_time > 1:
            self.last_break_check_time = time.time()
            if self.find_one('f_break', box=self.box_of_screen(0.2, 0.2, 0.75,
                                                               0.8, hcenter=True, vcenter=True), target_height=720):
                if not self.is_pick_f():
                    self.can_break = True
                    return True

    def f_break(self):
        if self.can_break or self.check_f_break():
            start = time.time()
            while time.time() - start < 0.5 or (time.time() - start < 5 and (self.can_break or self.check_f_break())):
                self.send_key('f', after_sleep=0.1)
                self.click(after_sleep=0.1)
                self.can_break = False
            return True

    def check_count_down(self):
        count_down_area = self.box_of_screen_scaled(3840, 2160, 1820, 266, 2100,
                                                    340, name="check_count_down", hcenter=True)
        count_down = self.calculate_color_percentage(text_white_color,
                                                     count_down_area)

        if self.has_count_down:
            if count_down < 0.03:
                numbers = self.ocr(box=count_down_area, match=count_down_re)
                if self.debug:
                    self.screenshot(f'count_down disappeared {count_down:.2f}%')
                logger.info(f'count_down disappeared {numbers} {count_down:.2f}%')
                if not numbers:
                    self.has_count_down = False
                    return False
                else:
                    return True
            else:
                return True
        else:
            if count_down > 0.03:
                numbers = self.ocr(box=count_down_area, match=count_down_re)
                if numbers:
                    self.has_count_down = True
                logger.info(f'set count_down to {self.has_count_down}  {numbers} {count_down:.2f}%')
            return self.has_count_down

    @property
    def target_area_box(self):
        return self.box_of_screen(0.1, 0.10, 0.9, 0.9, hcenter=True, name="target_area_box")

    def is_boss(self):
        return self.find_one('boss_break_shield') or self.find_one('boss_break_lock')

    def should_start_combat_by_key(self):
        """按过物理 F 且等够了时间就直接进战斗。只有自动战斗任务用, 见 AutoCombatTask。"""
        return False

    def start_combat_by_key(self):
        return False

    def f_grace_limit_seconds(self):
        """保护期最多撑多久, 0 = 一直撑到锁到目标。"""
        return 0.0

    def f_grace_hold(self):
        """按 F 开打后怪还没出来: 只补点一下中键就放行, 不卡那 3 秒找怪, 也不判脱战, 宏照常出招。

        撑过上限就退出保护期, 回到原规则(找 3 秒怪, 找不到算脱战)。
        """
        since = self._f_grace_since
        if not self._in_combat or since <= 0:
            return False
        held = time.time() - since
        limit = self.f_grace_limit_seconds()
        if limit > 0 and held >= limit:
            self._f_grace_since = -1.0
            self.log_info(f'F 开始战斗: {limit:.0f} 秒还没锁到目标, 按原规则找怪')
            return False
        self.middle_click(interval=GRACE_TARGET_INTERVAL)
        now = time.time()
        if now - self._f_grace_log >= GRACE_LOG_INTERVAL:
            self._f_grace_log = now
            self.log_info(f'F 开始战斗: 还没锁到目标, 不判脱战({held:.0f}s)')
        return True

    def do_check_in_combat(self, target):
        if not self._in_combat and self.should_start_combat_by_key():
            return self.start_combat_by_key()
        if self.in_liberation:
            return True
        if self._in_combat:
            if self.scene.in_combat() is not None:
                return self.scene.in_combat()
            self.check_f_break()
            if current_char := self.get_current_char():
                if current_char.skip_combat_check():
                    return self.scene.set_in_combat()
            if not self.on_combat_check():
                self.log_info(self.tr('on_combat_check failed'))
                return self.reset_to_false(reason='on_combat_check failed')
            if self.has_target():
                self.last_in_realm_not_combat = 0
                self._end_seen_since = 0.0
                return self.scene.set_in_combat()
            # Boss 还在(残血也算)就没打完, 只是一时没锁住(跳开/演出), 不能凭画面里有声骸收手
            if (self.combat_end_condition is not None and not self.boss_still_alive()
                    and self.combat_end_condition()):
                # 一帧看到「声骸」不算: 识别会误报, 而 Boss 没死时收手要空站好几秒才接上。
                # 隔一次判定再看到才收手, 中间照常出招
                now = time.time()
                if self._end_seen_since <= 0:
                    self._end_seen_since = now
                    self.log_info('end condition seen, confirm on next check')
                    return self.scene.set_in_combat()
                if now - self._end_seen_since >= END_CONFIRM_SECONDS:
                    return self.reset_to_false(reason='end condition reached')
                return self.scene.set_in_combat()
            self._end_seen_since = 0.0
            has_health = self.check_health_bar()
            retarget_timeout = 30 if has_health else self.target_enemy_time_out
            if self.target_enemy(wait=True, time_out=retarget_timeout, check_health=has_health):
                logger.debug(f'retarget enemy succeeded')
                return self.scene.set_in_combat()
            if self.should_check_monthly_card() and self.handle_monthly_card():
                return self.scene.set_in_combat()
            logger.error('target_enemy failed, try recheck break out of combat')
            return self.reset_to_false(reason='target enemy failed')
        else:
            from src.task.AutoCombatTask import AutoCombatTask
            chars_loaded = self.load_chars()
            has_target = self.has_target()
            if not has_target and target:
                self.log_debug('try target')
                self.middle_click(after_sleep=0.1)
            in_combat = has_target or ((self.config.get('Auto Target') or not isinstance(self,
                                                                                         AutoCombatTask)) and self.check_health_bar())
            if in_combat:
                if not has_target and not self.target_enemy(wait=True):
                    if not self.target_enemy_error_notified:
                        self.target_enemy_error_notified = True
                        self.log_error(self.tr('Target enemy failed, please disable Nvidia/AMD Filter or Sharpening!'),
                                       notify=True)
                    return False
                self.has_lavitator = self.find_one('edge_levitator', threshold=0.65)
                self.log_info(self.tr('enter combat {has_lavitator}').format(has_lavitator=self.has_lavitator))
                self._in_combat = chars_loaded or self.load_chars()
                return self._in_combat

    def in_combat(self, target=False):
        self.in_sleep_check = True
        try:
            return self.do_check_in_combat(target)
        except TaskDisabledException:
            raise
        except Exception as e:
            logger.error(f'do_check_in_combat:', e)
        finally:
            self.in_sleep_check = False

    def ensure_levitator(self):  # 目前未使用
        if not self.config.get('Check Levitator', True):
            return True
        if levi := self.find_one('edge_levitator', threshold=0.6):
            self.log_debug('edge levitator found {}'.format(levi))
            return True
        if self.has_char(Roccia):
            if self.find_one('levitator_roccia', threshold=0.6):
                return True
        if self.is_open_world_auto_combat():
            return False
        start = time.time()
        self.sleep(0.2)
        if levi := self.find_one('edge_levitator', threshold=0.6):
            self.log_debug('recheck edge levitator found {}'.format(levi))
            return True
        while time.time() - start < 1 and self.in_team()[0]:
            self.send_key_down(self.key_config.get('Wheel Key'))
            self.sleep(0.4)
        if self.in_team()[0]:
            self.log_info(self.tr('can not open wheel'))
            self.send_key_up(self.key_config.get('Wheel Key'))
            self.sleep(0.1)
            return False
        levitator = self.wait_feature('wheel_levitator', threshold=0.65, box=self.box_of_screen(0.27, 0.16, 0.68, 0.76))
        self.sleep(0.1)
        if not levitator:
            self.send_key_up(self.key_config.get('Wheel Key'))
            raise Exception('no levitator tool in the tab wheel!')
        old = win32api.GetCursorPos()
        self.move(levitator.x, levitator.y)
        abs_pos = self.executor.interaction.capture.get_abs_cords(levitator.x, levitator.y)
        win32api.SetCursorPos(abs_pos)
        self.sleep(0.1)
        self.send_key_up(self.key_config.get('Wheel Key'))
        self.sleep(0.2)
        win32api.SetCursorPos(old)
        if not self.wait_feature('edge_levitator', threshold=0.6, time_out=1):
            if self.has_char(Roccia):
                if self.find_one('levitator_roccia', threshold=0.6):
                    return True
        self.log_debug(f'ensuring leviator succees {levitator}')
        return self.target_enemy()

    def log_time(self, start, name):
        logger.debug(f'check cost {name} {time.time() - start}')
        return True

    def ocr_lv_text(self):
        lvs = self.ocr(box=self.target_area_box,
                       match=re.compile(r'lv\.\d{1,3}', re.IGNORECASE),
                       target_height=540, name='lv_text')
        return lvs

    def get_target_names(self):
        has_name = 'has_target'
        no_name = 'no_target'
        if self.is_browser():
            has_name += '_cloud'
            no_name += '_cloud'
        elif self.width == 1600:
            has_name += '_169'
            no_name += '_169'
        return has_name, no_name

    def allow_target_box_short_combat_check(self):
        if hasattr(self, 'get_current_char') and (current_char := self.get_current_char(raise_exception=False)):
            return getattr(current_char, 'target_box_short_combat_check', False)
        return False

    def has_target(self, double_check=False):
        got = self.find_target(double_check)
        if got:
            # 锁到目标, 按 F 开战的保护期到此结束
            self._f_grace_since = -1.0
        return got

    def find_target(self, double_check=False):
        threshold = 0.6
        has_name, no_name = self.get_target_names()
        scale = 1.2 if self.is_browser() else 1.1
        best = self.find_best_match_in_box(self.get_box_by_name(has_name).scale(scale), [has_name, no_name],
                                           threshold=threshold)
        if not best:
            best = self.find_best_match_in_box(self.get_box_by_name('box_target_enemy_long'),
                                               [has_name, no_name],
                                               threshold=threshold)
        if not best:
            best = self.find_best_match_in_box(self.get_box_by_name('target_box_long2'), [has_name, no_name],
                                               threshold=threshold)
        if not best and self.allow_target_box_short_combat_check() and self.find_best_match_in_box(
                self.get_box_by_name('target_box_short'), [has_name, no_name], threshold=threshold):
            return True

        if not best:
            best = self.find_best_match_in_box(self.get_box_by_name(has_name).scale(1.1, 2.0),
                                               [has_name, no_name],
                                               threshold=threshold)
            if best and self.esc_count == 0:
                if double_check:
                    logger.error(f'try fix bear echo')
                    self.send_key('esc', after_sleep=2)
                    self.send_key('esc', after_sleep=1.5)
                    self.esc_count = 1
                    return False
                else:
                    self.sleep(1)
                    return self.has_target(double_check=True)
        return best and best.name == has_name

    def target_enemy(self, wait=True, time_out=None, check_health=False):
        if not wait:
            self.middle_click()
        else:
            if self.has_target():
                return True
            elif self.f_grace_hold():
                return True
            else:
                timeout = self.target_enemy_time_out if time_out is None else time_out
                logger.info(f'target lost try retarget {timeout}')
                start = time.time()
                health_lost_start = None
                while time.time() - start < timeout:
                    self.middle_click(interval=0.2)
                    if self.has_target():
                        return True
                    self.next_frame()
                    if check_health:
                        if not self.check_health_bar():
                            if health_lost_start is None:
                                health_lost_start = time.time()
                            elif time.time() - health_lost_start > 1.2:
                                logger.info('health bar disappeared during retarget')
                                return False
                        else:
                            health_lost_start = None
                return False

    def _health_bar_min_size(self):
        min_height = self.height_of_screen(9 / 2160)
        min_width = self.width_of_screen((12 if self._in_combat else 100) / 3840)
        return min_width, min_height

    def has_health_bar(self):
        min_width, min_height = self._health_bar_min_size()
        boxes = find_color_rectangles(self.frame, enemy_health_color_red, min_width, min_height,
                                      max_height=min_height * 3)
        if len(boxes) > 0:
            self.draw_boxes('enemy_health_bar_red', boxes, color='blue')
            return True
        return self.has_boss_health_bar()

    def boss_health_area(self):
        return self.box_of_screen(1269 / 3840, 58 / 2160, 2533 / 3840, 200 / 2160, hcenter=True, vcenter=True)

    def has_boss_health_bar(self):
        """屏幕顶上 Boss 血条还有血(只看血的颜色, Boss 死后空槽和名字残留不算)。"""
        min_width, min_height = self._health_bar_min_size()
        boxes = find_color_rectangles(self.frame, boss_health_color, min_width, min_height * 1.3,
                                      box=self.boss_health_area())
        if len(boxes) == 1:
            self.boss_health_box = boxes[0]
            self.boss_health_box.width = 10
            self.boss_health_box.x += 6
            self.boss_health = self.boss_health_box.crop_frame(self.frame)
            self.draw_boxes('boss_health', boxes, color='blue')
            return True
        return False

    def boss_still_alive(self):
        """收手前问一句 Boss 还在不在: 破韧图标还挂着, 或者顶上血条还剩一点血色。

        has_boss_health_bar 要求血色块占满外接框 95%, 可血条左端是尖头, 残血时只剩尖头那一截,
        实机截图量过: 剩 7~8% 以下 2K/1K 都认不到。这里按连通块算, 只要高度够半条血条、面积够,
        尖头也算(2K/1K 都能认到 1% 血)。只在要收手时看一次。
        """
        if self.is_boss():
            return True
        area = self.boss_health_area()
        crop = self.frame[area.y:area.y + area.height, area.x:area.x + area.width, :3]
        lower, upper = color_range_to_bound(boss_health_color)
        mask = cv2.inRange(crop, lower, upper)
        count, _, stats, _ = cv2.connectedComponentsWithStats(mask)
        min_h = self.height_of_screen(BOSS_SLIVER_MIN_HEIGHT)
        max_h = self.height_of_screen(BOSS_SLIVER_MAX_HEIGHT)
        min_area = min_h * self.width_of_screen(BOSS_SLIVER_MIN_WIDTH)
        for i in range(1, count):
            h, a = stats[i, cv2.CC_STAT_HEIGHT], stats[i, cv2.CC_STAT_AREA]
            if min_h <= h <= max_h and a >= min_area:
                return True
        return False

    def check_health_bar(self):
        return self.has_health_bar() or self.is_boss()

    def keep_boss_text_white(self):
        cropped = self.boss_lv_box.crop_frame(self.frame)
        mask, area = get_mask_in_color_range(cropped, boss_white_text_color)
        if area / mask.shape[0] * mask.shape[1] < 0.05:
            mask, area = get_mask_in_color_range(cropped, boss_orange_text_color)
            if area / mask.shape[0] * mask.shape[1] < 0.05:
                mask, area = get_mask_in_color_range(cropped,
                                                     boss_red_text_color)
                if area / mask.shape[0] * mask.shape[1] < 0.05:
                    logger.error(f'keep_boss_text_white cant find text with the correct color')
                    return None, 0
        return cropped, mask


count_down_re = re.compile(r'\d\d')


def keep_only_white(frame):
    frame[frame != 255] = 0
    return frame


target_enemy_color_yellow = {
    'r': (0x84, 0xAD),  # Red range
    'g': (0x84, 0xAF),  # Green range
    'b': (0x20, 0x6F)  # Blue range
}  # 207,75,60

enemy_health_color_red = {
    'r': (174, 225),  # Red range
    'g': (55, 85),  # Green range
    'b': (55, 76)  # Blue range
}  # 207,75,60

enemy_health_color_black = {
    'r': (10, 55),  # Red range
    'g': (28, 50),  # Green range
    'b': (18, 70)  # Blue range
}

boss_white_text_color = {
    'r': (200, 255),  # Red range
    'g': (200, 255),  # Green range
    'b': (200, 255)  # Blue range
}

boss_orange_text_color = {
    'r': (218, 218),  # Red range
    'g': (178, 178),  # Green range
    'b': (68, 68)  # Blue range
}

boss_red_text_color = {
    'r': (200, 230),  # Red range
    'g': (70, 90),  # Green range
    'b': (60, 80)  # Blue range
}

boss_health_color = {
    'r': (245, 255),  # Red range
    'g': (30, 185),  # Green range
    'b': (4, 75)  # Blue range
}

aim_color = {
    'r': (150, 213),  # Red range
    'g': (148, 185),  # Green range
    'b': (22, 62)  # Blue range
}
