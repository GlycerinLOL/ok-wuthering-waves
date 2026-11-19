from src.char.BaseChar import BaseChar


class Baizhi(BaseChar):
    """白芷(治疗)。

    机制: 普攻每命中一次涨 1 点「念意」(满 4); E 或重击消耗全部念意, 每点回一次血,
    E 每点额外 +8 协奏(4 点 +32), 重击只给一半协奏但每点 +2.5 大招能量。大招是持续治疗区,
    延奏给后面的人持续回血。循环: 普攻 4 下 → E → 大招 → 声骸 → 协奏没满再普攻 4 下 + 重击 → 切人。
    """
    FIELD_TIME_OUT = 5.0
    STACK_TIME = 1.4

    def do_perform(self):
        self.wait_down()
        if not self.is_con_full():
            self.attack_while(self.STACK_TIME)
        if self.resonance_available():
            self.click_resonance(send_click=False, time_out=1)
        self.click_liberation()
        self.click_echo(time_out=0)
        if not self.is_con_full() and self.field_time() < self.FIELD_TIME_OUT - self.STACK_TIME:
            self.attack_while(self.STACK_TIME, stop=self.is_con_full)
            if not self.is_con_full():
                self.heavy_attack(0.6)
        self.switch_next_char()
