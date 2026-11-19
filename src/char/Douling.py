import time

from src.Labels import Labels
from src.char.BaseChar import BaseChar, SwitchPriority
from src.task.BaseWWTask import isolate_gua_strokes

GEN = 'G'  # 艮
ZHEN = 'Z'  # 震


class Douling(BaseChar):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._segment = 1

    def do_perform(self):
        if self._segment == 1:
            self._do_segment1()
        else:
            self._do_segment2()

    def _do_segment1(self):
        self._normal_attack_cycle(1.2)
        if self.flying():
            self.wait_down()
        self.check_combat()
        clicked = self.click_resonance(send_click=True, time_out=0.5)[0]
        if not clicked:
            self._finish_and_reset()
            return
        self.sleep(0.2)
        if self.flying():
            self.wait_down()
        self.check_combat()
        self._normal_attack_cycle(1.0)
        self._segment = 2
        self.switch_next_char()

    def _do_segment2(self):
        self.check_combat()
        self.task.jump(after_sleep=0.01)
        self.sleep(0.05)
        self.check_combat()
        if self.flying():
            self.click()
            self.sleep(0.05)
        else:
            self.wait_down()
        self.check_combat()
        self._arrange_gua()
        self._heavy_attack_hold(2.5)
        self.click_echo(time_out=0)
        self.click_liberation()
        self._segment = 1
        self.switch_next_char()

    @staticmethod
    def _gua_good(gua):
        # 两下重击先吃右边两个再吃左边两个: 一组同色(少阴) + 一组异色(少阳) 大招才强化
        return len(gua) == 4 and (gua[0] == gua[1]) != (gua[2] == gua[3])

    def _plan_gua(self, gua):
        """最多补两个卦把卦象排成一同一异, 返回要补的序列; 已经对了或补不成返回 ''。"""
        if len(gua) < 2 or self._gua_good(gua):
            return ''
        for seq in (ZHEN, GEN, ZHEN + ZHEN, ZHEN + GEN, GEN + ZHEN, GEN + GEN):
            if self._gua_good((gua + seq)[-4:]):
                return seq
        return ''

    def _add_gua(self, kind):
        if kind == ZHEN:
            # 下落攻击落地命中给震(不用 E: E 之后点普攻会派生 A4 再多给一个震)
            self.task.jump(after_sleep=0.05)
            self.click()
            self.wait_down()
            self.sleep(0.15)
        else:
            # A1 -> A2 命中给艮
            self.continues_normal_attack(0.5)
            self.sleep(0.25)

    def _arrange_gua(self):
        """被打断后卦象常排成两组异色/两组同色, 重击前补卦挤成对的。读不到卦象就不管。"""
        for _ in range(2):
            gua = self.gua()
            seq = self._plan_gua(gua)
            if not seq:
                return
            self.logger.debug(f'douling arrange gua {gua} + {seq}')
            self.check_combat()
            self._add_gua(seq[0])

    def _finish_and_reset(self):
        self.click_echo(time_out=0)
        self.click_liberation()
        self._segment = 1
        self.switch_next_char()

    def _normal_attack_cycle(self, duration):
        start = time.time()
        while time.time() - start < duration:
            self.cycle_start()
            if self.flying():
                self.wait_down()
                break
            self.click()
            self.cycle_sleep()

    def gua(self):
        """Reads the hexagram row (卦象) above the skill bar, left to right, e.g. 'GZZZ' (G = 艮, Z = 震).

        Returns '' when no hexagram can be read.
        """
        box = self.task.box_of_screen_scaled(3840, 2160, 1664, 1776, 2138, 1869, name='douling_gua', hcenter=True)
        found = []
        for label, kind in ((Labels.douling_gen, GEN), (Labels.douling_zhen, ZHEN)):
            for b in self.task.find_feature(label, box=box, threshold=0.65, frame_processor=isolate_gua_strokes):
                found.append((b, kind))
        kept = []
        for b, kind in sorted(found, key=lambda f: f[0].confidence, reverse=True):
            # both glyphs can match the same slot weakly, keep the better one
            if all(abs(b.x - k.x) > b.width * 0.6 for k, _ in kept):
                kept.append((b, kind))
        return ''.join(kind for _, kind in sorted(kept, key=lambda f: f[0].x))

    def _heavy_attack_hold(self, duration):
        retries = 3
        for _ in range(retries):
            self.check_combat()
            # read again on every retry, an interrupted hold may already have consumed some hexagrams
            gua = self.gua()
            if len(gua) == 1:
                # one hexagram is not enough for an enhanced heavy, holding would cast 鬼门占卦 (self damage) instead
                return
            self.task.mouse_down()
            start = time.time()
            interrupted = False
            spent = 0
            while time.time() - start < duration:
                if self.flying():
                    interrupted = True
                    break
                if gua:
                    # each enhanced heavy consumes two hexagrams, release once fewer than two are left
                    self.task.next_frame()
                    spent = spent + 1 if len(self.gua()) < 2 else 0
                    if spent >= 2:
                        break
                else:
                    self.sleep(0.1)
            self.task.mouse_up()
            self.sleep(0.01)
            if not interrupted:
                return
            self.wait_down()

    def get_switch_priority(self, current_char=None, has_intro=False, target_low_con=False):
        if self.time_elapsed_accounting_for_freeze(self.last_perform) < 8:
            return SwitchPriority.NO
        return SwitchPriority.NORMAL
