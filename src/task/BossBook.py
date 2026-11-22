"""F2 素材获取里按名字找 Boss。

旧做法按「第几个」+ 写死的列表总数去点滚动条，游戏每加一个 Boss 就整体错位
（2026-11-19 实机：讨伐强敌 23 个/写死 20，选 10 点到叹息古龙、选 20 点到聚械机偶；
战歌重奏 11 个/写死 9，选 6~9 全错）。这里只放和画面无关的部分：名字表、模糊比对、
学到的新名字的存取；滚动和 OCR 在 BaseWWTask.click_book_target_by_name。
"""
import json
import os
import re

from ok import Logger

logger = Logger.get_logger(__name__)

# 配置里「传送到哪类」→ open_boss_book 的分类名
CATEGORY_OF_MODE = {
    'Weekly Challenge': 'zhange',
    'Boss Challenge': 'qiangdi',
    'Special Nightmare': 'mengyan',
}

# 2026-11-19 2K 实机从上往下扫到的顺序（新地区排在最上面）。只当下拉框的初始选项，
# 游戏里新出的 Boss 由 learn_names 记进 LEARNED_FILE，下次启动出现在下拉框里。
SEED_NAMES = {
    'zhange': ['定序诸理之律', '失坠困咎之庭', '虚妄诞生之种', '星海迷途之扉', '烬夜天启之章',
               '命途断章之轮', '彼世猩红之幕', '时序命定之争', '无冠巨像之心', '无序边境之火',
               '昔日咏叹之钟'],
    'qiangdi': ['天傀劫煞', '万囮牢·朽躯', '梦魇亚当·重锤', '无铭探索者', '海维夏', '炉芯机骸',
                '海之女', '伪作的神王', '芬莱克', '荣耀狮像', '叹息古龙', '罗蕾莱', '异构武装',
                '无归的谬误', '无冠者', '朔雷之鳞', '云闪之鳞', '燎照之骑', '飞廉之猩', '哀声鸷',
                '无常凶鹭', '辉萤军势', '聚械机偶'],
    'mengyan': ['梦魇·赫卡忒', '梦魇·凯尔匹', '梦魇·辉萤军势', '梦魇·哀声鸷', '梦魇·燎照之骑',
                '梦魇·无冠者', '梦魇·朔雷之鳞', '梦魇·云闪之鳞', '梦魇·无常凶鹭', '梦魇·飞廉之猩'],
}

# 战歌重奏列表里只写副本名，玩家叫的是 Boss 名，下拉框显示成「Boss（副本名）」。
# 2026-11-19 对的表：fandom 中文 wiki 头目页「于 XX 出现」+ 材料页来源核实 9 个；
# 角/伤痕按排除法（1.2 公告里角的副本叫「命定的纷争」，伤痕的掉落物是火）。
# 新出的周本不在表里就只显示副本名，补一行即可。
# 用玩家口头的简称：设置页下拉框正文只有 196px（13px 字），「天演溯心·衍律化形（…）」要 211px 会被截。
BOSS_OF_REALM = {
    'zhange': {
        '定序诸理之律': '天演溯心',
        '失坠困咎之庭': '千傀重楼',
        '虚妄诞生之种': '达妮娅',
        '星海迷途之扉': '辛吉勒姆',
        '烬夜天启之章': '利维亚坦',
        '命途断章之轮': '芙露德莉斯',
        '彼世猩红之幕': '赫卡忒',
        '时序命定之争': '角',
        '无冠巨像之心': '无妄者',
        '无序边境之火': '伤痕',
        '昔日咏叹之钟': '鸣钟之龟',
    },
}

LEARNED_FILE = os.path.join('configs', 'boss_book_names.json')

# OCR 在这个字体上的固定误读（实机：魇→魔 每次都错，繁体字偶尔冒出来）
_CONFUSABLE = str.maketrans({'魔': '魇', '維': '维', '紅': '红', '異': '异', '昇': '异', '無': '无'})
_DECOR = re.compile(r'战歌重奏')
_NON_WORD = re.compile(r'[^0-9A-Za-z一-鿿]')
_CJK = re.compile(r'[一-鿿]')


def norm(text):
    """去掉间隔点/空格/「战歌重奏」后缀，统一 OCR 常错的字。"""
    text = (text or '').translate(_CONFUSABLE)
    text = _DECOR.sub('', text)
    return _NON_WORD.sub('', text)


def display_name(text):
    """下拉框里显示的名字：周本去掉「·战歌重奏」后缀，间隔点统一成 ·。"""
    text = _DECOR.sub('', text or '').replace('・', '·').strip(' ·・')
    return text


def edit_distance(a, b):
    if a == b:
        return 0
    if not a or not b:
        return len(a) + len(b)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def allowed_distance(target_norm):
    # 7 字以内只容 1 个错字：梦魇·朔雷之鳞 / 梦魇·云闪之鳞 只差 2 个字，不能混
    return 1 if len(target_norm) <= 7 else 2


def match_distance(ocr_text, target):
    """OCR 文本和目标名的距离；超出容错返回 None。"""
    a, b = norm(ocr_text), norm(target)
    if len(a) < 2 or not b:
        return None
    d = edit_distance(a, b)
    return d if d <= allowed_distance(b) else None


def nearer_to_other(ocr_text, distance, others):
    """OCR 文本离别的已知 Boss 名不比离目标远：说明读到的多半是那个 Boss。"""
    a = norm(ocr_text)
    return any(edit_distance(a, norm(o)) <= distance for o in others)


_PROGRESS = re.compile(r'\d+\s*/\s*\d+')
_LATIN = re.compile(r'[A-Za-z]')


def is_progress_text(text):
    """梦魇聚落名下面那行「已击败残象：0/36」。"""
    return bool(text) and bool(_PROGRESS.search(text))


def is_candidate_text(text, cjk_only=True):
    """名字列里可能是 Boss 名的 OCR 文本（排除进度行、名字下面那行装饰字母、数字）。

    中文客户端只认带 2 个以上汉字的；外文客户端放宽到 3 个以上字母（装饰字母那行没按钮，不会被数进去）。
    """
    if not text or is_progress_text(text):
        return False
    if len(_CJK.findall(text)) >= 2:
        return True
    return not cjk_only and len(_LATIN.findall(text)) >= 3


def same_name(a, b):
    return match_distance(a, b) is not None or match_distance(b, a) is not None


def similar_text(a, b):
    """两段已规范化的字差不多（提示条上倒计时、次数在变也算同一条）。"""
    if a == b:
        return True
    if not a or not b:
        return False
    return edit_distance(a, b) <= max(1, min(len(a), len(b)) // 3)


# 列表里不是 Boss 行的提示条和分组标题。2026-11 实机「战歌重奏」页：最上面多了「剩余战歌重奏双倍奖励次数：6/6」
# 和「限时提前开放」分组（主线没推到也能提前打的 Boss），把原来的地区分组和 Boss 行整体往下挤。
# Boss 名里没有这些字；就算这种条子旁边以后带了按钮，也不能当成 Boss 行去数、去学名字。
_BANNER = re.compile(r'提前开放|提前開放|次数|次數|筛选|篩選|奖励|獎勵|剩余|剩餘|索拉等级|索拉等級|活动|活動|'
                     r'Early\s*Access|Remaining|Filter', re.IGNORECASE)


def is_banner_text(text):
    return bool(text) and bool(_BANNER.search(text))


# 每行右边的按钮：地区里解锁了的是「前往」(打开地图传送)，「限时提前开放」分组里是「直接挑战」(直接进挑战界面)
_BUTTON_KINDS = (('challenge', re.compile(r'^(直接)?挑[战戰]$|^Challenge$', re.IGNORECASE)),
                 ('go', re.compile(r'^前往$|^(Go|Proceed)$', re.IGNORECASE)))
BUTTON_LABEL = {'challenge': '直接挑战', 'go': '前往', None: '按钮'}


def button_kind(text):
    """按钮上的字 → 'challenge' / 'go'；不是按钮字返回 None。"""
    t = _NON_WORD.sub('', text or '')
    return next((kind for kind, pattern in _BUTTON_KINDS if pattern.match(t)), None)


# 素材获取左边的分类菜单（简/繁）。活动会往这一栏插入口，按字找，找不到才退回写死的位置。
CATEGORY_TITLES = {
    'moni': ('模拟领域', '模擬領域'),
    'ningsu': ('凝素领域', '凝素領域'),
    'qiangdi': ('讨伐强敌', '討伐強敵'),
    'zhange': ('战歌重奏', '戰歌重奏'),
    'wuyin': ('无音清剿', '無音清剿'),
    'canxiang': ('残象聚落', '殘象聚落'),
    'mengyan': ('梦魇祓除', '夢魘祓除'),
}


def _title_norm(text):
    return _NON_WORD.sub('', (text or '').translate(_CONFUSABLE))


def category_of_title(text):
    """左边菜单里读到的字是哪个分类（整条只差 1 个字以内才算），否则 None。

    「产出高级技能材料」这种副标题、右边「剩余战歌重奏双倍奖励次数」这种长条都不算。
    """
    t = _title_norm(text)
    if not 3 <= len(t) <= 5:
        return None
    best = None
    for category, titles in CATEGORY_TITLES.items():
        d = min(edit_distance(t, _title_norm(title)) for title in titles)
        if d <= 1 and (best is None or d < best[0]):
            best = (d, category)
    return best[1] if best else None


def _read_learned():
    try:
        with open(LEARNED_FILE, encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as e:
        logger.warning(f'read {LEARNED_FILE} failed: {e}')
        return {}


def known_names(category):
    """下拉框选项：学到的顺序优先（来自游戏），再补上种子里没出现的。"""
    learned = [n for n in _read_learned().get(category, []) if isinstance(n, str) and n]
    names = list(learned)
    for seed in SEED_NAMES.get(category, []):
        if not any(same_name(seed, n) for n in names):
            names.append(seed)
    return names


_LABEL = re.compile(r'^(.+?)\s*[（(]\s*(.+?)\s*[）)]\s*$')


def option_label(category, name):
    """列表里的名字 → 下拉框写法：周本有对照的写成「Boss（副本名）」，其余原样。"""
    for realm, boss in BOSS_OF_REALM.get(category, {}).items():
        if same_name(realm, name):
            return f'{boss}（{realm}）'
    return name


def realm_of(value):
    """下拉框/配置里的值 → 列表里要找的名字（「Boss（副本名）」取括号里的）。"""
    m = _LABEL.match(value or '')
    return m.group(2) if m else (value or '')


def known_options(category):
    return [option_label(category, n) for n in known_names(category)]


def to_option(category, value):
    """配置里存的值换成下拉框里有的那一项（旧版存的是纯副本名，不换下拉框显示不出来）。"""
    if not value:
        return value
    realm = realm_of(value)
    return next((o for o in known_options(category) if same_name(realm_of(o), realm)),
                option_label(category, realm))


def merge_order(known, observed):
    """把 observed（本次在游戏里从上往下读到的，已去重）里的新名字插进 known 对应的位置。

    新名字放在 observed 里它后面第一个已知名字之前；后面没有已知名字就接在末尾。
    返回 (合并后的列表, 新名字列表)。
    """
    result = list(known)
    new_names = []
    for i, name in enumerate(observed):
        if any(same_name(name, k) for k in result):
            continue
        anchor = None
        for later in observed[i + 1:]:
            anchor = next((idx for idx, k in enumerate(result) if same_name(later, k)), None)
            if anchor is not None:
                break
        if anchor is None:
            result.append(name)
        else:
            result.insert(anchor, name)
        new_names.append(name)
    return result, new_names


def learn_names(category, observed):
    """记下本次读到的列表顺序；返回游戏里有、下拉框里还没有的新名字。"""
    if not observed:
        return []
    merged, new_names = merge_order(known_names(category), [display_name(n) for n in observed])
    if not new_names:
        return []
    data = _read_learned()
    data[category] = merged
    try:
        os.makedirs(os.path.dirname(LEARNED_FILE), exist_ok=True)
        with open(LEARNED_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f'write {LEARNED_FILE} failed: {e}')
    return new_names
