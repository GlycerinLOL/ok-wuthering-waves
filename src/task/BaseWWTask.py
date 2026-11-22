import math
import re
import time
from collections import namedtuple
from datetime import datetime, timedelta
from typing import List

import numpy as np

from ok import BaseTask, Logger, find_boxes_by_name, og, find_color_rectangles, mask_white, Box
from ok import CannotFindException
import cv2

from src.Labels import Labels
from src.task import BossBook
from src.scene.WWScene import WWScene

logger = Logger.get_logger(__name__)
number_re = re.compile(r'(\d+)')
stamina_re = re.compile(r'(\d+)/(\d+)')
LOGIN_TEXTS = ["登录", re.compile('Log|点击连接|Click to Connect', re.IGNORECASE), '登入']
LOGIN_CLICK_SETTLE_TIME = 4  # seconds; keep below AutoLoginTask trigger_interval (5) so triggers don't overlap
# 云游戏网页弹窗, 如 【云服务错误：1020】检测到您已长时间未进行操作，已和服务器断开连接。
# 1020 弹窗按钮是"退出游戏", 4012 只有"确定"; 两者都会回到云游戏首页。
CLOUD_DISCONNECT_TEXTS = re.compile(r'云服务错误|服务器断开|断开连接')
CLOUD_EXIT_GAME_TEXTS = re.compile(r'^(退出游戏|确定)$')
CLOUD_RELOGIN_TIMEOUT = 600
f_white_color = {
    'r': (235, 255),  # Red range
    'g': (235, 255),  # Green range
    'b': (235, 255)  # Blue range
}
processed_feature = False
WIDE_MODE_UI_SCALE = 0.75
# 编队界面的判据。team_close 是可交互按钮, 鼠标悬停时会放大变形, 匹配分数掉到 0.62;
# team_start_challenge 在同一帧不随悬停变化, 且下一步本来就要点它。
TEAM_SCREEN_FEATURES = ('team_start_challenge', 'team_close')
# 素材获取列表里的一行 Boss：OCR 名字、名字框顶的 y 占比、这一行自己的按钮框(没有为 None)、是否梦魇聚落、
# 按钮种类('go' 前往 / 'challenge' 直接挑战 / None 只认出按钮形状)
BookRow = namedtuple('BookRow', 'name y btn nest kind')
# 右边一个按钮：中心 y 占比、点击用的框、种类
BookButton = namedtuple('BookButton', 'cy box kind')
# 读一屏的结果：Boss 行、签名（翻到底判断用）、列表里所有字的高度（估露出多高用）
BookPage = namedtuple('BookPage', 'rows sig text_ys')


class BaseWWTask(BaseTask):
    map_zoomed = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.monthly_card_config = self.get_global_config('Monthly Card Config')
        self.char_config = self.get_global_config('Character Config')
        self.key_config = self.get_global_config('Game Hotkey')  # 游戏热键配置
        self.next_monthly_card_start = 0
        self.scene: WWScene | None = None

    @property
    def logged_in(self):
        return og.my_app.logged_in

    @logged_in.setter
    def logged_in(self, value):
        og.my_app.logged_in = value

    def is_open_world_auto_combat(self):
        from src.task.AutoCombatTask import AutoCombatTask
        from src.task.TacetTask import TacetTask
        from src.task.DailyTask import DailyTask
        if isinstance(self, AutoCombatTask):
            if not self.in_realm():
                return True
        elif isinstance(self, (TacetTask, DailyTask)):
            return True
        return False

    def zoom_map(self, esc=True):
        if not self.map_zoomed:
            self.log_info(self.tr('zoom map to max'))
            self.map_zoomed = True
            self.send_key('m', after_sleep=1)
            self.click_relative(0.94, 0.33, after_sleep=0.5)
            if esc:
                self.send_key('esc', after_sleep=1)

    def validate(self, key, value):
        message = self.validate_config(key, value)
        if message:
            return False, message
        else:
            return True, None

    def absorb_echo_text(self, ignore_config=False):
        if self.game_lang == 'zh_CN' or self.game_lang == 'en_US' or self.game_lang == 'zh_TW':
            return re.compile(r'(吸收|Absorb)')
        else:
            return None

    @property
    def absorb_echo_feature(self):
        return self.get_feature_by_lang('absorb')

    def get_feature_by_lang(self, feature):
        lang_feature = feature + '_' + self.game_lang
        if self.feature_exists(lang_feature):
            return lang_feature
        else:
            return None

    def set_check_monthly_card(self, next_day=False):
        if self.monthly_card_config.get('Check Monthly Card'):
            now = datetime.now()
            hour = self.monthly_card_config.get('Monthly Card Time')
            # Calculate the next 4 o'clock in the morning
            next_four_am = now.replace(hour=hour, minute=0, second=0, microsecond=0)
            if now >= next_four_am or next_day:
                next_four_am += timedelta(days=1)
            next_monthly_card_start_date_time = next_four_am - timedelta(seconds=30)
            # Subtract 1 minute from the next 4 o'clock in the morning
            self.next_monthly_card_start = next_monthly_card_start_date_time.timestamp()
            logger.info('set next monthly card start time to {}'.format(next_monthly_card_start_date_time))
        else:
            self.next_monthly_card_start = 0

    @property
    def f_search_box(self):
        f_search_box = self.get_box_by_name('pick_up_f_hcenter_vcenter')
        f_search_box = f_search_box.copy(x_offset=-f_search_box.width * 0.3,
                                         width_offset=f_search_box.width * 0.65,
                                         height_offset=f_search_box.height * 6.5,
                                         y_offset=-f_search_box.height * 5,
                                         name='search_dialog')
        return f_search_box

    def find_f_with_claim_text(self):
        return self.find_f_with_text(target_text=[re.compile('领取|領取|Claim', re.IGNORECASE)])

    def find_f_with_text(self, target_text=None):
        f = self.find_one(Labels.pick_up_f_hcenter_vcenter, box=self.f_search_box, threshold=0.8)
        if not f:
            return None
        if not target_text:
            return f

        start = time.time()
        percent = 0.0
        while time.time() - start < 1:
            percent = self.calculate_color_percentage(f_white_color, f)
            if percent > 0.5:
                break
            self.next_frame()
            self.log_debug(f'f white color percent: {percent} wait')
        if percent < 0.5:
            return None

        if target_text:
            search_text_box = f.copy(x_offset=f.width * 5.2, width_offset=f.width * 6, height_offset=4.5 * f.height,
                                     y_offset=-0.8 * f.height, name='search_text_box')
            text = self.ocr(box=search_text_box, match=target_text)
            logger.debug(f'found f with text {text}, target_text {target_text}')
            if text:
                if text[0].y > search_text_box.y + f.height * 1:
                    logger.debug(f'found f with text {text} below, target_text {target_text}')
                    self.scroll_relative(0.5, 0.5, 1)
                return f
        else:
            return f

    def has_target(self):
        return False

    def walk_to_yolo_echo(self, time_out=8, update_function=None, echo_threshold=0.5):
        last_direction = None
        start = time.time()
        no_echo_start = 0
        while time.time() - start < time_out:
            self.next_frame()
            if self.pick_f():
                self.log_debug('pick echo success')
                self._stop_last_direction(last_direction)
                return True
            if self.in_combat():
                self.log_debug('pick echo has_target return fail')
                self._stop_last_direction(last_direction)
                return False
            echos = self.find_echos(threshold=echo_threshold)
            if not echos:
                if no_echo_start == 0:
                    no_echo_start = time.time()
                elif time.time() - no_echo_start > 3:
                    self.log_debug(f'walk front to_echo, no echos found, break')
                    break
                next_direction = 'w'
            else:
                no_echo_start = 0
                echo = echos[0]
                center_distance = echo.center()[0] - self.width_of_screen(0.5)
                threshold = 0.05 if not last_direction else 0.15
                if abs(center_distance) < self.height_of_screen(threshold):
                    if echo.y + echo.height > self.height_of_screen(0.65):
                        next_direction = 's'
                    else:
                        next_direction = 'w'
                elif center_distance > 0:
                    next_direction = 'd'
                else:
                    next_direction = 'a'
            last_direction = self._walk_direction(last_direction, next_direction)
            if update_function is not None:
                update_function()
        self._stop_last_direction(last_direction)

    def _walk_direction(self, last_direction, next_direction):
        if next_direction != last_direction:
            self._stop_last_direction(last_direction)
            if next_direction:
                self.send_key_down(next_direction)
        return next_direction

    def _stop_last_direction(self, last_direction):
        if last_direction:
            self.send_key_up(last_direction)
            self.sleep(0.01)
        return None

    def walk_to_box(self, find_function, time_out=30, end_condition=None, y_offset=0.05, x_threshold=0.07,
                    use_hook=False):
        start = time.time()
        while time.time() - start < time_out:
            if ended := self.do_walk_to_box(find_function, time_out=time_out - (time.time() - start),
                                            end_condition=end_condition, y_offset=y_offset,
                                            x_threshold=x_threshold, use_hook=use_hook):
                return ended

    def do_walk_to_box(self, find_function, time_out=30, end_condition=None, y_offset=0.05, x_threshold=0.07,
                       use_hook=False):
        if find_function:
            self.wait_until(lambda: (not end_condition or end_condition()) or find_function(), raise_if_not_found=True,
                            time_out=time_out)
        last_direction = None
        start = time.time()
        ended = False
        running = False
        last_target = None
        centered = False
        while time.time() - start < time_out:
            self.next_frame()
            if end_condition:
                ended = end_condition()
                if ended:
                    logger.info(f'do_walk_to_box ended {ended}')
                    break
            treasure_icon = find_function()
            if isinstance(treasure_icon, list):
                if len(treasure_icon) > 0:
                    treasure_icon = treasure_icon[0]
                else:
                    treasure_icon = None
            if treasure_icon:
                last_target = treasure_icon
            if last_target is None:
                next_direction = self.opposite_direction(last_direction)
                self.log_info(self.tr('find_function not found, change to opposite direction'))
            else:
                x, y = last_target.center()
                y = max(0, y - self.height_of_screen(y_offset))
                x_abs = abs(x - self.width_of_screen(0.5))
                threshold = 0.04 if not last_direction else x_threshold
                centered = centered or x_abs <= self.width_of_screen(threshold)
                if not centered:
                    if x > self.width_of_screen(0.5):
                        next_direction = 'd'
                    else:
                        next_direction = 'a'
                else:
                    if last_direction == 's':
                        center = 0.45
                    elif last_direction == 'w':
                        center = 0.6
                    else:
                        center = 0.5
                    if y > self.height_of_screen(center):
                        next_direction = 's'
                    else:
                        next_direction = 'w'
            if next_direction != last_direction:
                if last_direction:
                    self.send_key_up(last_direction)
                    self.sleep(0.001)
                last_direction = next_direction
                if next_direction:
                    self.send_key_down(next_direction)
            if running:
                if not self.find_one('on_the_wall', threshold=0.7):
                    self.log_info(self.tr('not on the wall, stop running'))
                    self.mouse_up(key='right')
            else:
                if next_direction == 'w' and self.find_one('on_the_wall', threshold=0.7):
                    self.log_info(self.tr('on the wall, start running'))
                    running = True
                    self.mouse_down(key='right')
                    self.sleep(0.1)
            if use_hook and next_direction == 'w':
                if self.find_one('tool_teleport', 0.75):
                    self.send_key(self.key_config['Tool Key'])
                    self.sleep(3)
                    continue
        if last_direction:
            self.send_key_up(last_direction)
            self.sleep(0.001)
        if running:
            self.send_key_up(self.key_config.get('Dodge Key'))
        if not end_condition:
            return last_direction is not None
        else:
            return ended

    def opposite_direction(self, direction):
        if direction == 'w':
            return 's'
        elif direction == 's':
            return 'w'
        elif direction == 'a':
            return 'd'
        elif direction == 'd':
            return 'a'
        else:
            return 'w'

    def get_direction(self, location_x, location_y, screen_width, screen_height, centered, current_direction):
        """
        Determines the direction ('w', 'a', 's', 'd') closest to the screen center.
        Args:
            location_x: The x-coordinate of the point.
            location_y: The y-coordinate of the point.
            screen_width: The width of the screen.
            screen_height: The height of the screen.
        Returns:
            A string "w", "a", "s", or "d".
        """
        if screen_width <= 0 or screen_height <= 0:
            # Handle invalid dimensions, default based on horizontal position
            return "a" if location_x < screen_width / 2 else "d"
        center_x = screen_width / 2
        center_y = screen_height / 2
        # Calculate vector from point towards the center
        delta_x = center_x - location_x
        delta_y = center_y - location_y
        # Determine dominant direction based on vector magnitude
        direction = None
        if (abs(delta_x) > abs(delta_y) or (not current_direction and abs(delta_x) > 0.05 * screen_height)
                or abs(delta_x) > 0.15 * screen_height):
            # More horizontal movement needed
            return "a" if delta_x > 0 else "d"

            # More vertical movement needed (or equal)
        return "w" if delta_y > 0 else "s"

    def find_treasure_icon(self):
        return self.find_one('treasure_icon', box=self.box_of_screen(0.03, 0.1, 0.97, 0.81, hcenter=True, vcenter=True),
                             threshold=0.8,
                             target_height=720)

    def click(self, x=-1, y=-1, move_back=False, name=None, interval=-1, move=False, down_time=0.01, after_sleep=0,
              key="left"):
        if x == -1 and y == -1:
            x = self.width_of_screen(0.5)
            y = self.height_of_screen(0.5)
            move = False
            down_time = 0.01
        else:
            down_time = 0.2
        return super().click(x, y, move_back, name, interval, move=move, down_time=down_time, after_sleep=after_sleep,
                             key=key)

    def check_for_monthly_card(self):
        if self.should_check_monthly_card():
            start = time.time()
            logger.info(f'check_for_monthly_card start check')
            if self.in_combat():
                logger.info(f'check_for_monthly_card in combat return')
                return time.time() - start
            if self.in_team_and_world():
                logger.info(f'check_for_monthly_card in team send sleep until monthly card popup')
                monthly_card = self.wait_until(self.handle_monthly_card, time_out=120, raise_if_not_found=False)
                logger.info(f'wait monthly card end {monthly_card}')
                cost = time.time() - start
                return cost
        return 0

    def in_realm(self):
        return not bool(getattr(self, 'treat_as_not_in_realm', False)) and self.find_one('illusive_realm_exit',
                                                                                         threshold=0.7,
                                                                                         frame_processor=convert_bw) and self.in_team() and not self.find_one(
            'world_earth_icon', threshold=0.55,
            frame_processor=convert_bw)

    def in_world(self):
        return self.find_one('world_earth_icon', threshold=0.55,
                             frame_processor=convert_bw) and self.in_team() and not self.find_one('illusive_realm_exit',
                                                                                                  threshold=0.7,
                                                                                                  frame_processor=convert_bw)

    def in_illusive_realm(self):
        return self.find_one('new_realm_4') and self.in_realm() and self.find_one('illusive_realm_menu', threshold=0.6)

    def walk_until_f(self, direction='w', time_out=1, raise_if_not_found=True, backward_time=0, target_text=None,
                     check_combat=False, running=False):
        logger.info(f'walk_until_f direction {direction} target_text: {target_text}')
        if not self.find_f_with_text(target_text=target_text):
            # 视角朝前
            self.middle_click(after_sleep=0.2)
            if backward_time > 0:
                if self.send_key_and_wait_f('s', raise_if_not_found, backward_time, target_text=target_text,
                                            running=running, check_combat=check_combat):
                    logger.info('walk backward found f')
                    return True
            if self.send_key_and_wait_f(direction, raise_if_not_found, time_out, target_text=target_text,
                                        running=running, check_combat=check_combat):
                logger.info('walk forward found f')
                return True
            return False
        else:
            return True

    def get_stamina(self):
        boxes = self.wait_ocr(0.49, 0.0, 0.92, 0.10, raise_if_not_found=False,
                              match=[number_re, stamina_re])
        if not boxes:
            self.screenshot('stamina_error')
            return -1, -1, -1
        current = 0
        back_up = 0
        for box in boxes:
            if match := stamina_re.search(box.name):
                current = int(match.group(1))
            elif match := number_re.search(box.name):
                back_up = int(match.group(1))
        self.info_set('current_stamina', current)
        self.info_set('back_up_stamina', back_up)
        return current, back_up, current + back_up

    def use_stamina(self, once=60, must_use=0):
        self.sleep(1)
        current, back_up, total = self.get_stamina()
        if current >= once * 2:
            used = once * 2
            use_double = True
            logger.info(f"当前体力大于等于双倍, {current} >= {once * 2}")
        elif must_use > once and total >= once * 2:
            used = once * 2
            use_double = True
            logger.info(f"当前加备用大于日常剩余所需, 使用双倍, {must_use} >= {once} and {total} >= {once * 2}")
        else:
            used = once
            use_double = False
            logger.info(f"使用单倍体力")
        if use_double:
            btn = self.click_dialog_right_button()
        else:
            btn = self.click_dialog_left_button()
        if self.wait_feature('gem_add_stamina', horizontal_variance=0.4, vertical_variance=0.05,
                             time_out=2, settle_time=0.5):  # 看是否需要使用备用体力
            self.click_relative(0.70, 0.71, hcenter=True, after_sleep=1)  # 点击确认
            self.click_relative(0.70, 0.71, hcenter=True, after_sleep=1)
            self.back(after_sleep=1)
            self.click(btn, after_sleep=1)

        current -= used
        must_use -= used
        total -= used
        if total < once:
            logger.info(f"current stamina: {current} not enough to continue")
            can_continue = False
        elif must_use <= 0 and current < once:
            can_continue = False
            logger.info(f"current stamina: {current} must_use completed, no need to use back_up")
        else:
            can_continue = True
        return can_continue, used

    def send_key_and_wait_f(self, direction, raise_if_not_found, time_out, running=False, target_text=None,
                            check_combat=False):
        if time_out <= 0:
            return
        self.send_key_down(direction)
        if running:
            self.sleep(0.1)
            self.mouse_down(key='right')
        f_found = self.wait_until(
            lambda: self.find_f_with_text(target_text=target_text) or (check_combat and self.in_combat()),
            time_out=time_out,
            raise_if_not_found=False)
        self.send_key_up(direction)
        if running:
            self.sleep(0.1)
            self.mouse_up(key='right')
        if not f_found:
            if raise_if_not_found:
                raise CannotFindException(self.tr('cant find the f to enter'))
            else:
                logger.warning(f"can't find the f to enter")
                return False
        return f_found

    def run_until(self, condiction, direction, time_out, raise_if_not_found=False, running=False, target=False,
                  post_walk=0):
        if time_out <= 0:
            return
        self.send_key_down(direction)
        if running:
            self.sleep(0.1)
            logger.debug(f'run_until condiction {condiction} direction {direction}')
            self.mouse_down(key='right')
        start = time.time()
        result = None
        while time.time() - start < time_out:
            if result := condiction():
                break
            if target:
                self.middle_click(interval=0.5)
            self.sleep(0.02)
        if result and post_walk:
            self.sleep(post_walk)
        self.send_key_up(direction)
        if running:
            self.sleep(0.1)
            self.mouse_up(key='right')

        if raise_if_not_found and not result:
            raise Exception(self.tr('wait condition failed while walking'))
        return result

    def is_moving(self):
        return False

    def handle_claim_button(self):
        while self.wait_until(self.has_claim, raise_if_not_found=False, time_out=1.5):
            self.sleep(0.5)
            self.send_key('esc')
            self.sleep(0.5)
            logger.info(f"handle_claim_button found a claim reward")
            return True

    def has_claim_stamina(self):
        return not self.in_team()[0] and self.find_one('claim_stamina_sign')

    def has_claim(self):
        return not self.in_team()[0] and self.find_one('claim_cancel_button_hcenter_vcenter', horizontal_variance=0.05,
                                                       vertical_variance=0.1, threshold=0.8)

    def test_absorb(self):
        # self.set_image('tests/images/absorb.png')
        image = cv2.imread('tests/images/absorb.png')
        result = self.executor.ocr_lib(image, use_det=True, use_cls=False, use_rec=True)
        self.logger.info(f'ocr_result {result}')

    def find_echos(self, threshold=0.3):
        """
        Main function to load ONNX model, perform inference, draw bounding boxes, and display the output image.

        Args:
            onnx_model (str): Path to the ONNX model.
            input_image (ndarray): Path to the input image.

        Returns:
            list: List of dictionaries containing detection information such as class_id, class_name, confidence, etc.
        """
        # Load the ONNX model
        ret = og.my_app.yolo_detect(self.frame, threshold=threshold, label=0)

        for box in ret:
            box.y += box.height * 1 / 3
            box.height = 1
        self.draw_boxes("echo", ret)
        return ret

    def yolo_find_all(self, threshold=0.3):
        """
        Main function to load ONNX model, perform inference, draw bounding boxes, and display the output image.

        Args:
            onnx_model (str): Path to the ONNX model.
            input_image (ndarray): Path to the input image.

        Returns:
            list: List of dictionaries containing detection information such as class_id, class_name, confidence, etc.
        """
        # Load the ONNX model
        boxes = og.my_app.yolo_detect(self.frame, threshold=threshold, label=-1)
        ret = sorted(boxes, key=lambda detection: detection.confidence, reverse=True)
        return ret

    def pick_echo(self):
        if self.find_f_with_text(target_text=self.absorb_echo_text()):
            self.send_key('f')
            if not self.handle_claim_button():
                self.log_debug('found a echo picked')
                return True

    def pick_f(self, handle_claim=True):
        if self.find_one('pick_up_f_hcenter_vcenter', box=self.f_search_box, threshold=0.8):
            self.send_key('f', after_sleep=1)
            if not handle_claim:
                return True
            if not self.handle_claim_button():
                self.log_debug('found a echo picked')
                return True

    def is_pick_f(self):
        f = self.find_one('pick_up_f_hcenter_vcenter', box=self.f_search_box,
                          threshold=0.8)
        if not f:
            return False
        dialog_search = f.copy(x_offset=f.width * 3, width_offset=f.width * 1.8, height_offset=f.height * 2,
                               y_offset=-f.height,
                               name='search_dialog')
        dialog_3_dots = self.find_feature('dialog_3_dots', box=dialog_search,
                                          threshold=0.6)
        return bool(dialog_3_dots)

    def walk_to_treasure(self, send_f=True, raise_if_not_found=True):
        self.log_info(self.tr('start walk_to_treasure'))
        if not self.walk_to_box(self.find_treasure_icon, end_condition=self.find_f_with_claim_text):
            if not self.walk_to_box(self.find_treasure_icon, end_condition=self.find_f_with_text):
                raise Exception(self.tr('can not walk to treasure!'))
        if send_f:
            self.walk_until_f(time_out=2, backward_time=0, raise_if_not_found=raise_if_not_found)
        self.sleep(1)

    def yolo_find_echo(self, use_color=False, turn=True, update_function=None, time_out=8, threshold=0.5):
        max_echo_count = 0
        if self.pick_echo():
            self.sleep(0.5)
            return True, True
        front_box = self.box_of_screen(0.35, 0.35, 0.65, 0.53, hcenter=True)
        color_threshold = 0.02
        for i in range(4):
            if turn:
                self.center_camera()
            echos = self.find_echos(threshold=threshold)
            max_echo_count = max(max_echo_count, len(echos))
            self.log_debug(f'max_echo_count {max_echo_count}')
            if echos:
                self.log_info(self.tr('yolo found echo {echos}').format(echos=echos))
                # return self.walk_to_box(self.find_echos, time_out=15, end_condition=self.pick_echo), max_echo_count > 1
                return self.walk_to_yolo_echo(update_function=update_function, time_out=time_out), max_echo_count > 1
            if use_color:
                color_percent = self.calculate_color_percentage(echo_color, front_box)
                self.log_debug(f'pick_echo color_percent:{color_percent}')
                if color_percent > color_threshold:
                    # if self.debug:
                    #     self.screenshot('echo_color_picked')
                    self.log_debug(f'found color_percent {color_percent} > {color_threshold}, walk now')
                    # return self.walk_to_box(self.find_echos, time_out=15, end_condition=self.pick_echo), max_echo_count > 1
                    return self.walk_to_yolo_echo(update_function=update_function), max_echo_count > 1
            if not turn and i == 0:
                return False, max_echo_count > 1
            self.send_key('a', down_time=0.05)
            self.sleep(0.5)

        self.center_camera()
        return False, max_echo_count > 1

    def center_camera(self):
        self.click(0.5, 0.5, down_time=0.2, key='middle')
        self.sleep(1)

    def turn_direction(self, direction):
        if direction != 'w':
            self.send_key(direction, down_time=0.05, after_sleep=0.5)
        self.center_camera()

    def walk_find_echo(self, backward_time=1, time_out=3):
        if self.walk_until_f(time_out=time_out, backward_time=backward_time, target_text=self.absorb_echo_text(),
                             raise_if_not_found=False, check_combat=True):  # find and pick echo
            logger.debug(f'farm echo found echo move forward walk_until_f to find echo')
            return self.pick_f()

    def incr_drop(self, dropped):
        if dropped:
            self.info['Echo Count'] = self.info.get('Echo Count', 0) + 1
            self.info['Echo per Hour'] = round(
                self.info.get('Echo Count', 0) / max(time.time() - self.start_time, 1) * 3600)

    def should_check_monthly_card(self):
        if self.next_monthly_card_start > 0:
            if 0 < time.time() - self.next_monthly_card_start < 120:
                return True
        return False

    def sleep(self, timeout):
        return super().sleep(timeout - self.check_for_monthly_card())

    def wait_in_team_and_world(self, time_out=10, raise_if_not_found=True, esc=False):
        success = self.wait_until(self.in_team_and_world, time_out=time_out, raise_if_not_found=raise_if_not_found,
                                  post_action=lambda: self.back(after_sleep=2) if esc else None)
        if success:
            self.sleep(0.5)
        return success

    def esc_world_confirm(self, send_esc=True):
        if send_esc:
            self.send_key('esc', after_sleep=1)
        self.click_dialog_right_button()
        self.wait_in_team_and_world(time_out=120)

    def click_dialog_right_button(self):
        confirm = self.find_one([
            Labels.confirm_btn_hcenter_vcenter,
            Labels.confirm_btn_highlight_hcenter_vcenter,
        ])
        if not confirm:
            raise CannotFindException(self.tr("can't find dialog right button"))
        self.click(confirm, after_sleep=2)
        return confirm

    def esc_cancel(self, send_esc=True):
        if send_esc:
            self.send_key('esc', after_sleep=1)
        self.click_dialog_left_button()
        self.wait_in_team_and_world(time_out=120)

    def click_dialog_left_button(self) -> Box:
        cancel = self.find_one([
            Labels.cancel_button_hcenter_vcenter,
            Labels.cancel_button_highlight_hcenter_vcenter,
        ])
        if not cancel:
            raise CannotFindException(self.tr("can't find dialog left button"))
        self.click(cancel, after_sleep=2)
        return cancel

    def ensure_main(self, esc=True, time_out=30):
        self.info_set('current task', f'wait main esc={esc}')
        if not self.logged_in:
            time_out = 600
        if not self.wait_until(lambda: self.is_main(esc=esc), time_out=time_out, raise_if_not_found=False):
            raise Exception(self.tr('Please start in game world and in team!'))
        self.sleep(0.5)
        self.info_set('current task', f'in main esc={esc}')

    def is_main(self, esc=True):
        if self.in_team_and_world():
            self.logged_in = True
            if self.in_realm():
                self.esc_world_confirm()
            return True
        if self.wait_login():
            return False
        if self.handle_monthly_card():
            return False
        if self.handle_cloud_disconnect():
            return self.in_team_and_world()
        if esc:
            self.log_debug('main esc')
            self.back(after_sleep=2)
            return False

    def find_cloud_disconnect(self):
        """云游戏断线弹窗: 返回 '退出游戏' 按钮, 否则 None。"""
        if not self.is_browser():
            return None
        texts = self.ocr(box=self.box_of_screen(0.2, 0.25, 0.8, 0.75, hcenter=True, vcenter=True),
                         name='cloud_disconnect', log=self.debug)
        if not self.find_boxes(texts, match=CLOUD_DISCONNECT_TEXTS):
            return None
        return self.find_boxes(texts, match=CLOUD_EXIT_GAME_TEXTS) or None

    def handle_cloud_disconnect(self):
        """云游戏断线(如长时间无操作的 1020/4012 错误)后退出到云游戏首页, 再点进入游戏重新登录。"""
        exit_game = self.find_cloud_disconnect()
        if not exit_game:
            return False
        self.log_info(self.tr('Cloud game disconnected, re-entering the game'), notify=True)
        self.click(exit_game[0], after_sleep=3)
        self.logged_in = False
        # wait_login 在云游戏首页点击"进入游戏", 进游戏后处理登录/月卡, 回到大世界后返回 True
        self.wait_until(self.wait_login, time_out=CLOUD_RELOGIN_TIMEOUT, raise_if_not_found=True,
                        settle_time=1)
        return True

    def wait_login(self):
        if not self.logged_in:
            if self.in_team_and_world():
                self.logged_in = True
                return True
            self.handle_monthly_card()
            if login_close := self.find_one('login_close', horizontal_variance=0.15, vertical_variance=0.1):
                self.click(login_close, after_sleep=1)
                self.log_info(self.tr('Close the notice!'))
                return False
            texts = self.ocr(log=self.debug)

            login_box = self.box_of_screen(0.3, 0.3, 0.7, 0.7, hcenter=True, vcenter=True)
            if login := self.find_boxes(texts,
                                        boundary=login_box,
                                        match=LOGIN_TEXTS):
                if not self.find_boxes(texts, boundary=login_box, match="+86"):
                    # the game may be auto logging in with saved credentials, wait and
                    # confirm the login button is still there before clicking (#1356)
                    self.sleep(LOGIN_CLICK_SETTLE_TIME)
                    texts = self.ocr(log=self.debug)
                    login = self.find_boxes(texts, boundary=login_box,
                                            match=LOGIN_TEXTS)
                    if login and not self.find_boxes(texts, boundary=login_box,
                                                     match="+86"):
                        self.click(login, after_sleep=1)
                        self.log_info(self.tr('Click the Login button!'))
                return False
            if agree := self.find_boxes(texts, boundary=login_box, match="同意"):
                self.log_debug(f'found agree {agree}')
                if self.find_boxes(texts, boundary=login_box, match=re.compile("隐私")):
                    self.click(agree, after_sleep=1)
                    self.log_info(self.tr('Click the Agree button!'))
                return False
            if self.find_boxes(texts, match=[re.compile("游戏即将重启"), re.compile('遊戲即將重啟')]):
                self.sleep(0.2)
                self.log_info(self.tr('Game update completed; the game will restart'))
                self.click(self.find_boxes(texts, match=["确认", "確認"]), after_sleep=60)
                result = self.start_device()
                self.log_info(self.tr('start_device end {result}').format(result=result))
                self.sleep(30)
                return False

            if start := self.find_boxes(texts, boundary='bottom_right', match=["开始游戏", re.compile("进入游戏")]):
                if not self.find_boxes(texts, boundary='bottom_right', match=LOGIN_TEXTS):
                    self.click(start)
                    self.log_info(self.tr('Click Start Game! {start}').format(start=start))
                    return False
            if switch_login := self.find_one(Labels.switch_account, vertical_variance=0.1, threshold=0.7):
                if boxes := self.find_boxes(texts, boundary=self.box_of_screen(0.37, 0.63, 0.63, 0.99, hcenter=True,
                                                                               vcenter=True)):
                    self.log_info(self.tr('wait_login {switch_login} {boxes}').format(switch_login=switch_login, boxes=boxes))
                    self.click_relative(0.503, 0.926, hcenter=True, vcenter=True, after_sleep=3)
                    return False

    def in_team_and_world(self):
        return self.in_team()[
            0]  # and self.find_one(f'gray_book_button', threshold=0.7, canny_lower=50, canny_higher=150)

    def get_angle_between(self, my_angle, angle):
        if my_angle > angle:
            to_turn = angle - my_angle
        else:
            to_turn = -(my_angle - angle)
        if to_turn > 180:
            to_turn -= 360
        elif to_turn < -180:
            to_turn += 360
        return to_turn

    def get_my_angle(self):
        return self.rotate_arrow_and_find()[0]

    def rotate_arrow_and_find(self):
        arrow_template = self.get_feature_by_name('arrow')
        original_mat = arrow_template.mat
        max_conf = 0
        max_angle = 0
        max_target = None
        max_mat = None
        (h, w) = arrow_template.mat.shape[:2]
        # self.log_debug(f'turn_east h:{h} w:{w}')
        center = (w // 2, h // 2)
        target_box = self.get_box_by_name('arrow')
        # if self.debug:
        #     self.screenshot('arrow_original', original_ mat)
        for angle in range(0, 360):
            # Rotate the template image
            rotation_matrix = cv2.getRotationMatrix2D(center, -angle, 1.0)
            template = cv2.warpAffine(original_mat, rotation_matrix, (w, h))
            # mask = np.where(np.all(template == [0, 0, 0], axis=2), 0, 255).astype(np.uint8)

            target = self.find_one(box=target_box,
                                   template=template, threshold=0.01)
            # if self.debug and angle % 90 == 0:
            #     self.screenshot(f'arrow_rotated_{angle}', arrow_template.mat)
            if target and target.confidence > max_conf:
                max_conf = target.confidence
                max_angle = angle
                max_target = target
                # max_mat = template
        # arrow_template.mat = original_mat
        # arrow_template.mask = None
        # if self.debug and max_mat is not None:
        #     self.screenshot('max_mat',frame=max_mat)
        # self.log_debug(f'turn_east max_conf: {max_conf} {max_angle}')
        return max_angle, max_target

    def get_mini_map_turn_angle(self, feature, threshold=0.72, x_offset=0, y_offset=0):
        box = self.get_box_by_name('box_minimap')
        target = self.find_one(feature, box=box, threshold=threshold)
        if not target:
            self.log_info(self.tr('Can not find {feature} on minimap').format(feature=feature))
            return None
        else:
            self.log_debug(f'found {box} on minimap')
        target.x += target.width * x_offset
        target.y += target.height * y_offset
        direction_angle = calculate_angle_clockwise(box, target)
        my_angle = self.get_my_angle()
        to_turn = self.get_angle_between(my_angle, direction_angle)
        self.log_info(self.tr('angle: {my_angle}, to_turn: {to_turn}').format(my_angle=my_angle, to_turn=to_turn))
        return to_turn

    def _stop_movement(self, current_direction):
        """Releases keys and mouse to stop character movement."""
        if current_direction is not None:
            self.mouse_up(key='right')
            self.send_key_up(current_direction)

    def _navigate_based_on_angle(self, angle, current_direction, current_adjust):
        """
        Core navigation logic to adjust movement based on a target angle.
        This contains the shared logic from the original functions.

        Returns a tuple: (new_direction, new_adjust, should_continue)
        - new_direction: The updated movement direction ('w', 'a', 's', 'd').
        - new_adjust: The updated adjustment state.
        - should_continue: A boolean indicating if the calling loop should `continue`.
        """
        # 1. Handle minor adjustments if already moving forward
        if current_direction == 'w':
            if 10 <= angle <= 80:
                minor_adjust = 'd'
            elif -80 <= angle <= -10:
                minor_adjust = 'a'
            else:
                minor_adjust = None

            if minor_adjust:
                self.send_key_down(minor_adjust)
                self.sleep(0.1)
                self.middle_click(down_time=0.1)
                self.send_key_up(minor_adjust)
                self.sleep(0.01)
                # Tell the caller to continue to the next loop iteration
                return current_direction, current_adjust, True

        # 2. Clean up any previous adjustments
        if current_adjust:
            self.send_key_up(current_adjust)
            current_adjust = None

        # 3. Determine the major new direction based on the angle
        if -45 <= angle <= 45:
            new_direction = 'w'
        elif 45 < angle <= 135:
            new_direction = 'd'
        elif -135 < angle <= -45:
            new_direction = 'a'
        else:
            new_direction = 's'

        # 4. Change direction if needed
        if current_direction != new_direction:
            self.log_info(self.tr('changed direction {angle} {current_direction} -> {new_direction}').format(angle=angle, current_direction=current_direction, new_direction=new_direction))
            if current_direction:
                self.mouse_up(key='right')
                self.send_key_up(current_direction)
                self.wait_until(self.in_combat, time_out=0.2)
            self.turn_direction(new_direction)
            self.send_key_down('w')
            self.wait_until(self.in_combat, time_out=0.2)
            self.mouse_down(key='right')
            current_direction = 'w'  # After turning, we always move forward
            self.wait_until(self.in_combat, time_out=1)

        return current_direction, current_adjust, False

    def in_team(self):
        c1 = self.find_one('char_1_text',
                           threshold=0.8)
        c2 = self.find_one('char_2_text',
                           threshold=0.8)
        c3 = self.find_one('char_3_text',
                           threshold=0.8)
        arr = [c1, c2, c3]
        # logger.debug(f'in_team check {arr}')
        current = -1
        exist_count = 0
        for i in range(len(arr)):
            if arr[i] is None:
                if current == -1:
                    current = i
            else:
                exist_count += 1
        if exist_count == 2 or exist_count == 1:
            self.logged_in = True
            return True, current, exist_count + 1
        else:
            return False, -1, exist_count + 1

        # Function to check if a component forms a ring

    def find_monthly_card(self):
        return self.find_one('monthly_card', threshold=0.65, horizontal_variance=0.05, vertical_variance=0.05)

    def handle_monthly_card(self):
        monthly_card = self.find_monthly_card()
        # self.screenshot('monthly_card1')
        if monthly_card is not None:
            # self.screenshot('monthly_card1')
            self.log_info(self.tr('monthly_card found click'))
            self.click_relative(0.50, 0.89)
            self.sleep(2)
            # self.screenshot('monthly_card2')
            self.click_relative(0.50, 0.89)
            self.sleep(2)
            self.wait_until(self.in_team_and_world, time_out=10,
                            post_action=lambda: self.click_relative(0.50, 0.89, after_sleep=1))
            # self.screenshot('monthly_card3')
            self.set_check_monthly_card(next_day=True)
        # logger.debug(f'check_monthly_card {monthly_card}')
        return monthly_card is not None

    @property
    def game_lang(self):
        if '鸣潮' in self.hwnd_title or self.is_browser():
            return 'zh_CN'
        elif 'Wuthering' in self.hwnd_title:
            return 'en_US'
        elif '鳴潮' in self.hwnd_title:
            return 'zh_TW'
        return 'unknown_lang'

    def open_esc_menu(self):
        self.send_key_down('alt')
        self.sleep(0.05)
        self.click_relative(0.95, 0.04)
        self.send_key_up('alt')
        self.sleep(0.5)

    # 素材获取左边的分类菜单。右边列表从 0.44 起，顶上提示条最左到 ~0.36，框到 0.33 为止不会读进来
    BOOK_MENU_BOX = (0.0, 0.10, 0.33, 0.97)

    def find_book_menu(self, name):
        """左边菜单里分类标题（「战歌重奏」等）的框，没读到返回 None。"""
        self.next_frame()
        texts = self.ocr(*self.BOOK_MENU_BOX, name='boss_book_menu') or []
        hits = [t for t in texts if BossBook.category_of_title(t.name) == name]
        return min(hits, key=lambda t: t.y) if hits else None

    def open_boss_book(self, name, after_sleep=2):
        self.log_info(self.tr('open_boss_book {name}').format(name=name))
        x = 0.24
        self.sleep(0.4)
        if name in BossBook.CATEGORY_TITLES:
            # 按字点：活动往菜单里插入口时写死的高度会点到别的分类
            menu = self.find_book_menu(name)
            if menu is not None:
                self.click_box(menu, after_sleep=after_sleep)
                if name == 'mengyan':
                    # 菜单最底下一项：旧代码也是点两下（第一下可能只把菜单滚上来）
                    self.click_box(self.find_book_menu(name) or menu, after_sleep=after_sleep)
                return
            logger.info(f'open_boss_book {name}: menu title not read, use fixed position')
        if name == 'ningsu':
            y = 0.4
        elif name == 'moni':
            y = 0.3
        elif name == 'qiangdi':
            y = 0.49
        elif name == 'wuyin':
            y = 0.73
        elif name == 'zhange':
            y = 0.61
        elif name == 'canxiang':
            y = 0.83
        elif name == 'mengyan':
            self.click_relative(0.356, 0.882, after_sleep=after_sleep)
            y = 0.86
        else:
            raise Exception(self.tr('unknown_lang {name}').format(name=name))
        self.click_relative(x, y, after_sleep=after_sleep, name=name)

    def openF2Book(self, feature="gray_book_all_monsters"):
        if hasattr(self, 'reset_to_false'):
            self.reset_to_false('opening book')
        self.ensure_main()
        book_key = self.key_config.get('Guidebook Key', self.key_config.get('索拉指南', 'f2'))
        self.log_info(self.tr('click {book_key} to open the book').format(book_key=book_key))
        if self.in_team_and_world():
            self.send_key(book_key, after_sleep=4)
            self.log_info(self.tr('send {book_key} key to open').format(book_key=book_key))
        if self.in_team_and_world():
            self.log_info(self.tr('send f2 key mouse key to open the book'))
            self.send_key_down('alt')
            self.sleep(0.05)
            self.click_relative(0.77, 0.05)
            self.sleep(0.02)
            self.send_key_up('alt')
            self.sleep(4)
        gray_book_boss = self.wait_book(feature)
        if not gray_book_boss:
            self.log_error(self.tr("can't find gray_book_boss, make sure f2 is the hotkey for book"), notify=True)
            raise Exception(self.tr("can't find gray_book_boss, make sure f2 is the hotkey for book"))
        self.sleep(2)
        self.click_box(gray_book_boss, after_sleep=1.5)
        return gray_book_boss

    def click_traval_button(self):
        for feature_name in ['fast_travel_custom', 'gray_teleport', 'remove_custom']:
            if self.find_one(feature_name, threshold=0.7):
                self.sleep(0.5)
                feature = self.find_one(feature_name, threshold=0.7)
                if not feature:
                    continue
                self.click(feature, after_sleep=1)
                if feature.name == 'fast_travel_custom':
                    if confirm := self.wait_feature(
                            ['confirm_btn_hcenter_vcenter', 'confirm_btn_highlight_hcenter_vcenter'],
                            raise_if_not_found=False,
                            threshold=0.6,
                            time_out=2):
                        self.click(0.49, 0.55, after_sleep=0.5)  # 点击不再提醒
                        self.click(confirm, after_sleep=0.5)
                        self.click_confirm()
                if feature.name != 'remove_custom':
                    self.wait_click_skip_dialog_confirm()
                return True

    def click_confirm(self, timeout=1):
        self.wait_click_feature(
            ['confirm_btn_hcenter_vcenter', 'confirm_btn_highlight_hcenter_vcenter'],
            relative_x=-1, raise_if_not_found=False,
            threshold=0.6,
            time_out=1)

    def click_skip_dialog_confirm(self):
        skip_dialog_confirm = self.find_one(
            ['confirm_btn_hcenter_vcenter', 'confirm_btn_highlight_hcenter_vcenter'],
            horizontal_variance=0.1,
            vertical_variance=0.1,
        )
        if not skip_dialog_confirm:
            return False

        skip_dialog_check = self.find_one(
            'skip_dialog_check',
            horizontal_variance=0.1,
            vertical_variance=0.1,
        )
        if not skip_dialog_check:
            check_feature = self.get_feature_by_name('skip_dialog_check')
            wide_check_template = cv2.resize(
                check_feature.mat,
                (0, 0),
                fx=WIDE_MODE_UI_SCALE,
                fy=WIDE_MODE_UI_SCALE,
                interpolation=cv2.INTER_AREA,
            )
            skip_dialog_check = self.find_one(
                'skip_dialog_check',
                box=self.box_of_screen(0.35, 0.45, 0.55, 0.65),
                template=wide_check_template,
            )
        if not skip_dialog_check:
            return False

        logger.info('confirm dialog exists, click confirm')
        self.sleep(0.5)
        self.click(skip_dialog_check)
        self.sleep(0.5)
        self.click(skip_dialog_confirm)
        self.sleep(0.2)
        return True

    def wait_click_skip_dialog_confirm(self, time_out=3):
        return self.wait_until(
            self.click_skip_dialog_confirm,
            time_out=time_out,
            raise_if_not_found=False,
        )

    def click_team_challenge(self):
        self.wait_click_feature('team_start_challenge', raise_if_not_found=True, click_after_delay=0.5, after_sleep=1)
        self.wait_click_skip_dialog_confirm()

    # 等级页「单人挑战」和队伍页「开启挑战」都在右下这一条
    CHALLENGE_BUTTON_BOX = (0.60, 0.86, 0.98, 0.97)
    CHALLENGE_TEXTS = (('team', re.compile(r'开启挑战|開啟挑戰|Start', re.IGNORECASE)),
                       ('level', re.compile(r'单人挑战|單人挑戰|Solo', re.IGNORECASE)))

    def find_challenge_button(self):
        """当前停在等级页/队伍页时返回 (页名, 按钮框)，否则 None。

        外文客户端认不出字时用 team_start_challenge 模板兜底（它在两页的主按钮上都能匹配，点哪页都是往前走）。
        """
        texts = self.ocr(*self.CHALLENGE_BUTTON_BOX, name='challenge_buttons') or []
        for page, pattern in self.CHALLENGE_TEXTS:
            box = next((t for t in texts if pattern.search(t.name)), None)
            if box is not None:
                return page, box
        box = self.find_one('team_start_challenge')
        return ('button', box) if box is not None else None

    def click_challenge_until_in_world(self, time_out=120, retry_interval=4):
        """从等级页/队伍页一路点到进副本，点击丢了会补点。

        2026-11-19 实机：点完「单人挑战」后停在等级页没翻页，旧代码只点一次就干等 120 秒。
        这里每轮看一眼停在哪页：换了页 1 秒后点这一页的按钮；同一页超过 retry_interval 秒还没走就补点。
        结晶波片(含备用)不足时进队伍界面前会弹「无法获取奖励, 是否继续进入」，每轮都先勾「不再提示」点确认。
        """
        start = time.time()
        last_click, last_page, clicks = 0.0, None, 0
        while time.time() - start < time_out:
            self.next_frame()
            if self.in_team_and_world():
                self.sleep(0.5)
                return True
            if self.click_skip_dialog_confirm():
                continue
            found = self.find_challenge_button()
            now = time.time()
            if found is not None:
                page, box = found
                wait = 1.0 if page != last_page else retry_interval
                if now - last_click >= wait:
                    if page == last_page:
                        self.log_info(self.tr('challenge: {button} did not respond, click again').format(button=box.name))
                    self.click_box(box, after_sleep=0.5)
                    last_click, last_page, clicks = time.time(), page, clicks + 1
                    continue
            self.sleep(0.5)
        raise Exception(self.tr('challenge: not in world after {seconds}s').format(seconds=time_out))

    def wait_click_travel(self):
        self.wait_until(self.click_traval_button, raise_if_not_found=True, time_out=10)

    def wait_book(self, feature="gray_book_all_monsters", time_out=3):
        gray_book_boss = self.wait_until(
            lambda: self.find_one(feature, box='box_gray_book',
                                  threshold=0.3),
            time_out=time_out, settle_time=1)
        logger.info(f'found gray_book_boss {gray_book_boss}')
        # if self.debug:
        #     self.screenshot(feature)
        return gray_book_boss

    def check_main(self):
        if not self.in_team()[0]:
            self.click_relative(0, 0)
            self.send_key('esc')
            self.sleep(1)
            if not self.in_team()[0]:
                raise Exception(self.tr('must be in game world and in teams'))
        return True

    # 素材获取右边整块面板，从体力栏下面到屏幕底，不按「列表大概从哪开始」划窗口：
    # 上方提示条多了少了、列表起点跟着上下挪都在框里，找名字、找按钮都在整块面板里找，再靠滚轮把目标滚出来。
    # 横向只避开左边分类菜单（≤0.34，里面也有「战歌重奏」几个字）。
    BOOK_NAME_BOX = (0.40, 0.10, 0.80, 0.97)
    # 每行右侧「直接挑战/前往」按钮所在的竖条（模板认形状）
    BOOK_BUTTON_BOX = (0.86, 0.10, 0.985, 0.97)
    # 按钮上的字所在的竖条（左边是奖励图标和「直接挑战」前面的地图钉，都不是字）
    BOOK_LABEL_BOX = (0.84, 0.10, 0.985, 0.97)
    # 同一行：按钮中心在名字框顶往下 0~0.095 屏高以内，归离它最近的那个名字（量的是名字和按钮的相对距离，和列表从哪开始无关）。
    # 2K 实测「前往」行 ≈0.05；「限时提前开放」分组的行按钮更靠下 ≈0.065；
    # 分组标题到下一行按钮中心 ≥0.11，所以分组标题分不到按钮
    BOOK_ROW_SPAN = 0.095
    # 模板框和按钮字中心差不到这么多算同一个按钮（按钮高 ≈0.037，两行差 0.14）
    BOOK_BUTTON_MERGE = 0.03
    # 滚轮 1 格约 0.0244 屏高，一行 Boss 约 0.142。一次最多翻 10 格（≈1.7 行）；
    # 列表露出来的部分越矮翻得越少：一行从名字顶到按钮底 ≈0.09，翻的距离不超过「露出高度 - 一行」，每一行都有一屏能整行露出来
    BOOK_SCROLL_TICKS = 10
    BOOK_TICK_H = 0.0244
    BOOK_ROW_FULL_H = 0.10
    # 翻页后高度差不到这么多、字也差不多的文字不跟着列表滚：是列表外面的提示条
    BOOK_FIXED_DY = 0.003
    # 滚轮放在每行左半边名字那一段。中间那排奖励图标（x≈0.63~0.83）自己能横着滚，滚轮落在上面只滚图标、
    # 列表不动，会被当成翻到底而报错（2026-11-22 实机截图）。列表没动时换到头像那一段（x≈0.39~0.45）再滚一次，
    # 两处都不动才算到头；换过去能滚就以后都用那一处
    BOOK_SCROLL_XS = (0.56, 0.42)

    def _read_book_buttons(self):
        """右边的按钮，从上往下 [BookButton]。

        模板认按钮右端的形状（「前往」「直接挑战」都是同一种黑色圆角按钮），OCR 认按钮上的字分出是哪种；
        模板没认出来的（比如悬停高亮）靠字补上。同一高度的合成一个，点击用模板框（实机点过）。
        """
        found = [BookButton((b.y + b.height / 2) / self.height, b, None)
                 for b in self.find_feature('boss_proceed', box=self.box_of_screen(*self.BOOK_BUTTON_BOX),
                                            threshold=0.8) or []]
        for t in self.ocr(*self.BOOK_LABEL_BOX, name='boss_book_buttons') or []:
            kind = BossBook.button_kind(t.name)
            if kind is None:
                continue
            cy = (t.y + t.height / 2) / self.height
            i = next((i for i, b in enumerate(found) if abs(b.cy - cy) <= self.BOOK_BUTTON_MERGE), None)
            if i is None:
                found.append(BookButton(cy, t, kind))
            else:
                found[i] = found[i]._replace(kind=kind)
        return sorted(found, key=lambda b: b.cy)

    def _note_fixed_texts(self, scan, names, buttons):
        """和上一次读的比：列表动了（按钮位置变了），这几行字却一点没挪、字也差不多 → 是列表外面不跟着滚的提示条。

        记下它们的高度，以后这几个高度上的字都不当 Boss 名（倒计时、次数这种字在变的也认得出）。
        不认识的提示条也能这样分出来，不靠「上面有几条提示」去算列表从哪开始。
        """
        cur = [(BossBook.norm(t.name), t.y / self.height) for t in names]
        cur_buttons = tuple(round(b.cy, 3) for b in buttons)
        prev = scan.get('prev')
        scan['prev'] = (cur, cur_buttons)
        if not prev or not prev[1] or not cur_buttons or cur_buttons == prev[1]:
            return  # 同一屏多读一帧 / 翻到底了：列表没动，分不出谁是固定的
        for text, y in cur:
            if any(abs(y - py) <= self.BOOK_FIXED_DY and BossBook.similar_text(text, ptext) for ptext, py in prev[0]):
                if not any(abs(y - fy) <= self.BOOK_FIXED_DY for fy in scan['fixed_y']):
                    scan['fixed_y'].append(y)
                    logger.info(f'boss book: text at y={y:.3f} does not scroll ({text}), not a boss row')

    def _read_book_rows(self, scan=None):
        """当前画面列表里的行，返回 BookPage(从上往下的 [BookRow], 签名, 列表里所有字的高度)。

        不靠位置、不靠分组顺序：每个按钮归它上方最近的名字（同一行），分到按钮的才是能点的 Boss 行。
        分组标题（地区名、「限时提前开放」）分不到按钮；提示条（「剩余…次数」「筛选…」、翻页时不跟着滚的字）连名字都不算。
        没按钮的名字还可能是被列表上沿/下沿裁掉一半的行（翻页后会完整读到）。
        聚落行下面一行是「已击败残象：0/36」这种进度，按进度认，不靠「聚落」两个字（外文客户端也能分）。
        签名只拿按钮和 Boss 行算（翻页后不变 = 到底了）：提示条上的倒计时/次数每帧在变，不能把「到底」判断搅乱。
        """
        texts = sorted(self.ocr(*self.BOOK_NAME_BOX, name='boss_book_names') or [], key=lambda b: b.y)
        buttons = self._read_book_buttons()
        cjk_only = self.game_lang == 'zh_CN'
        fixed_y = []
        if scan is not None:
            self._note_fixed_texts(scan, [t for t in texts if BossBook.is_candidate_text(t.name, cjk_only)], buttons)
            fixed_y = scan['fixed_y']
        texts = [t for t in texts if not any(abs(t.y / self.height - fy) <= self.BOOK_FIXED_DY for fy in fixed_y)]
        progress = [t.y / self.height for t in texts
                    if BossBook.is_progress_text(t.name) and not BossBook.is_banner_text(t.name)]
        names = [t for t in texts if BossBook.is_candidate_text(t.name, cjk_only) and not BossBook.is_banner_text(t.name)]
        owner = {}
        for button in buttons:
            above = [i for i, t in enumerate(names) if 0 <= button.cy - t.y / self.height <= self.BOOK_ROW_SPAN]
            if above:
                owner.setdefault(max(above, key=lambda i: names[i].y), button)
        rows = []
        for i, t in enumerate(names):
            ty = t.y / self.height
            button = owner.get(i)
            nest = '聚落' in t.name or any(0.03 <= py - ty <= 0.10 for py in progress)
            rows.append(BookRow(t.name, ty, button.box if button else None, nest, button.kind if button else None))
        sig = (tuple(round(b.cy, 2) for b in buttons), tuple(BossBook.norm(r.name) for r in rows if r.btn is not None))
        return BookPage(rows, sig, [t.y / self.height for t in texts if not BossBook.is_banner_text(t.name)])

    @staticmethod
    def _book_scroll_y(page):
        """滚轮放在列表上：本屏 Boss 行的中间高度。上方提示条再多，滚轮也不会落到提示条上滚不动。"""
        ys = [r.y for r in page.rows if r.btn is not None] or [r.y for r in page.rows]
        if not ys:
            return 0.6
        return min(0.92, (min(ys) + max(ys)) / 2 + 0.04)

    def _book_scroll_ticks(self, page):
        """这一屏往下翻几格。列表露出来的高度按这一屏估：最上面一行 Boss 名到最下面的字（只会估矮不会估高）。"""
        ys = [r.y for r in page.rows if r.btn is not None] or [r.y for r in page.rows]
        if not ys or not page.text_ys:
            return self.BOOK_SCROLL_TICKS
        shown = max(page.text_ys) - min(ys)
        return max(2, min(self.BOOK_SCROLL_TICKS, int((shown - self.BOOK_ROW_FULL_H) / self.BOOK_TICK_H)))

    @staticmethod
    def _target_rows(rows, category, target):
        """本页名字对得上 target 的行 [(距离, 行)]。只差一个字的另一个 Boss（如 xx·今州 / xx·梦州）不算。"""
        others = [n for n in BossBook.known_names(category) if not BossBook.same_name(n, target)]
        hits = []
        for row in rows:
            d = BossBook.match_distance(row.name, target)
            if d is None or (d > 0 and BossBook.nearer_to_other(row.name, d, others)):
                continue
            hits.append((d, row))
        return hits

    @staticmethod
    def _pick_book_row(rows, category, target, serial, seen, nests):
        """本页能点的目标行 BookRow，没有返回 None。"""
        if target:
            # 同一个 Boss 可能出现两次（「限时提前开放」里一行、地区里一行）：取有按钮的，名字最像的，再取靠上的
            hits = [(d, row) for d, row in BaseWWTask._target_rows(rows, category, target) if row.btn is not None]
            return min(hits, key=lambda h: (h[0], h[1].y))[1] if hits else None
        eligible = [s for s in seen if not (category == 'mengyan' and BossBook.norm(s) in nests)]
        if serial < 1 or len(eligible) < serial:
            return None
        want = eligible[serial - 1]
        return next((row for row in rows if row.btn is not None and BossBook.same_name(row.name, want)), None)

    def _scan_book_page(self, scan, category, target, frames=1):
        """读当前这一屏（frames 帧，记进 scan），返回第一帧的 BookPage。"""
        first = None
        for i in range(frames):
            if i:
                self.sleep(0.3)
            self.next_frame()
            page = self._read_book_rows(scan)
            first = first or page
            for row in page.rows:
                if row.btn is None:
                    continue
                key = BossBook.norm(row.name)
                if row.nest:
                    scan['nests'].add(key)
                scan['reads'][key] = scan['reads'].get(key, 0) + 1
                if not any(BossBook.same_name(row.name, s) for s in scan['seen']):
                    scan['seen'].append(row.name)
            if target:
                # 名字下面还有别的能点的行（说明这一行的按钮该露出来了）却没有按钮：多半是没解锁，报错时说清楚
                for _, row in self._target_rows(page.rows, category, target):
                    if row.btn is None and any(r.btn is not None and r.y > row.y for r in page.rows):
                        scan['no_button'].add(BossBook.display_name(row.name))
        return first

    @staticmethod
    def _new_book_scan(keep=None):
        # seen: 有按钮的行从上往下去重；nests: 梦魇聚落（规范化名字），特异梦魇按序号数时跳过；
        # reads: 规范化文本 -> 读到次数，学新名字只认读到 2 次的，防 OCR 错字进下拉框；
        # no_button: 看到了但没按钮的目标；fixed_y: 不跟着列表滚的提示条高度；prev: 上一次读到的字和按钮（认提示条用）
        # scroll_xs: 滚轮放哪（第一个滚不动换第二个，能滚的那个换到前面）
        scan = {'seen': [], 'nests': set(), 'reads': {}, 'no_button': set(), 'fixed_y': [], 'prev': None,
                'scroll_xs': list(BaseWWTask.BOOK_SCROLL_XS)}
        if keep:
            scan['no_button'] |= keep['no_button']
            for key in ('fixed_y', 'prev', 'scroll_xs'):
                scan[key] = keep[key]
        return scan

    def _scroll_book(self, scan, page, ticks, x=None):
        self.scroll_relative(x or scan['scroll_xs'][0], self._book_scroll_y(page), ticks)
        self.sleep(0.6)

    def _book_scroll_retry(self, scan, page, ticks, read):
        """滚了列表却没动：换个地方再滚一次。动了返回 read() 的新结果并记住这个地方，还是不动返回 None。"""
        alt = scan['scroll_xs'][1]
        logger.info(f'boss book: list did not move, scroll again at x={alt}')
        self._scroll_book(scan, page, ticks, x=alt)
        new = read()
        if new.sig == page.sig:
            return None
        scan['scroll_xs'].reverse()
        return new

    def _scroll_book_to_top(self, scan, page, max_scrolls=8):
        """往上滚到列表顶。返回 (动过没有, 最后一屏的签名)。

        一次都没滚动时换个地方再试一次再认定本来就在顶上（滚轮可能落在能横着滚的奖励图标上）。
        """
        def read():
            self.next_frame()
            return self._read_book_rows(scan)

        moved = False
        for _ in range(max_scrolls):
            self._scroll_book(scan, page, self.BOOK_SCROLL_TICKS * 3)
            new = read()
            if new.sig == page.sig and not moved:
                new = self._book_scroll_retry(scan, page, self.BOOK_SCROLL_TICKS * 3, read) or new
            if new.sig == page.sig:
                break
            moved, page = True, new
        return moved, page.sig

    def click_book_target_by_name(self, category, target='', serial=0, max_pages=30):
        """在已打开的素材获取分类里找 Boss，点它那一行自己的「直接挑战/前往」，返回点到的 OCR 名字。

        target 非空按名字模糊匹配（容 OCR 错 1 个字）；为空时数第 serial 个有按钮的行
        （特异梦魇不数聚落，外文客户端走这条）。不依赖行号、分组顺序、列表总数、滚动条位置，也不依赖列表从哪开始：
        上面插了「限时提前开放」分组或任意多条提示、提示文字在变、Boss 行整体上移下移，都是在整块面板里按名字找，
        找到名字再点它下面同一行的按钮；滚轮跟着列表走，翻页步长按露出来的列表高度定。
        先看当前这一屏（列表可能停在上次的位置），没有就滚到顶，再一屏屏往下翻；
        翻到底还没有就报错（列出读到的名字 / 说明哪一行没按钮），不会随便点一个；最多翻 max_pages 屏（按 10 格一屏算）。
        """
        self.sleep(0.5)
        scan = self._new_book_scan()
        # 最上面两行只在第一屏出现，多读一帧凑够 2 次（新 Boss 一般排在最上面）
        page = self._scan_book_page(scan, category, target, frames=2)
        hit = self._pick_book_row(page.rows, category, target, serial, scan['seen'], scan['nests']) if target else None
        if hit is None:
            moved, sig = self._scroll_book_to_top(scan, page)
            if moved:
                logger.info('boss book: list was not at top, scrolled up and read again')
                scan = self._new_book_scan(keep=scan)
                page = self._scan_book_page(scan, category, target, frames=2)
            hit = self._pick_book_row(page.rows, category, target, serial, scan['seen'], scan['nests'])
        sig = page.sig
        budget = max_pages * self.BOOK_SCROLL_TICKS
        while hit is None and budget > 0:
            ticks = min(self._book_scroll_ticks(page), budget)
            budget -= ticks
            self._scroll_book(scan, page, -ticks)
            new = self._scan_book_page(scan, category, target)
            if new.sig == sig:
                # 列表没动：先换个地方再滚一次（滚轮可能被奖励图标那排吃掉了），还不动才是到底
                new = self._book_scroll_retry(scan, page, -ticks,
                                              lambda: self._scan_book_page(scan, category, target)) or new
            page = new
            hit = self._pick_book_row(page.rows, category, target, serial, scan['seen'], scan['nests'])
            if hit is None and page.sig == sig:
                break  # 两处都翻不动了，到底
            sig = page.sig
        self._learn_book_names(category, scan['seen'], scan['reads'], scan['nests'])
        if hit is not None:
            label = BossBook.option_label(category, BossBook.display_name(hit.name))
            button = BossBook.BUTTON_LABEL[hit.kind]
            self.book_button_kind = hit.kind
            self.log_info(self.tr('boss book: clicked {name} ({button})').format(name=label, button=button))
            logger.info(f'boss book: row {hit.name} y={hit.y:.3f} button={hit.kind} box={hit.btn}')
            self.draw_boxes(boxes=hit.btn, color="red")
            self.click(hit.btn, after_sleep=1)
            return hit.name
        wanted = BossBook.option_label(category, target) if target else f'#{serial}'
        if scan['no_button']:
            raise Exception(self.tr('boss book: {wanted} is in the list but has no Go/Challenge button').format(
                wanted=wanted))
        names = '、'.join(BossBook.option_label(category, BossBook.display_name(s)) for s in scan['seen'])
        raise Exception(self.tr('boss book: {wanted} not found, list has: {names}').format(wanted=wanted, names=names))

    def _learn_book_names(self, category, seen, reads, nests):
        if self.game_lang != 'zh_CN':
            return  # 下拉框是中文名，外文客户端读到的名字不往里记
        stable = [s for s in seen if reads.get(BossBook.norm(s), 0) >= 2 and BossBook.norm(s) not in nests]
        new_names = BossBook.learn_names(category, stable)
        if new_names:
            self.log_info(self.tr('boss book: new bosses found {names}, restart to pick them').format(
                names='、'.join(new_names)), notify=True)

    def _find_book_scroll_top(self):
        box = self.box_of_screen(0.969, 0.191, 0.978, 0.271, name="bar")
        self.draw_boxes(boxes=box, color="blue")
        min_width = self.width_of_screen(5 / 2560)
        min_height = self.height_of_screen(10 / 1440)
        boxes = find_color_rectangles(self.frame, book_bar_color, min_width, min_height, threshold=0.8, box=box)
        if not boxes:
            return 424 / 2160
        bar = boxes[0]
        self.draw_boxes(boxes=bar, color="red")
        return bar.y / self.height

    def click_on_book_target(self, serial_number: int, total_number: int, structure: list[int] = None):
        def get_cross_count(structure, sn):
            current_sum = 0
            cross_count = 0
            for s in structure:
                current_sum += s
                if sn > current_sum:
                    cross_count += 1
                else:
                    break
            return cross_count

        self.sleep(0.5)
        bar_bottom = 0.8806
        bar_x = 0.9730
        header_h = 0.028  # calibrated header height (~40px @1440), replaces separator=0.01
        cross_count = 0
        container_max_rows = 4
        target_index = -1

        bar_top = self._find_book_scroll_top()

        if serial_number <= container_max_rows:
            target_index = serial_number - 1
        else:
            calib_cross = get_cross_count(structure, serial_number) if structure else 0
            calib_container_h = (bar_bottom - bar_top - (len(structure) - 1) * header_h) if structure else (
                        bar_bottom - bar_top)
            calib_item_h = calib_container_h / total_number if total_number else 0
            calib_y = min(bar_top + calib_item_h * serial_number + calib_cross * header_h, bar_bottom)
            to_click_y = calib_y
            item_h = calib_item_h
            self.click(bar_x, to_click_y, after_sleep=1)
        btns = self.find_feature('boss_proceed', box=self.box_of_screen(0.9113, 0.229, 0.9613, 0.861), threshold=0.8)
        # adaptive retry: closed-loop correction for residual underscroll (found 3/4 with low max_y)
        if not target_index > -1 and btns and len(btns) in (3, 4) and structure and serial_number > container_max_rows:
            max_y = max(b.y / self.height for b in btns)
            if max_y < 0.73:
                retry_y = min(to_click_y + item_h * 0.55, bar_bottom) if 'item_h' in locals() and item_h else min(
                    to_click_y + 0.018, bar_bottom)
                self.click(bar_x, retry_y, after_sleep=1)
                btns = self.find_feature('boss_proceed', box=self.box_of_screen(0.9113, 0.229, 0.9613, 0.861),
                                         threshold=0.8)
        if not btns:
            raise Exception(self.tr("can't find boss_proceed"))
        if target_index > -1:
            if target_index < len(btns):
                target = btns[target_index]
            else:
                # Fallback: not enough visible rows, scroll with calibrated header
                calib_cross2 = get_cross_count(structure, serial_number) if structure else 0
                calib_h2 = (bar_bottom - bar_top - (len(structure) - 1) * header_h) if structure else (
                            bar_bottom - bar_top)
                calib_y2 = min(bar_top + (calib_h2 / total_number) * serial_number + calib_cross2 * header_h,
                               bar_bottom) if total_number else bar_bottom
                self.click(bar_x, calib_y2, after_sleep=1)
                btns = self.find_feature('boss_proceed', box=self.box_of_screen(0.9113, 0.229, 0.9613, 0.861),
                                         threshold=0.8)
                if not btns:
                    raise Exception(self.tr("can't find boss_proceed after scroll"))
                target = max(btns, key=lambda box: box.y)
        else:
            target = max(btns, key=lambda box: box.y)
        self.draw_boxes(boxes=target, color="red")
        self.click(target, after_sleep=1)
        feature = self.wait_feature(['fast_travel_custom', 'gray_teleport', 'remove_custom', *TEAM_SCREEN_FEATURES],
                                    time_out=10, settle_time=0.5, raise_if_not_found=True)
        return feature.name in TEAM_SCREEN_FEATURES

    def wait_after_book_click(self):
        """点了「直接挑战/前往」之后：队伍/等级界面返回 True，地图传送界面返回 False，10 秒都没等到就报错。

        「前往」打开地图传送；「限时提前开放」里的「直接挑战」直接进挑战界面。
        """
        feature = self.wait_feature(['fast_travel_custom', 'gray_teleport', 'remove_custom', *TEAM_SCREEN_FEATURES],
                                    time_out=10, settle_time=0.5, raise_if_not_found=False)
        if not feature:
            button = BossBook.BUTTON_LABEL[getattr(self, 'book_button_kind', None)]
            raise Exception(self.tr('boss book: clicked {button}, but neither map nor challenge screen opened').format(
                button=button))
        return feature.name in TEAM_SCREEN_FEATURES

    def change_time_to_night(self):
        logger.info('change time to night')
        self.send_key("esc")
        self.sleep(1)
        self.click_relative(0.71, 0.96)
        self.sleep(2)
        self.click_relative(0.19, 0.14)
        self.sleep(1)

        # 调整时间到晚上
        for _ in range(3):
            self.click_relative(0.82, 0.53)
            self.sleep(1)

        self.click_relative(0.52, 0.90)
        self.sleep(6)
        self.send_key("esc")
        self.sleep(1)
        self.send_key("esc")
        self.sleep(1)

    def jump(self, after_sleep=0.01):
        self.send_key(self.key_config.get('Jump Key'), after_sleep=after_sleep)


book_bar_color = {
    "r": (190, 255),
    "g": (190, 255),
    "b": (190, 255),
}

echo_color = {
    'r': (200, 255),  # Red range
    'g': (150, 220),  # Green range
    'b': (130, 170)  # Blue range
}


def calculate_angle_clockwise(box1, box2):
    """
    Calculates angle (radians) from horizontal right to line (x1,y1)->(x2,y2).
    Positive clockwise, negative counter-clockwise.
    """
    x1, y1 = box1.center()
    x2, y2 = box2.center()
    dx = x2 - x1
    dy = y2 - y1
    # math.atan2(dy, dx) gives angle from positive x-axis, positive CCW.
    # Negate for positive CW convention.

    degree = math.degrees(math.atan2(dy, dx))
    if degree < 0:
        degree += 360
    return degree


lower_white = np.array([244, 244, 244], dtype=np.uint8)
lower_white_none_inclusive = np.array([240, 240, 240], dtype=np.uint8)
upper_white = np.array([255, 255, 255], dtype=np.uint8)
black = np.array([0, 0, 0], dtype=np.uint8)


def isolate_white_text_to_black(cv_image):
    """
    Converts pixels in the near-white range (244-255) to black,
    and all others to white.
    Args:
        cv_image: Input image (NumPy array, BGR).
    Returns:
        Black and white image (NumPy array), where matches are black.
    """
    match_mask = cv2.inRange(cv_image, black, lower_white_none_inclusive)
    output_image = cv2.cvtColor(match_mask, cv2.COLOR_GRAY2BGR)

    return output_image


def convert_bw(cv_image):
    match_mask = cv2.inRange(cv_image, lower_white, upper_white)
    output_image = cv2.cvtColor(match_mask, cv2.COLOR_GRAY2BGR)
    return output_image


lower_icon_white = np.array([210, 210, 210], dtype=np.uint8)
upper_icon_white = np.array([255, 255, 255], dtype=np.uint8)


def convert_dialog_icon(cv_image):
    match_mask = cv2.inRange(cv_image, lower_icon_white, upper_icon_white)
    output_image = cv2.cvtColor(match_mask, cv2.COLOR_GRAY2BGR)
    return output_image


def isolate_gua_strokes(image):
    """
    Keeps the white strokes of Buling's hexagram glyphs (卦象 艮/震) and drops the colored halo behind them.

    A pixel's minimum channel is only high when the pixel is close to white, and the top-hat keeps thin
    bright strokes while removing the smooth glow, so plain and highlighted glyphs look the same.

    Args:
        image (np.array): The input BGR image from OpenCV.

    Returns:
        np.array: Single channel 8-bit stroke map.
    """
    gray = image.min(axis=2) if image.ndim == 3 else image
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
    return cv2.morphologyEx(gray, cv2.MORPH_TOPHAT, kernel)


def binarize_for_matching(image, threshold=244):
    """
    Converts a colored image to a binary image based on a brightness threshold.

    The rule is: pixels with a value of 240-255 become pure white (255),
    and all other pixels become pure black (0).

    Args:
        image (np.array): The input BGR image from OpenCV.

    Returns:
        np.array: The resulting binary image (single channel, 8-bit).
    """
    # Convert the image to grayscale for a single brightness value per pixel.
    # This is more robust than checking individual R, G, B channels.

    gray_image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    # Apply the binary threshold.
    # Pixels > 239 will be set to 255 (white).
    # Pixels <= 239 will be set to 0 (black).
    # cv2.THRESH_BINARY is the type of thresholding we want.
    _, binary_image = cv2.threshold(gray_image, threshold, 255, cv2.THRESH_BINARY)
    return binary_image
