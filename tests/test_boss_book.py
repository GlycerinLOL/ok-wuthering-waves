"""刷4C 传送：F2 素材获取列表按名字找 Boss(不起游戏、不起界面)。

列表数据是 2026-11-19 2K 实机一页页滚下来的 OCR 原文和按钮位置(tests/data/boss_book_pages_2560x1440.json)，
带着真实的 OCR 错字(魇→魔、囮→四/@、维→維 ...)。

在 coco-ww 根目录跑:  python\\python.exe -X utf8 tests\\test_boss_book.py
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ok import Box
from src.task import BossBook
from src.task.FarmEchoTask import FarmEchoTask

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'boss_book_pages_2560x1440.json')
with open(DATA, encoding='utf-8') as f:
    PAGES = json.load(f)


class FakeBookTask(FarmEchoTask):
    """不走 __init__；ocr/find_feature 按录下来的页返回，滚轮翻到下一页。"""
    width = 2560
    height = 1440
    game_lang = 'zh_CN'

    @classmethod
    def make(cls, category):
        task = object.__new__(cls)
        task.pages = PAGES[category]
        task.page = 0
        task.clicked = None
        task.logs = []
        return task

    def _current(self):
        return self.pages[min(self.page, len(self.pages) - 1)]

    def ocr(self, x, y, to_x, to_y, **kwargs):
        return [Box(int(tx * self.width), int(ty * self.height), 200, 40, name=text)
                for text, tx, ty in self._current()['texts'] if x <= tx <= to_x and y <= ty <= to_y]

    def find_feature(self, *args, **kwargs):
        return [Box(int(bx * self.width), int(by * self.height), 60, 60, name='boss_proceed')
                for bx, by in self._current()['btns']]

    def box_of_screen(self, *args, **kwargs):
        return None

    def scroll_relative(self, x, y, count):
        # 负数往下翻一页（录的就是一次滚 10 格一页）；正数往上，滚到顶不再动
        if count < 0:
            self.page = min(self.page + 1, len(self.pages) - 1)
        else:
            self.page = max(0, self.page - max(1, count // 10))

    def click(self, box, *args, **kwargs):
        self.clicked = box

    def next_frame(self):
        return None

    def sleep(self, *args, **kwargs):
        pass

    def draw_boxes(self, *args, **kwargs):
        pass

    def tr(self, message):
        return message

    def log_info(self, message, *args, **kwargs):
        self.logs.append(message)


class BookLearnSandbox(unittest.TestCase):
    """学到的新名字写进临时文件，不碰 configs/。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_file = BossBook.LEARNED_FILE
        BossBook.LEARNED_FILE = os.path.join(self.tmp.name, 'boss_book_names.json')

    def tearDown(self):
        BossBook.LEARNED_FILE = self.old_file
        self.tmp.cleanup()


class PickByNameTest(BookLearnSandbox):
    def assert_clicked_row(self, task, name, expected):
        self.assertIsNotNone(task.clicked, expected)
        self.assertTrue(BossBook.same_name(name, expected), f'{name} != {expected}')
        # 点的按钮和名字在同一行
        page = task._current()
        name_y = next(ty for text, _, ty in page['texts'] if text == name)
        self.assertTrue(0 <= task.clicked.y / task.height - name_y <= 0.06)

    def test_every_boss_challenge_by_name(self):
        for target in BossBook.SEED_NAMES['qiangdi']:
            task = FakeBookTask.make('qiangdi')
            name = task.click_book_target_by_name('qiangdi', target)
            self.assert_clicked_row(task, name, target)

    def test_every_weekly_by_name(self):
        for target in BossBook.SEED_NAMES['zhange']:
            task = FakeBookTask.make('zhange')
            name = task.click_book_target_by_name('zhange', target)
            self.assert_clicked_row(task, name, target)

    def test_every_special_nightmare_by_name(self):
        for target in BossBook.SEED_NAMES['mengyan']:
            task = FakeBookTask.make('mengyan')
            name = task.click_book_target_by_name('mengyan', target)
            self.assert_clicked_row(task, name, target)

    def test_index_counts_rows_not_headers(self):
        # 旧代码选第 10/14/20 个分别点到叹息古龙/无冠者/聚械机偶(实机 2026-11-19)
        for serial, expected in [(1, '天傀劫煞'), (10, '荣耀狮像'), (14, '无归的谬误'), (20, '哀声鸷'), (23, '聚械机偶')]:
            task = FakeBookTask.make('qiangdi')
            name = task.click_book_target_by_name('qiangdi', '', serial)
            self.assert_clicked_row(task, name, expected)

    def test_nightmare_index_skips_nests(self):
        for serial, expected in enumerate(BossBook.SEED_NAMES['mengyan'], 1):
            task = FakeBookTask.make('mengyan')
            name = task.click_book_target_by_name('mengyan', '', serial)
            self.assert_clicked_row(task, name, expected)

    def test_nightmare_index_skips_nests_by_progress_line(self):
        # 外文客户端认不出「聚落」两个字，靠名字下面那行 0/36 进度认
        pages = json.loads(json.dumps(PAGES['mengyan']))
        for page in pages:
            page['texts'] = [[t[0].replace('聚落', '巢穴'), t[1], t[2]] for t in page['texts']]
        task = FakeBookTask.make('mengyan')
        task.pages = pages
        name = task.click_book_target_by_name('mengyan', '', 1)
        self.assert_clicked_row(task, name, '梦魇·赫卡忒')

    def test_weekly_index_beyond_old_hardcoded_total(self):
        task = FakeBookTask.make('zhange')
        name = task.click_book_target_by_name('zhange', '', 11)
        self.assert_clicked_row(task, name, '昔日咏叹之钟')

    def test_unknown_name_raises_without_clicking(self):
        task = FakeBookTask.make('qiangdi')
        with self.assertRaises(Exception) as ctx:
            task.click_book_target_by_name('qiangdi', '不存在的Boss')
        self.assertIsNone(task.clicked)
        self.assertIn('聚械机偶', str(ctx.exception))

    def test_index_out_of_range_raises(self):
        task = FakeBookTask.make('qiangdi')
        with self.assertRaises(Exception):
            task.click_book_target_by_name('qiangdi', '', 24)
        self.assertIsNone(task.clicked)


class NewBossTest(BookLearnSandbox):
    def test_new_boss_on_top_is_learned_and_pickable(self):
        pages = json.loads(json.dumps(PAGES['qiangdi']))
        # 模拟新版本：第一页最上面那行换成没见过的 Boss
        pages[0]['texts'] = [['新王之影' if t[0] == '天傀劫煞' else t[0], t[1], t[2]] for t in pages[0]['texts']]
        task = FakeBookTask.make('qiangdi')
        task.pages = pages
        name = task.click_book_target_by_name('qiangdi', '新王之影')
        self.assertEqual(name, '新王之影')
        self.assertAlmostEqual(task.clicked.y / task.height, pages[0]['btns'][0][1], places=3)
        names = BossBook.known_names('qiangdi')
        # 排在游戏里紧跟它的那个已知 Boss 前面
        self.assertEqual(names.index('新王之影') + 1, names.index('万囮牢·朽躯'))
        self.assertIn('天傀劫煞', names)  # 种子里的不丢
        self.assertTrue(any('新王之影' in m for m in task.logs))


class FuzzyNameTest(unittest.TestCase):
    def test_real_ocr_misreads_match(self):
        for ocr, target in [('万四牢・朽躯', '万囮牢·朽躯'), ('万@牢・朽躯', '万囮牢·朽躯'), ('海維夏', '海维夏'),
                            ('海维复', '海维夏'), ('海っ女', '海之女'), ('梦魔・凯尔匹', '梦魇·凯尔匹'),
                            ('彼世猩紅之幕・战歌重奏', '彼世猩红之幕'), ('異构武装', '异构武装'), ('昇构武装', '异构武装')]:
            self.assertIsNotNone(BossBook.match_distance(ocr, target), (ocr, target))

    def test_similar_bosses_do_not_match(self):
        for ocr, target in [('梦魔·朔雷之鳞', '梦魇·云闪之鳞'), ('朔雷之鳞', '云闪之鳞'), ('海维夏', '海之女'),
                            ('梦魇·辉萤军势', '辉萤军势'), ('时序命定之争・战歌重奏', '无序边境之火'),
                            ('梦魔亚当·重锤', '梦魇·哀声鸷')]:
            self.assertIsNone(BossBook.match_distance(ocr, target), (ocr, target))

    def test_one_char_apart_prefers_the_closer_known_boss(self):
        # 只差一个字的两个 Boss(今州/梦州 这种)：读到「今州」时目标是「梦州」不能点
        others = ['瑝珑·今州']
        self.assertTrue(BossBook.nearer_to_other('瑝珑·今州', BossBook.match_distance('瑝珑·今州', '瑝珑·梦州'), others))
        self.assertFalse(BossBook.nearer_to_other('瑝珑·梦卅', BossBook.match_distance('瑝珑·梦卅', '瑝珑·梦州'), others))

    def test_seed_names_are_mutually_distinct(self):
        for category, names in BossBook.SEED_NAMES.items():
            for i, a in enumerate(names):
                for b in names[i + 1:]:
                    self.assertFalse(BossBook.same_name(a, b), (category, a, b))

    def test_merge_order_inserts_before_next_known(self):
        merged, new = BossBook.merge_order(['A1A1', 'B2B2', 'C3C3'], ['B2B2', 'X9X9', 'C3C3'])
        self.assertEqual(merged, ['A1A1', 'B2B2', 'X9X9', 'C3C3'])
        self.assertEqual(new, ['X9X9'])


class MigrateIndexTest(BookLearnSandbox):
    def test_old_index_becomes_name(self):
        task = object.__new__(FarmEchoTask)
        task.config = {'Weekly Boss Name': '', 'Boss Challenge Name': '', 'Special Nightmare Name': '梦魇·凯尔匹',
                       'Which Weekly Boss to Teleport': 6, 'Which Boss Challenge to Teleport': 99,
                       'Which Special Nightmare to Teleport': 1}
        task.migrate_teleport_index_to_name()
        self.assertEqual(task.config['Weekly Boss Name'], '芙露德莉斯（命途断章之轮）')
        self.assertEqual(task.config['Boss Challenge Name'], '')  # 超出已知列表：运行时按序号数，点到后再记名字
        self.assertEqual(task.config['Special Nightmare Name'], '梦魇·凯尔匹')  # 已经选了名字的不动

    def test_bare_weekly_realm_becomes_option(self):
        # 11-19 版存的是纯副本名，不换成下拉框里的写法界面上显示不出来
        task = object.__new__(FarmEchoTask)
        task.config = {'Weekly Boss Name': '虚妄诞生之种', 'Boss Challenge Name': '天傀劫煞',
                       'Special Nightmare Name': '梦魇·赫卡忒'}
        task.migrate_teleport_index_to_name()
        self.assertEqual(task.config, {'Weekly Boss Name': '达妮娅（虚妄诞生之种）', 'Boss Challenge Name': '天傀劫煞',
                                       'Special Nightmare Name': '梦魇·赫卡忒'})


class WeeklyLabelTest(BookLearnSandbox):
    def test_every_weekly_has_boss_name(self):
        options = BossBook.known_options('zhange')
        self.assertEqual(len(options), len(BossBook.SEED_NAMES['zhange']))
        self.assertIn('鸣钟之龟（昔日咏叹之钟）', options)
        self.assertIn('天演溯心（定序诸理之律）', options)
        for option, realm in zip(options, BossBook.SEED_NAMES['zhange']):
            self.assertNotEqual(option, realm, realm)
            self.assertEqual(BossBook.realm_of(option), realm)

    def test_other_categories_unchanged(self):
        self.assertEqual(BossBook.known_options('qiangdi'), BossBook.known_names('qiangdi'))
        self.assertEqual(BossBook.known_options('mengyan'), BossBook.known_names('mengyan'))

    def test_realm_of(self):
        for value, realm in [('角（时序命定之争）', '时序命定之争'), ('角(时序命定之争)', '时序命定之争'),
                             ('时序命定之争', '时序命定之争'), ('梦魇·赫卡忒', '梦魇·赫卡忒'), ('', '')]:
            self.assertEqual(BossBook.realm_of(value), realm)

    def test_every_weekly_option_clicks_its_row(self):
        for option in BossBook.known_options('zhange'):
            task = FakeBookTask.make('zhange')
            name = task.click_book_target_by_name('zhange', BossBook.realm_of(option))
            PickByNameTest.assert_clicked_row(self, task, name, BossBook.realm_of(option))
            self.assertTrue(any(option in m for m in task.logs), task.logs)  # 日志里也写 Boss 名

    def test_new_weekly_without_alias_shows_realm(self):
        self.assertEqual(BossBook.option_label('zhange', '某某新章之门'), '某某新章之门')
        self.assertEqual(BossBook.to_option('zhange', '某某新章之门'), '某某新章之门')

    def test_ocr_variant_still_gets_label(self):
        self.assertEqual(BossBook.option_label('zhange', '彼世猩紅之幕'), '赫卡忒（彼世猩红之幕）')


if __name__ == '__main__':
    unittest.main()
