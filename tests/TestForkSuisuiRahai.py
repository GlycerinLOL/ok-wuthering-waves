import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from src.char.Suisui import Suisui
from src.task.RahaiBlocksTask import RahaiBlocksTask


class TestForkSuisui(unittest.TestCase):
    def setUp(self):
        self.suisui = Suisui(MagicMock(), 0)
        self.suisui.has_intro = False
        self.suisui.forte2_available = self.suisui.forte3_available = lambda: False
        self.suisui.cycle_start = self.suisui.cycle_sleep = self.suisui.click = MagicMock()
        self.suisui.switch_next_char = MagicMock()

    def test_e_switches_only_once(self):
        self.suisui.try_e = lambda: True
        self.suisui.time_elapsed_accounting_for_freeze = lambda *_, **__: 0

        self.suisui.do_perform()

        self.suisui.switch_next_char.assert_called_once_with()

    def test_forte_timeout_saves_screenshot_and_still_switches(self):
        self.suisui.time_elapsed_accounting_for_freeze = lambda *_, **__: Suisui.FORTE_TIMEOUT

        self.suisui.do_perform()

        self.suisui.task.screenshot.assert_called_once_with('suisui_forte3_timeout')
        self.suisui.switch_next_char.assert_called_once_with()


class TestForkRahaiScroll(unittest.TestCase):
    def moved(self, before, after):
        def page(cards):
            return [(SimpleNamespace(name=name, x=x), SimpleNamespace(name='已完成', x=x + 45)) for name, x in cards]

        task = SimpleNamespace(width=2560, height=1440, sleep=lambda *_: None, _stage_list=None,
                               wait_until=lambda *_, **__: page(after))
        return RahaiBlocksTask._scroll_stages(task, MagicMock(), 1, page(before))[1]

    def test_whole_card_scroll_counts_as_moved(self):
        # x positions read from a real 2560x1440 zh_TW list; the scroll lands exactly one card further.
        self.assertTrue(self.moved([('A', 220), ('B', 895), ('C', 1580)], [('B', 218), ('C', 900), ('D', 1580)]))

    def test_unchanged_list_is_the_end(self):
        self.assertFalse(self.moved([('A', 220), ('B', 895)], [('A', 218), ('B', 900)]))


if __name__ == '__main__':
    unittest.main()
