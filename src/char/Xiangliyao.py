import time

from src.char.BaseChar import BaseChar


class Xiangliyao(BaseChar):
    """相里要(导电主C)。

    机制: 平时 E1 + 普攻攒能量, 满了 E 变 E2(不用)。大招后能量条变 5 格「超算效能」, 身边 3 个立方体:
    能量没满 E 是 E3(飞起来, 之后点普攻接空中攻击), 能量满了 E 变 E4「万方法则」, 消耗一个立方体,
    E4 在 E 冷却时也能放。E4 后别闪避, 等头上魔方炸完。打满 3 次 E4 就可以切人(魔方自己会炸)。
    大招循环: R → E3 → 空中攻击 → E4 → 3 段普攻 → E4 → E3 → 空中攻击 → E4。
    """
    LIB_DURATION = 24.0
    LIB_FIELD_TIME_OUT = 15.0
    FIELD_TIME_OUT = 4.0

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.liberation_time = -1
        self.e4_count = 0

    def reset_state(self):
        super().reset_state()
        self.liberation_time = -1
        self.e4_count = 0

    def do_perform(self):
        self.wait_down()
        if self.click_liberation():
            self.liberation_time = time.time()
            self.e4_count = 0
        if self.still_in_liberation() and self.e4_count < 3:
            self.liberation_combo()
        else:
            self.click_echo(time_out=0)
            if self.resonance_available():
                self.click_resonance(send_click=False, time_out=1)
            if not self.is_con_full():
                self.attack_while(max(0.3, self.FIELD_TIME_OUT - self.field_time()), stop=self.is_con_full)
        self.switch_next_char()

    def still_in_liberation(self):
        return self.time_elapsed_accounting_for_freeze(self.liberation_time) < self.LIB_DURATION

    def liberation_combo(self):
        casts = 0
        while (self.still_in_liberation() and self.e4_count < 3 and casts < 8
               and self.field_time() < self.LIB_FIELD_TIME_OUT):
            if self.resonance_available():
                e4 = self.is_forte_full()
                if self.click_resonance(send_click=False, time_out=1)[0]:
                    casts += 1
                    if e4:
                        self.e4_count += 1
                        # 等魔方炸完
                        self.sleep(0.9)
                    else:
                        # E3 飞起来, 接空中攻击
                        self.attack_while(0.5)
                    continue
            self.attack_while(1.0)
        if self.e4_count >= 3 or casts >= 8:
            self.liberation_time = -1
