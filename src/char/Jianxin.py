from src.char.BaseChar import BaseChar


class Jianxin(BaseChar):
    """鉴心(气动护盾辅助, 大招聚怪)。

    机制: 普攻/E/变奏涨「气」(大招不涨), 气满长按普攻进「运气」, 一直按着运到大周天·外(约 4 秒)
    给 4 层护盾 + 持续回血, 协奏也跟着满。E 第一段下蹲、第二段推手, 两段都涨气;
    E 第一段后马上开大会吞掉第二段的气, 所以先开大再 E。
    循环: 变奏入场 → 大招(聚怪) → E → 普攻 4 段 → 气满长按运气到协奏满 → 切人。
    """
    FIELD_TIME_OUT = 8.0
    CHANNEL_TIME = 4.5

    def do_perform(self):
        self.wait_down()
        self.click_liberation()
        if self.resonance_available():
            self.click_resonance(send_click=False, time_out=1)
            self.sleep(0.3)
        self.click_echo(time_out=0)
        if self.is_con_full():
            return self.switch_next_char()
        if not self.forte_full():
            self.attack_while(1.5, stop=self.forte_full)
        if self.forte_full():
            # 运气: 按住到协奏满, 最多运到大周天·外
            self.hold_mouse(self.CHANNEL_TIME, until=self.is_con_full, min_time=0.8)
            self.sleep(0.2)
        elif not self.is_con_full():
            self.attack_while(max(0.3, self.FIELD_TIME_OUT - self.field_time() - self.CHANNEL_TIME),
                              stop=self.is_con_full)
        self.switch_next_char()

    def forte_full(self):
        return self.is_forte_full() or self.is_mouse_forte_full()
