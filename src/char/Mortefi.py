from src.char.BaseChar import BaseChar


class Mortefi(BaseChar):
    """莫特斐(热熔辅助, 延奏加下一位重击伤害)。

    机制: 怒气值满时 E 变成跳起下砸的强化 E; 普攻第 4 段涨怒气和协奏最多, 切他上场自动打的正好是第 4 段,
    别用技能把它取消。E 后 5 秒内普攻额外涨怒气。大招开出协同「加强音」, 普攻一发、重击两发, 带到后面队友身上。
    循环: 变奏入场 → E → 强化 E → 大招 → 4 段普攻 → 强化 E → 切人。
    """
    FIELD_TIME_OUT = 5.0

    def do_perform(self):
        if self.has_intro:
            self.wait_down()
        else:
            # 等切人自动打的第 4 段打完
            self.attack_while(0.5)
        self.click_echo(time_out=0)
        self.e_twice()
        self.click_liberation(wait_if_cd_ready=0.5)
        if not self.is_con_full():
            self.attack_while(1.4, stop=self.is_con_full)
            if self.resonance_available():
                self.click_resonance(send_click=False, time_out=0.8)
        if not self.is_con_full():
            self.attack_while(max(0.3, self.FIELD_TIME_OUT - self.field_time()), stop=self.is_con_full)
        self.switch_next_char()

    def e_twice(self):
        if not self.resonance_available():
            return
        self.click_resonance(send_click=False, time_out=1)
        self.sleep(0.1)
        # 怒气满了 E 图标马上又亮(强化 E)
        if self.resonance_available():
            self.click_resonance(send_click=False, time_out=0.8)
