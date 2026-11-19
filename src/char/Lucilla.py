import time

from src.char.BaseChar import BaseChar, SwitchPriority


class Lucilla(BaseChar):
    """洛瑟菈(拍照攒相片, 大招进「追忆」)。

    机制(帧表 + 攻略视频 + 绯洛穗录屏):
    - 长按 E / 普攻第 3 段展开聚焦环, 判定分三段: 长判1 松手=成功, 长判2 松手=失败, 长判3 松手或按到底=成功。
      成功是「追光/可圈可点」(+50 印象 = 1 张相片, E 追光另给 +20 协奏), 失败是「补光/乏善可陈」(+25)。
      录屏量过第一个金色窗口在按下后 0.86~1.10 秒, 但 E 起手一被前一个动作缓存就整个往后挪, 定时松手很容易落进长判2。
      攻略作者的用法是「按住不动等它跑满, 自动打出强化版」: E 按到底(帧表 92F+起手延迟)、A3 按到底(86F)都必定成功。
    - 追光的印象在追光第 54F 才到账, 这之前切人会打断、能量不涨。
    - 变奏入场直接给 2 张相片(+100), 再一发追光就能开大。没变奏就 E + A1A2 长按 A3 慢慢攒。
    - 大招演出约 4.5 秒(隐藏 HUD), 然后进「追忆」: E/R 都放不了, 按住普攻依次打溯念留形 1/2/3 段,
      第 3 段一直按着持续消耗相片, 松手或打完自动「断舍离」(+20 协奏, 期间不能切人, 隐藏 HUD ~1.4 秒), 然后退出追忆。
      中途松手会少打相片, 所以要一直按住到断舍离。
    """
    FOCUS_HOLD_TIME: float = 2.0
    R_AFTER_FOCUS: float = 1.4
    A1_TO_A2: float = 0.45
    A2_TO_A3: float = 0.3
    A3_HOLD_TIME: float = 2.1
    INTRO_SETTLE: float = 0.9
    CHARGE_TIME_OUT: float = 9.0
    LIBERATION_CD_SKIP: float = 1.5
    LIB_ANIMATION_CAP: float = 6.0
    ZHUIYI_HOLD_CAP: float = 8.0
    DSL_BACK_CAP: float = 3.0

    def do_perform(self):
        if not self.perform_combat():
            self.switch_next_char()

    def perform_combat(self):
        """攒相片 -> 大招可用就放大招打完追忆。放出了大招(已切人)返回 True。"""
        self.task.wait_until(lambda: self.task.in_team()[0], time_out=0.8)
        settle = (self.INTRO_SETTLE if self.has_intro else 0.3) - self.field_time()
        if settle > 0:
            self.sleep(settle, check_combat=False)
        self.task.next_frame()
        # 延奏和格洛犸图声骸联动, 入场先放声骸(大招 CD 中提前走也要放到)
        self.click_echo(time_out=0)

        if self.try_liberation():
            return True

        while self.field_time() < self.CHARGE_TIME_OUT:
            # 能量满但放不出(短CD / 切回来UI没渲染好) -> 别空攒, 切人
            if self.energy_full() and not self.liberation_available():
                self.logger.info('Lucilla energy full but liberation not castable, switch')
                break
            # 能量没满且大招长 CD -> 不攒满, E 好了打一发追光(印象和协奏都留着)再走
            if not self.liberation_available() and self.task.get_cd('liberation') > self.LIBERATION_CD_SKIP:
                self.logger.info('Lucilla liberation on long cd, switch to save time')
                if self.resonance_available():
                    self.focus_shot()
                    self.sleep(1.0)
                break
            if self.try_liberation():
                return True
            if self.resonance_available():
                self.focus_shot()
                # 追光的印象松手后约 0.9 秒才到账, 这段时间等 R 亮
                if self.task.wait_until(self.liberation_available, time_out=self.R_AFTER_FOCUS):
                    continue
            else:
                # E 没好: A1 A2 长按 A3 拿一张相片; 还在追忆里的话就是打到断舍离
                self.focus_a3()
            self.task.next_frame()
        return False

    def focus_shot(self):
        """长按 E 到聚焦环跑满, 自动出追光。不赌金色窗口: E 起手被缓存时定时松手会落进补光。"""
        self.hold_key(self.get_resonance_key(), self.FOCUS_HOLD_TIME)
        self.record_resonance_use()
        self.logger.info(f'Lucilla focus E held {self.FOCUS_HOLD_TIME:.2f}s')

    def focus_a3(self):
        """A1 -> A2 -> 长按 A3 到聚焦环跑满(可圈可点, +50 印象)。"""
        self.click()
        self.sleep(self.A1_TO_A2)
        self.click()
        self.sleep(self.A2_TO_A3)
        self.hold_mouse(self.A3_HOLD_TIME)

    def try_liberation(self):
        """大招就绪则放(顺带先放声骸)并打完追忆、切人, 返回是否放出。"""
        if not self.liberation_available():
            return False
        if self.echo_available():
            self.click_echo(time_out=0)
        self.perform_liberation()
        self.switch_next_char()
        return True

    def energy_full(self):
        """相片是否已满(大招图标高亮, 忽略 CD)。"""
        return self.available('liberation', check_color=True, check_cd=False)

    def perform_liberation(self):
        """放大招 -> 等演出 -> 按住普攻打追忆直到断舍离。

        不用 BaseChar.click_liberation(): 追忆形态下 HUD 判断不稳, 它的等待会误判。
        这里放出判据是「HUD 消失」或「大招图标灭」。
        """
        if not self.task.use_liberation:
            return
        start = time.time()
        hud_gone_at = None
        while time.time() - start < 1.5:
            if not self.task.in_team()[0]:
                hud_gone_at = time.time()
                break
            if not self.liberation_available():
                break
            self.send_liberation_key()
            self.sleep(0.1, check_combat=False)
        self.record_liberation_use()
        self.task.in_liberation = True
        try:
            # 演出: HUD 回来就结束; HUD 读不到就按 4.5 秒保底
            if hud_gone_at is None:
                hud_gone_at = time.time()
            self.task.wait_until(lambda: time.time() - hud_gone_at > 1.0 and self.task.in_team()[0],
                                 time_out=self.LIB_ANIMATION_CAP)
            if not self.task.in_team()[0]:
                left = 4.5 - (time.time() - hud_gone_at)
                if left > 0:
                    self.sleep(left, check_combat=False)
            self.add_freeze_duration(hud_gone_at, time.time() - hud_gone_at)
        finally:
            self.task.in_liberation = False
        self.logger.info('Lucilla liberation animation end, hold attack until 断舍离')
        self.hold_until_dsl()

    def hold_until_dsl(self):
        """追忆: 按住普攻到断舍离(HUD 连续 2 帧消失), 再等 HUD 回来。"""
        gone = 0
        gone_at = None
        start = time.time()
        self.task.mouse_down()
        try:
            while time.time() - start < self.ZHUIYI_HOLD_CAP:
                self.task.next_frame()
                if time.time() - start < 1.0:
                    continue
                if not self.task.in_team()[0]:
                    gone += 1
                    if gone_at is None:
                        gone_at = time.time()
                    if gone >= 2:
                        break
                else:
                    gone = 0
                    gone_at = None
        finally:
            self.task.mouse_up()
        if gone_at is None:
            self.logger.info(f'Lucilla 断舍离 not seen in {self.ZHUIYI_HOLD_CAP}s, released')
            gone_at = time.time()
        # 断舍离期间不能切人, 等 HUD 回来
        self.task.wait_until(lambda: time.time() - gone_at > 0.8 and self.task.in_team()[0],
                             time_out=self.DSL_BACK_CAP)
        self.add_freeze_duration(gone_at, time.time() - gone_at)

    def hold_resonance(self, duration=None):
        """兼容旧接口: 长按 E 对焦(时长固定用追光窗口)。"""
        self.focus_shot()

    def get_switch_priority(self, current_char=None, has_intro=False, target_low_con=False):
        if has_intro and current_char and current_char.char_name in {'char_verina', 'char_shorekeeper'}:
            return SwitchPriority.MUST
        return super().get_switch_priority(current_char, has_intro, target_low_con)
