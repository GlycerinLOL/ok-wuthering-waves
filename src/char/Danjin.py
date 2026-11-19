import time

from src.char.BaseChar import BaseChar


class Danjin(BaseChar):
    """丹瑾(湮灭副C)。

    机制: 前面打了几段普攻就能接几段 E, E 起手能顶掉一段普攻; 两段 E 给敌人挂「花」增伤;
    E 回能量(同时扣血), 能量满 120 时重击伤害最高还回血, 能量 >=60 重击后再点一下普攻有追击。
    循环: E 起手 → 普攻两段 → E 三段 → 能量满就重击+追击 → 大招 → 补协奏 → 切人。
    """
    FIELD_TIME_OUT = 6.0
    ATTACK_GAP = 0.3
    E_GAP = 0.35

    def do_perform(self):
        if self.has_intro:
            self.continues_normal_attack(0.5)
        else:
            self.wait_down()
        self.click_echo(time_out=0)
        self.heavy_if_full()
        if self.resonance_available():
            self.e_chain()
        self.heavy_if_full()
        if self.click_liberation():
            self.continues_normal_attack(0.3)
        if not self.is_con_full():
            self.attack_while(max(0.4, self.FIELD_TIME_OUT - self.field_time()), stop=self.is_con_full)
        self.switch_next_char()

    def e_chain(self):
        # E 起手 + 两段普攻, 后面就能接三段 E(第二段起挂花)
        self.send_resonance_key()
        self.record_resonance_use()
        self.sleep(self.E_GAP)
        for _ in range(2):
            self.click()
            self.sleep(self.ATTACK_GAP)
        for _ in range(3):
            self.send_resonance_key()
            self.sleep(self.E_GAP)

    def forte_full(self):
        return self.is_forte_full() or self.is_mouse_forte_full()

    def heavy_if_full(self):
        if not self.forte_full():
            return False
        self.heavy_attack(0.8)
        self.sleep(0.15)
        self.click()
        self.sleep(0.4)
        return True
