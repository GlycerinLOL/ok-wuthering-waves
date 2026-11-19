from src.char.BaseChar import BaseChar


class Youhu(BaseChar):
    """釉瑚(治疗)。

    机制: 每次「问祯」(变奏入场 / E / 大招 / 霜色满时长按松手的前滚 / 极限闪避反击)摇出一个图案,
    之后点普攻「奇珍赏」把图案扔出去变成一个吉兆, 攒满 4 个吉兆再长按普攻「诗中物」结算回血。
    霜色: 一套 4 段普攻或长按普攻都能攒满。
    循环: 变奏 → 普攻 → E → 普攻 → 大招 → 普攻 → 长按攒满松手前滚 → 普攻 → 长按结算 → 切人。
    """
    FIELD_TIME_OUT = 9.0

    def do_perform(self):
        self.wait_down()
        self.throw()
        self.click_echo(time_out=0)
        if self.resonance_available() and self.click_resonance(send_click=False, time_out=1)[0]:
            self.throw()
        if self.click_liberation():
            self.throw()
        if not self.is_con_full() and self.field_time() < self.FIELD_TIME_OUT - 3:
            # 长按攒满霜色, 松手前滚问祯, 再扔
            self.hold_mouse(1.5, until=self.is_mouse_forte_full, min_time=0.5)
            self.sleep(0.5)
            self.throw()
        if not self.is_con_full() and self.field_time() < self.FIELD_TIME_OUT - 1.5:
            # 4 个吉兆时长按是诗中物结算; 不够 4 个就只是又摇一次
            self.heavy_attack(0.8)
            self.sleep(0.3)
        self.switch_next_char()

    def throw(self):
        self.click()
        self.sleep(0.45)
