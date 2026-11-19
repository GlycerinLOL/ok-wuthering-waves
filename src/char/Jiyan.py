import time

from src.char.BaseChar import BaseChar


class Jiyan(BaseChar):
    """忌炎(气动主C)。

    机制: 破阵值满 60, 一套 5 段普攻约 +30、变奏入场 +30; 破阵值 >=30 时 E 消耗 30 点增伤 20%,
    开大时有破阵值会多一段下砸。大招 10 秒「破阵」: 普攻变成龙转(有聚怪), E 照常能放且不吃倒计时,
    第一段转完闪避能重开, 10 秒能多转一次。
    循环: 变奏入场(破阵值正好 30)直接开大 → 大招里 E 好了就放、其余时间普攻, 隔一会闪避一次;
    没大招: 普攻一套攒破阵值 → E → 补协奏 → 切人。
    """
    LIB_DURATION = 10.0
    FIELD_TIME_OUT = 5.0

    def do_perform(self):
        if self.has_intro:
            self.continues_normal_attack(0.3)
        else:
            self.wait_down()
        self.click_echo(time_out=0)
        if self.click_liberation():
            self.liberation_combo(time.time())
            return self.switch_next_char()
        # 一套 5 段普攻把破阵值攒到 30 以上再放 E
        self.attack_while(2.0, stop=self.is_con_full)
        if self.resonance_available():
            self.click_resonance(send_click=False, time_out=1, post_sleep=0.6)
        if not self.is_con_full():
            self.attack_while(max(0.3, self.FIELD_TIME_OUT - self.field_time()), stop=self.is_con_full)
        self.switch_next_char()

    def liberation_combo(self, start):
        while self.time_elapsed_accounting_for_freeze(start) < self.LIB_DURATION:
            if self.resonance_available():
                if self.click_resonance(send_click=True, time_out=0.6)[0]:
                    continue
            self.attack_while(1.0)
            # 龙转一段后闪避取消后摇
            self.task.click(key='right')
            self.sleep(0.05)
