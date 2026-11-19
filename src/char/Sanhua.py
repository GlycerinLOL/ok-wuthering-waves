import time

from src.char.BaseChar import BaseChar


class Sanhua(BaseChar):
    """散华(冷凝速切辅助)。

    机制: E 放冰棱、大招放冰川、变奏入场也落一块冰; 长按普攻蓄力, 光标走到蓝区松手打「爆裂」,
    把场上的冰一起引爆。E/变奏/第 5 段普攻让蓝区变大, 大招直接拉满(约半条)。
    预输入: 按 E / 开大 / 变奏入场的同时就按住普攻, 动作做完正好蓄好。大招能取消 E 的后空翻。
    协奏: 冰棱 15 + 冰川 20, 爆裂和每块冰爆炸各 15, E+大招+爆裂一轮约 80, 变奏入场再爆一次就满。
    循环: (按住普攻)E → 大招 → 松手爆裂 → 切人。
    """
    CHARGE_TIME = 0.85

    def do_perform(self):
        self.task.mouse_down()
        self.wait_down(click=False)
        start = time.time()
        charge = self.CHARGE_TIME
        e_used = False
        if self.resonance_available():
            self.task.mouse_up()
            e_used = self.click_resonance(send_click=False, time_out=1)[0]
            self.task.mouse_down()
            start = time.time()
        if self.click_liberation(send_click=False):
            # 大招取消 E 的后摇, 挥刀时继续蓄力
            start = time.time()
            charge += 0.1
        remain = charge - self.time_elapsed_accounting_for_freeze(start)
        if remain > 0:
            self.sleep(remain, False)
        self.task.mouse_up()
        self.sleep(0.15)
        if not e_used and self.resonance_available():
            # 爆裂冲刺途中补 E, 冰一起炸
            self.click_resonance(send_click=False, time_out=0.8)
        self.sleep(0.4)
        if self.is_con_full():
            self.click_echo(time_out=0)
        self.switch_next_char()
