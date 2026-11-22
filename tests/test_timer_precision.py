"""计时精度: 打开之后框架 sleep 用的 Condition.wait 要按毫秒走。

在 coco-ww 根目录跑:  python\\python.exe -X utf8 tests\\test_timer_precision.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import timer_precision


class TimerPrecisionTest(unittest.TestCase):
    def test_short_waits_are_not_rounded_to_15ms(self):
        timer_precision.apply()
        timer_precision.apply()  # 重复调用无害
        self.assertLess(timer_precision.measure_wait_ms(0.002, 9), 6.0, 'wait(2ms) 不该再被拉到 15.6ms')
        self.assertLess(timer_precision.measure_wait_ms(0.02, 5), 26.0, 'wait(20ms) 不该再被拉到 31ms')


if __name__ == '__main__':
    unittest.main()
