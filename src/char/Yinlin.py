from src.char.BaseChar import BaseChar


class Yinlin(BaseChar):
    """吟霖(导电副C, 后台协同)。

    机制: 普攻/大招/变奏命中给敌人上圆标记; E1 进强化(人偶变红), 强化中普攻/重击命中有标记的敌人
    打 4 次协同(每次 +5 协奏); E1 后能接 E2。审判值满 100 时重击变「千面魅影」, 把圆标记变成三角,
    之后 18 秒她在后台每秒最多打一次协同 —— 这才是她的核心, 千面魅影一定要放出来再走。
    重击回审判值比普攻快。循环: 变奏 → E1 → 大招 → 重击×2 → E2 → 千面魅影 → 补协奏 → 切人。
    """
    FIELD_TIME_OUT = 8.0

    def do_perform(self):
        if self.has_intro:
            self.continues_normal_attack(0.3)
        else:
            self.wait_down()
        self.click_echo(time_out=0)
        e1 = self.resonance_available() and self.click_resonance(send_click=False, time_out=1)[0]
        self.click_liberation()
        for _ in range(2):
            if self.is_mouse_forte_full():
                break
            self.heavy_attack(0.5)
            self.sleep(0.2)
        if e1:
            # E1 后的 E2(不放也会自己进冷却)
            self.click_resonance(send_click=False, time_out=0.8)
        self.judgement()
        if not self.is_con_full():
            self.attack_while(max(0.4, self.FIELD_TIME_OUT - self.field_time()), stop=self.is_con_full)
        self.switch_next_char()

    def judgement(self):
        """审判值满了放千面魅影; 差一点就再补两下重击。"""
        for _ in range(4):
            if self.is_mouse_forte_full():
                self.heavy_click_forte(self.is_mouse_forte_full)
                self.sleep(0.3)
                return True
            if self.field_time() > self.FIELD_TIME_OUT:
                return False
            self.heavy_attack(0.5)
            self.sleep(0.2)
        return False
