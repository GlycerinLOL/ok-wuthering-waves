from src.char.BaseChar import BaseChar


class Taoqi(BaseChar):
    """桃祈(防御盾辅, 延奏加下一位共鸣技能伤害)。

    机制: E 生成 3 个盾牌, 盾牌被打/到期/第 4 段普攻命中都会转成能量(3 格);
    有能量时普攻变成 3 段强化普攻, 每段消耗 1 格并给全队加护盾。变奏入场直接给 3 格能量。
    长按普攻是防御姿态, 3 秒后自动弹反, 之后点普攻接第 4 段(费时间, 自动战斗不用)。
    循环: 变奏入场 → 普攻(强化三段上盾) → E → 大招 → 声骸 → 补协奏 → 切人。
    """
    FIELD_TIME_OUT = 4.0

    def do_perform(self):
        if self.has_intro:
            self.wait_down()
            # 变奏给满能量, 直接打三段强化普攻上盾
            self.attack_while(1.5)
        else:
            self.wait_down()
        if self.resonance_available():
            self.click_resonance(send_click=False, time_out=1)
        self.click_liberation()
        self.click_echo(time_out=0, sleep_time=0.1)
        if not self.is_con_full():
            self.attack_while(max(0.3, self.FIELD_TIME_OUT - self.field_time()), stop=self.is_con_full)
        self.switch_next_char()
