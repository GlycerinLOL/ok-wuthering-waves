"""刷4C 传送：素材获取列表的布局变了也照样按名字点对那一行(不起游戏、不起界面)。

2026-11 有人反馈：「战歌重奏」页最上面多了「剩余战歌重奏双倍奖励次数：6/6」提示条和「限时提前开放」分组
(主线没推到的号可以提前打星海迷途之扉/烬夜天启之章)，原来的地区分组和 Boss 行整体被往下挤；
分组里的行按钮是「直接挑战」(前面多一个地图钉，按钮比「前往」行靠下一点)，地区里的是「前往」。

这里用一个能滚动的列表模型生成每一屏：分组标题、提示条、Boss 行、按钮模板和按钮上的字都按 2K 实测的间距排，
想插什么分组、提示条、让哪一行没解锁/模板认不出都行。tests/test_boss_book.py 用的是实机录的原文。

在 coco-ww 根目录跑:  python\\python.exe -X utf8 tests\\test_boss_book_layout.py
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ok import Box
from src.task import BossBook
from src.task.FarmEchoTask import FarmEchoTask

W, H = 2560, 1440
# 2K 实测间距（屏高占比）：分组标题到下一行名字 0.0687，一行 Boss 0.1396；
# 名字框顶 → 按钮模板框顶：「前往」行 0.0229，「直接挑战」行更靠下；滚轮 10 格 ≈0.244
HEADER_H, ROW_H, SCROLL_PER_TICK = 0.0687, 0.1396, 0.0244
BTN_DY = {'go': 0.0229, 'challenge': 0.035}
TEMPLATE_W, TEMPLATE_H = 44, 80  # boss_proceed 33x60@1080 放大到 2K
LABEL = {'go': '前往', 'challenge': '直接挑战'}
# 每行中间那排奖励图标（2026-11-22 实机截图 x≈0.63~0.83）自己能横着滚：滚轮落在上面只滚图标，列表不动
REWARD_STRIP = (0.62, 0.84)

WEEKLY_REGIONS = [('瑝珑·梦州', ['定序诸理之律', '失坠困咎之庭']),
                  ('索拉里斯之极·拉海洛', ['虚妄诞生之种', '星海迷途之扉']),
                  ('黎那汐塔', ['烬夜天启之章', '命途断章之轮', '彼世猩红之幕']),
                  ('瑝珑·今州', ['时序命定之争', '无冠巨像之心', '无序边境之火', '昔日咏叹之钟'])]
EARLY = ['星海迷途之扉', '烬夜天启之章']


def header(text):
    return {'kind': 'header', 'text': text}


def row(name, button='go', template=True, label=True, suffix='・战歌重奏'):
    """button: 'go' 前往 / 'challenge' 直接挑战 / None 没解锁(没按钮)。"""
    return {'kind': 'row', 'text': name + suffix, 'button': button, 'template': template, 'label': label}


def weekly_list(early=(), locked=()):
    """战歌重奏列表。early 里的 Boss 放进最上面的「限时提前开放」分组(直接挑战)，地区里那一行按 locked 决定有没有按钮。"""
    items = []
    if early:
        items.append(header('限时提前开放'))
        items += [row(n, 'challenge') for n in early]
    for region, names in WEEKLY_REGIONS:
        items.append(header(region))
        for n in names:
            if n in early and n in locked:
                items.append(row(n, None))
            elif n in early:
                continue  # 没解锁的只出现在「限时提前开放」里
            else:
                items.append(row(n, None if n in locked else 'go'))
    return items


class BookList:
    """可滚动的列表。banners 是列表上方不跟着滚的提示条 [(文字, y)]，列表可见区从 top 到 0.90。"""

    def __init__(self, items, banners=(), top=0.25, bottom=0.90):
        # banners 里的文字可以是函数(每次 OCR 读到的都不一样，比如倒计时、次数在变)
        self.items, self.banners, self.top, self.bottom = items, list(banners), top, bottom
        self.reads = 0
        height = sum(HEADER_H if it['kind'] == 'header' else ROW_H for it in items)
        self.max_offset = max(0.0, height - (bottom - top) + 0.03)

    def frame(self, offset):
        self.reads += 1
        texts = [(text(self.reads) if callable(text) else text, 0.45, y) for text, y in self.banners]
        templates, labels = [], []
        y = self.top - offset
        for it in self.items:
            if it['kind'] == 'header':
                if self.top <= y <= self.bottom:
                    texts.append((it['text'], 0.468, y))
                y += HEADER_H
                continue
            if self.top <= y <= self.bottom:
                texts.append((it['text'], 0.463, y))
                texts.append(('450', 0.657, y + 0.07))  # 奖励图标上的数字
            if it['button']:
                ty = y + BTN_DY[it['button']]
                cy = ty + TEMPLATE_H / H / 2
                # 按钮整个露在列表可见区里才看得到（被上下沿裁掉一半的认不出）
                if it['template'] and self.top <= ty and ty + TEMPLATE_H / H <= self.bottom:
                    templates.append((0.941, ty))
                if it['label'] and self.top <= cy - 0.014 and cy + 0.014 <= self.bottom:
                    labels.append((LABEL[it['button']], 0.90, cy - 0.014))
            y += ROW_H
        return texts, templates, labels

    def offset_of(self, name):
        """让某一行排到可见区第 3 行左右的滚动量（模拟上次停在中间）。"""
        y = 0.0
        for it in self.items:
            if it['kind'] == 'row' and BossBook.same_name(it['text'], name):
                return min(self.max_offset, max(0.0, y - 0.3))
            y += HEADER_H if it['kind'] == 'header' else ROW_H
        raise KeyError(name)


class FakeListTask(FarmEchoTask):
    """不走 __init__；ocr/find_feature 按 BookList 当前滚动位置出一屏，滚轮改滚动位置。"""
    width = W
    height = H
    game_lang = 'zh_CN'

    @classmethod
    def make(cls, book, offset=0.0):
        task = object.__new__(cls)
        task.book = book
        task.offset = offset
        task.clicked = None
        task.logs = []
        task.scrolls = []
        task.scroll_points = []
        task.missed_scrolls = 0
        task.eaten_scrolls = 0
        task.eat_x = [REWARD_STRIP]
        task.ocr_calls = 0
        return task

    def ocr(self, x, y, to_x, to_y, **kwargs):
        self.ocr_calls += 1
        texts, _, labels = self.book.frame(self.offset)
        boxes = [Box(int(tx * W), int(ty * H), 200, 40, name=t) for t, tx, ty in texts]
        boxes += [Box(int(tx * W), int(ty * H), 110, 40, name=t) for t, tx, ty in labels]
        return [b for b in boxes if x <= b.x / W <= to_x and y <= b.y / H <= to_y]

    def find_feature(self, *args, box=None, **kwargs):
        _, templates, _ = self.book.frame(self.offset)
        x0, y0, x1, y1 = box or (0, 0, 1, 1)
        return [Box(int(bx * W), int(by * H), TEMPLATE_W, TEMPLATE_H, name='boss_proceed') for bx, by in templates
                if x0 <= bx and bx + TEMPLATE_W / W <= x1 and y0 <= by and by + TEMPLATE_H / H <= y1]

    def box_of_screen(self, x, y, to_x, to_y, *args, **kwargs):
        return x, y, to_x, to_y

    def scroll_relative(self, x, y, count):
        self.scrolls.append(count)
        self.scroll_points.append((x, y, count))
        if not self.book.top <= y <= self.book.bottom:
            self.missed_scrolls += 1
            return  # 滚轮落在列表外面（上方提示条上），列表不动
        if any(x0 <= x <= x1 for x0, x1 in self.eat_x):
            self.eaten_scrolls += 1
            return  # 滚轮落在奖励图标那排上，滚的是图标，列表不动
        self.offset = min(self.book.max_offset, max(0.0, self.offset - count * SCROLL_PER_TICK))

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

    # 点到的是哪一行：按点击时的画面，按钮中心落在哪个名字下面
    def clicked_row_name(self):
        texts, _, _ = self.book.frame(self.offset)
        cy = (self.clicked.y + self.clicked.height / 2) / H
        names = [(t, ty) for t, tx, ty in texts if tx < 0.6 and ty <= cy and BossBook.is_candidate_text(t)]
        return max(names, key=lambda n: n[1])[0]


class BookLearnSandbox(unittest.TestCase):
    """学到的新名字写进临时文件，不碰 configs/。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_file = BossBook.LEARNED_FILE
        BossBook.LEARNED_FILE = os.path.join(self.tmp.name, 'boss_book_names.json')

    def tearDown(self):
        BossBook.LEARNED_FILE = self.old_file
        self.tmp.cleanup()

    def pick(self, book, target='', serial=0, offset=0.0, category='zhange'):
        task = FakeListTask.make(book, offset)
        name = task.click_book_target_by_name(category, target, serial)
        return task, name

    def assert_row(self, task, name, expected, kind=None):
        self.assertIsNotNone(task.clicked)
        self.assertTrue(BossBook.same_name(name, expected), f'{name} != {expected}')
        # 点的按钮就在这一行（按钮中心上方最近的名字就是它）
        self.assertTrue(BossBook.same_name(task.clicked_row_name(), expected), (task.clicked_row_name(), expected))
        if kind is not None:
            self.assertEqual(task.book_button_kind, kind)


# 截图那一屏：列表上方三条固定提示，可见区整体下移一行
SCREENSHOT_BANNERS = [('剩余战歌重奏双倍奖励次数：6/6', 0.19), ('本周剩余可收取次数：3/3', 0.245),
                      ('筛选查看各等级奖励', 0.30)]


class EarlyAccessGroupTest(BookLearnSandbox):
    def screenshot_book(self, locked=()):
        return BookList(weekly_list(early=EARLY, locked=locked), banners=SCREENSHOT_BANNERS, top=0.31)

    def test_every_weekly_clicks_its_own_row_after_group_inserted(self):
        plain = BookList(weekly_list())
        shifted = self.screenshot_book()
        for target in BossBook.SEED_NAMES['zhange']:
            task_plain, name_plain = self.pick(plain, target)
            task, name = self.pick(shifted, target)
            self.assert_row(task, name, target, 'challenge' if target in EARLY else 'go')
            self.assert_row(task_plain, name_plain, target, 'go')

    def test_rows_really_moved(self):
        # 同一个 Boss，两种布局下点到的按钮高度不一样：说明不是靠写死的位置点对的
        _, plain = self.pick(BookList(weekly_list()), '定序诸理之律')
        task_plain, _ = self.pick(BookList(weekly_list()), '定序诸理之律')
        task_shifted, _ = self.pick(self.screenshot_book(), '定序诸理之律')
        self.assertGreater(abs(task_plain.clicked.y - task_shifted.clicked.y), 0.05 * H)

    def test_headers_and_banners_are_not_rows(self):
        task = FakeListTask.make(self.screenshot_book())
        rows = task._read_book_rows().rows
        names = [r.name for r in rows if r.btn is not None]
        self.assertEqual([BossBook.display_name(n) for n in names][:2], EARLY)
        for r in rows:
            self.assertFalse(BossBook.is_banner_text(r.name), r.name)
            if r.name in ('瑝珑·梦州', '限时提前开放'):
                self.assertIsNone(r.btn)

    def test_learned_names_skip_group_title(self):
        self.pick(self.screenshot_book(), '昔日咏叹之钟')
        names = BossBook.known_names('zhange')
        self.assertNotIn('限时提前开放', names)
        self.assertFalse(any(BossBook.is_banner_text(n) for n in names), names)
        self.assertEqual(len(names), len(BossBook.SEED_NAMES['zhange']))

    def test_challenge_button_without_template_found_by_label(self):
        # 模板没认出「直接挑战」(悬停高亮之类)，靠按钮上的字也能点到同一行
        items = weekly_list(early=EARLY)
        for it in items:
            if it['kind'] == 'row' and it['button'] == 'challenge':
                it['template'] = False
        task, name = self.pick(BookList(items, banners=SCREENSHOT_BANNERS, top=0.31), '烬夜天启之章')
        self.assert_row(task, name, '烬夜天启之章', 'challenge')
        self.assertEqual(task.clicked.name, '直接挑战')

    def test_early_row_preferred_when_region_row_locked(self):
        # 地区里那一行还没解锁(没按钮)，只能点「限时提前开放」里那一行
        book = self.screenshot_book(locked=EARLY)
        for target in EARLY:
            task, name = self.pick(book, target)
            self.assert_row(task, name, target, 'challenge')

    def test_locked_target_raises_clearly(self):
        book = BookList(weekly_list(locked=['无冠巨像之心']))
        task = FakeListTask.make(book)
        with self.assertRaises(Exception) as ctx:
            task.click_book_target_by_name('zhange', '无冠巨像之心')
        self.assertIsNone(task.clicked)
        self.assertIn('no Go/Challenge button', str(ctx.exception))
        self.assertIn('无妄者（无冠巨像之心）', str(ctx.exception))

    def test_unknown_target_raises_and_stops_at_bottom(self):
        task = FakeListTask.make(self.screenshot_book())
        with self.assertRaises(Exception) as ctx:
            task.click_book_target_by_name('zhange', '不存在之门')
        self.assertIsNone(task.clicked)
        self.assertIn('not found', str(ctx.exception))
        self.assertIn('昔日咏叹之钟', str(ctx.exception))
        self.assertLessEqual(len([s for s in task.scrolls if s < 0]), 10)

    def test_banner_with_its_own_button_is_not_counted(self):
        # 以后活动在列表里插一条带「前往」的提示：按序号数、按名字找都不受影响
        items = [{'kind': 'row', 'text': '限时活动：战歌重奏奖励翻倍', 'button': 'go', 'template': True, 'label': True}]
        items += weekly_list()
        task, name = self.pick(BookList(items), '', 1)
        self.assert_row(task, name, '定序诸理之律')
        task, name = self.pick(BookList(items), '失坠困咎之庭')
        self.assert_row(task, name, '失坠困咎之庭')


class ScrollPositionTest(BookLearnSandbox):
    def test_list_left_in_the_middle_target_above(self):
        # 列表停在上次的位置(中间)，目标在上面：先滚到顶再往下找
        book = BookList(weekly_list(early=EARLY), banners=SCREENSHOT_BANNERS, top=0.31)
        task, name = self.pick(book, '星海迷途之扉', offset=book.offset_of('彼世猩红之幕'))
        self.assert_row(task, name, '星海迷途之扉', 'challenge')
        self.assertTrue(any(s > 0 for s in task.scrolls))

    def test_target_on_current_screen_clicked_without_scrolling(self):
        book = BookList(weekly_list())
        task, name = self.pick(book, '彼世猩红之幕', offset=book.offset_of('彼世猩红之幕'))
        self.assert_row(task, name, '彼世猩红之幕')
        self.assertEqual(task.scrolls, [])

    def test_serial_counts_from_top_even_if_left_in_middle(self):
        book = BookList(weekly_list())
        task, name = self.pick(book, '', 3, offset=book.offset_of('无序边境之火'))
        self.assert_row(task, name, '虚妄诞生之种')

    def test_endless_list_gives_up(self):
        # 列表一直翻得动(或者画面一直在变)：最多翻 max_pages 屏就报错，不会卡死
        items = [row(f'测试首领{i:03d}号', suffix='') for i in range(300)]
        task = FakeListTask.make(BookList(items))
        with self.assertRaises(Exception):
            task.click_book_target_by_name('qiangdi', '不存在之门', max_pages=12)
        self.assertIsNone(task.clicked)
        self.assertEqual(len([s for s in task.scrolls if s < 0]), 12)


def hint_lines(count, top=0.10, step=0.056):
    """列表上方 count 条提示：已知的、没见过的、数字在变的都有。返回 (提示条, 列表可见区顶)。"""
    pool = [lambda k: f'剩余战歌重奏双倍奖励次数：{6 - k % 6}/6', '本周剩余可收取次数：3/3', '筛选查看各等级奖励',
            '新首领挑战限时开启', lambda k: f'倒计时{k}秒后刷新', '首通奖励可领取', '版本更新说明',
            '周常挑战进度', '新增难度已解锁']
    return [(pool[i % len(pool)], top + i * step) for i in range(count)], top + count * step


class TopAreaChangesTest(BookLearnSandbox):
    """上方提示条多了、少了、文字在变，列表起点跟着变：不管它，滑动找名字，点同一行的按钮。"""

    def test_every_weekly_found_whatever_is_above(self):
        for count in (0, 1, 3, 6, 9):
            banners, top = hint_lines(count)
            for early in ((), EARLY):
                book = BookList(weekly_list(early=early), banners=banners, top=top)
                for target in BossBook.SEED_NAMES['zhange']:
                    with self.subTest(hints=count, early=bool(early), target=target):
                        task, name = self.pick(book, target)
                        self.assert_row(task, name, target, 'challenge' if target in early else 'go')
                        self.assertEqual(task.missed_scrolls, 0)

    def test_serial_first_row_whatever_is_above(self):
        # 列表顶上那一行（上方提示条变少时会顶到面板最上面）也要数得到、点得到
        for count in (0, 1, 3, 9):
            banners, top = hint_lines(count)
            with self.subTest(hints=count):
                task, name = self.pick(BookList(weekly_list(), banners=banners, top=top), '', 1)
                self.assert_row(task, name, '定序诸理之律')

    def test_unknown_hints_never_become_rows(self):
        # 列表被挤得只露两行，翻页时顶上那行名字被裁掉、按钮还露着：按钮不能配给紧挨着的、没见过的提示条
        banners, top = hint_lines(9)
        task, name = self.pick(BookList(weekly_list(), banners=banners, top=top), '', 6)
        self.assert_row(task, name, '命途断章之轮')
        names = BossBook.known_names('zhange')
        self.assertEqual(sorted(names), sorted(BossBook.SEED_NAMES['zhange']))

    def test_short_visible_list_does_not_skip_rows(self):
        # 只露两行时一次翻 10 格会把整行翻过去：翻页步长要跟着露出来的高度变小
        banners, top = hint_lines(9)
        task = FakeListTask.make(BookList(weekly_list(), banners=banners, top=top))
        task.click_book_target_by_name('zhange', '命途断章之轮')
        self.assertTrue(all(-10 < s < 0 for s in task.scrolls if s < 0), task.scrolls)

    def test_changing_hint_does_not_keep_scrolling(self):
        # 提示条上的数字每一帧都在变：照样能判断翻到底，找不到就报错，不会翻满 30 屏
        banners, top = hint_lines(6)
        task = FakeListTask.make(BookList(weekly_list(early=EARLY), banners=banners, top=top))
        with self.assertRaises(Exception) as ctx:
            task.click_book_target_by_name('zhange', '不存在之门')
        self.assertIn('not found', str(ctx.exception))
        self.assertLessEqual(len([s for s in task.scrolls if s < 0]), 20)
        self.assertNotIn('倒计时', str(ctx.exception))
        self.assertNotIn('新首领挑战限时开启', str(ctx.exception))


class WheelPositionTest(BookLearnSandbox):
    """滚轮落在能横着滚的奖励图标上，列表不动，会被当成翻到底报错（2026-11-22 实机反馈）。"""

    def test_wheel_never_on_reward_strip(self):
        for count in (0, 3, 9):
            banners, top = hint_lines(count)
            book = BookList(weekly_list(early=EARLY), banners=banners, top=top)
            for target in ('昔日咏叹之钟', '星海迷途之扉', '无冠巨像之心'):
                with self.subTest(hints=count, target=target):
                    task, name = self.pick(book, target, offset=book.offset_of('彼世猩红之幕'))
                    self.assert_row(task, name, target)
                    self.assertEqual(task.eaten_scrolls, 0, task.scroll_points)
                    self.assertTrue(all(not REWARD_STRIP[0] <= x <= REWARD_STRIP[1] for x, _, _ in task.scroll_points))

    def test_wheel_eaten_retries_elsewhere_and_keeps_going(self):
        # 万一名字那一段也吃滚轮（以后界面改了）：列表没动时换到头像那一段再滚，能滚就一直用那里，照样找到
        book = BookList(weekly_list(early=EARLY), banners=SCREENSHOT_BANNERS, top=0.31)
        task = FakeListTask.make(book)
        task.eat_x = [(0.50, 0.84)]
        name = task.click_book_target_by_name('zhange', '昔日咏叹之钟')
        self.assert_row(task, name, '昔日咏叹之钟')
        # 滚到顶那一下、往下第一下各被吃一次（在顶上分不出哪处能滚），之后都用头像那一段
        self.assertLessEqual(task.eaten_scrolls, 2)
        self.assertGreater(len([1 for x, _, c in task.scroll_points if c < 0 and x < 0.5]), 3)

    def test_whole_row_eats_wheel_reports_not_found_without_hanging(self):
        book = BookList(weekly_list(), banners=SCREENSHOT_BANNERS, top=0.31)
        task = FakeListTask.make(book)
        task.eat_x = [(0.0, 1.0)]
        with self.assertRaises(Exception) as ctx:
            task.click_book_target_by_name('zhange', '昔日咏叹之钟')
        self.assertIn('not found', str(ctx.exception))
        self.assertLessEqual(len(task.scrolls), 6)


class FakeMenuTask(FarmEchoTask):
    """左边分类菜单：OCR 读到 texts [(文字, x, y)]，记下点了哪里。"""
    width = W
    height = H
    game_lang = 'zh_CN'

    @classmethod
    def make(cls, texts):
        task = object.__new__(cls)
        task.texts = texts
        task.clicks = []
        task.logs = []
        return task

    def ocr(self, x, y, to_x, to_y, **kwargs):
        return [Box(int(tx * W), int(ty * H), 160, 40, name=t) for t, tx, ty in self.texts
                if x <= tx <= to_x and y <= ty <= to_y]

    def click_box(self, box, *args, **kwargs):
        self.clicks.append(('box', box.name, round(box.y / H, 3)))

    def click_relative(self, x, y, *args, **kwargs):
        self.clicks.append(('rel', x, y))

    def next_frame(self):
        return None

    def sleep(self, *args, **kwargs):
        pass

    def tr(self, message):
        return message

    def log_info(self, message, *args, **kwargs):
        self.logs.append(message)


MENU = ['模拟领域', '凝素领域', '讨伐强敌', '战歌重奏', '无音清剿', '残象聚落', '梦魇祓除']


def menu_texts(shift=0.0, extra=()):
    texts = list(extra)
    for i, title in enumerate(MENU):
        y = 0.27 + i * 0.105 + shift
        texts.append((title, 0.06, y))
        texts.append(('产出高级技能材料', 0.07, y + 0.04))
    # 右边列表里也有「战歌重奏」几个字，不能当成菜单
    texts.append(('剩余战歌重奏双倍奖励次数：6/6', 0.37, 0.19))
    texts.append(('星海迷途之扉・战歌重奏', 0.463, 0.39))
    return texts


class CategoryMenuTest(unittest.TestCase):
    def test_click_category_by_title_when_menu_shifted(self):
        # 活动往菜单最上面插了一个入口，分类整体往下挪
        task = FakeMenuTask.make(menu_texts(shift=0.1, extra=[('限时活动', 0.06, 0.20)]))
        task.open_boss_book('zhange')
        self.assertEqual(task.clicks, [('box', '战歌重奏', round(0.27 + 3 * 0.105 + 0.1, 3))])

    def test_ocr_misread_title(self):
        texts = [(t.replace('梦魇', '梦魔'), x, y) for t, x, y in menu_texts()]
        task = FakeMenuTask.make(texts)
        task.open_boss_book('mengyan')
        self.assertEqual([c[1] for c in task.clicks], ['梦魔祓除', '梦魔祓除'])

    def test_fallback_to_fixed_position(self):
        task = FakeMenuTask.make([])
        task.open_boss_book('qiangdi')
        self.assertEqual(task.clicks, [('rel', 0.24, 0.49)])


class AfterClickTest(unittest.TestCase):
    def make(self, feature):
        task = object.__new__(FarmEchoTask)
        task.wait_feature = lambda *a, **k: feature
        task.tr = lambda m: m
        return task

    def test_challenge_goes_to_team_screen(self):
        self.assertTrue(self.make(Box(0, 0, 1, 1, name='team_start_challenge')).wait_after_book_click())
        self.assertFalse(self.make(Box(0, 0, 1, 1, name='gray_teleport')).wait_after_book_click())

    def test_nothing_opened_raises_with_button_name(self):
        task = self.make(None)
        task.book_button_kind = 'challenge'
        with self.assertRaises(Exception) as ctx:
            task.wait_after_book_click()
        self.assertIn('直接挑战', str(ctx.exception))


class BossBookTextTest(unittest.TestCase):
    def test_boss_names_are_not_banners(self):
        for names in BossBook.SEED_NAMES.values():
            for name in names:
                self.assertFalse(BossBook.is_banner_text(name), name)
        for text in ['限时提前开放', '限時提前開放', '剩余战歌重奏双倍奖励次数：6/6', '本周剩余可收取次数：3/3',
                     '筛选查看各等级奖励', '索拉等级7（当前）']:
            self.assertTrue(BossBook.is_banner_text(text), text)

    def test_button_kind(self):
        for text, kind in [('前往', 'go'), ('直接挑战', 'challenge'), ('直接挑戰', 'challenge'), ('直接 挑战', 'challenge'),
                           ('前往活动', None), ('450', None), ('', None)]:
            self.assertEqual(BossBook.button_kind(text), kind, text)

    def test_category_titles(self):
        for text, category in [('战歌重奏', 'zhange'), ('戰歌重奏', 'zhange'), ('梦魔祓除', 'mengyan'),
                               ('讨伐强敌', 'qiangdi'), ('产出高级技能材料', None), ('剩余战歌重奏双倍奖励次数', None),
                               ('UP', None), ('活动', None)]:
            self.assertEqual(BossBook.category_of_title(text), category, text)


if __name__ == '__main__':
    unittest.main()
