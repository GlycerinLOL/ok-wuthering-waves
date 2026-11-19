import time

from src.char.BaseChar import BaseChar


class Qiuyuan(BaseChar):
    """仇远: 挑灯问剑满 600 进 8 秒醉墨, 醉墨里才有强化重击。

    变奏入场直接 +400, 两段强化普攻凑满 -> 开大(视为声骸, 给登场队友暴伤)
    -> 按住打 3 段强化重击(每段 -200, 重击3 额外 +30 协奏) -> 长按 E(不辞远) -> 切人。
    """
    # 三段强化重击按派生帧累加约 2.77 秒(1 派生92F + 2 派生14F + 3 结束60F);
    # 先按满 HEAVY_MIN 再看回路提示灭没灭, 判据失灵最多按 HEAVY_MAX (嘉仇守实机定的)
    HEAVY_MIN = 2.5
    HEAVY_MAX = 5.0
    E_HOLD_MIN = 0.35
    E_HOLD_MAX = 1.6

    def do_perform(self):
        if self.has_intro:
            self.continues_normal_attack(1.17)
        if self.flying():
            self.wait_down()
        start = time.time()
        time_out = 4 if self.has_sub_dps_intro else 1.2
        while time.time() - start < time_out:
            self.click_echo(time_out=0)
            if time.time() - start < 0.5 and self.click_liberation():
                start = time.time()
            if self.is_mouse_forte_full():
                # 醉墨里先开大再按住打强化重击
                if self.liberation_available():
                    self.click_liberation()
                self.hold_heavy_chain()
                break
            if self.flying():
                self.shorekeeper_auto_dodge()
            self.click()
            self.check_combat()
            self.task.next_frame()
        self.hold_e2()
        self.switch_next_char()

    def hold_heavy_chain(self):
        """按住普攻打完 3 段强化重击(松早了只出第一段)。"""
        self.hold_mouse(self.HEAVY_MAX, until=lambda: not self.is_mouse_forte_full(), min_time=self.HEAVY_MIN)

    def hold_e2(self):
        """长按 E 出不辞远, 进冷却就松(再按着会进奔袭跑路)。"""
        if not self.resonance_available():
            return False
        self.hold_key(self.get_resonance_key(), self.E_HOLD_MAX, until=lambda: self.has_cd('resonance'),
                      min_time=self.E_HOLD_MIN)
        self.record_resonance_use()
        return True

    def shorekeeper_auto_dodge(self):
        from src.char.ShoreKeeper import ShoreKeeper
        for i, char in enumerate(self.task.chars):
            if isinstance(char, ShoreKeeper):
                return char.auto_dodge(condition=self.flying)
