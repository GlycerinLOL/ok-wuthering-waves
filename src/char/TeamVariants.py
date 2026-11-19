"""同一支队伍的多份代码(存档)。

一个队伍目录 configs/custom_teams/<队伍键>/ 同一时刻只能放一份代码, 而工坊里同一支队伍常有好几条轴。
这里给每支队伍一个存档库, 换轴就是把整个队伍目录换成另一份, 当前那份先原样收回库里, 所以怎么换都不丢东西:

    configs/custom_team_variants/<队伍键>/<存档 id>/   队伍目录的完整副本(连同目录里的其它文件)
    configs/custom_team_variants/<队伍键>/state.json   {"active": 现在生效的那份的存档 id}

生效的那份以队伍目录为准, 库里同 id 的副本只是上次收回时的旧样子, 列表里不单独列出。
"""
import hashlib
import json
import re
import shutil
from pathlib import Path

from ok import Logger
from ok.util.config import Config
from src.char.CustomCharLoader import (
    TEAM_CODE_MODE_IMPORT, TEAM_IMPORT_SUFFIX, TEAM_MANIFEST_FILE,
    clear_team_char_cache, get_custom_team_folder, get_custom_team_root, get_team_key,
    import_custom_team, make_work_folder, normalize_team,
)

logger = Logger.get_logger(__name__)

VARIANT_FOLDER = "custom_team_variants"
STATE_FILE = "state.json"
# 运行时生成的东西不跟着存档走
COPY_IGNORE = shutil.ignore_patterns("__pycache__", "cache", "*.pyc")


def get_variant_root(team, create=False):
    folder = Path(Config.config_folder) / VARIANT_FOLDER / get_team_key(team)
    if create:
        folder.mkdir(parents=True, exist_ok=True)
    return folder


def _read_manifest(folder):
    try:
        manifest = json.loads((Path(folder) / TEAM_MANIFEST_FILE).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        manifest = None
    return manifest if isinstance(manifest, dict) else {}


def _manifest_id(manifest):
    parts = [str(manifest.get(field) or "").strip() for field in ("name", "author", "version")]
    text = "_".join(part for part in parts if part)
    text = re.sub(r'[<>:"/\\|?*\s]+', "_", text).strip(" ._")
    return text[:80] or "未命名"


def variant_label(manifest):
    name = str(manifest.get("name") or "").strip() or "未命名"
    version = str(manifest.get("version") or "").strip()
    author = str(manifest.get("author") or "").strip()
    # 包名多半是三个角色的英文名(同队的包全一样, 队伍列表里也已经有了), 下拉里不显示;
    # 轴叫什么写在说明第一行, 拿它当标题。包名本身带中文的才用包名。
    lines = [line.strip() for line in str(manifest.get("description") or "").splitlines() if line.strip()]
    if any(ord(char) > 0x2E7F for char in name):
        title = name
    elif lines:
        title = lines[0][:30]
    else:
        title = ""
    parts = [f"{title} v{version}".strip() if version else title, author]
    return " · ".join(part for part in parts if part) or name


def _signature(folder, class_names):
    """三份代码的指纹。有导入副本就认导入副本: 切到内置代码时 .py 是内置版, 轴本身还在 .import.py 里。"""
    digest = hashlib.sha256()
    for class_name in class_names:
        path = Path(folder) / f"{class_name}{TEAM_IMPORT_SUFFIX}"
        if not path.is_file():
            path = Path(folder) / f"{class_name}.py"
        try:
            code = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None
        digest.update(code.replace("\r\n", "\n").encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def _codes_signature(codes, class_names):
    digest = hashlib.sha256()
    for class_name in class_names:
        digest.update(codes[class_name].replace("\r\n", "\n").encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def _read_state(team):
    try:
        state = json.loads((get_variant_root(team) / STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        state = None
    return state if isinstance(state, dict) else {}


def _write_state(team, state):
    path = get_variant_root(team, create=True) / STATE_FILE
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _stored_ids(team):
    root = get_variant_root(team)
    if not root.is_dir():
        return []
    return sorted((item.name for item in root.iterdir() if item.is_dir() and not item.name.startswith(".")),
                  key=str.casefold)


def _free_id(team, base, signature, class_names):
    """给一份代码挑存档 id: 库里同名同内容的直接沿用, 同名不同内容的往后编号。"""
    root = get_variant_root(team)
    candidate, number = base, 1
    while (root / candidate).is_dir():
        if signature is not None and _signature(root / candidate, class_names) == signature:
            return candidate
        number += 1
        candidate = f"{base}_{number}"
    return candidate


def active_variant_id(team):
    """现在队伍目录里那份代码的存档 id; 队伍目录不存在返回 None。"""
    class_names = normalize_team(team)
    live = get_custom_team_folder(class_names)
    if not live.is_dir():
        return None
    active = _read_state(class_names).get("active")
    if isinstance(active, str) and active:
        return active
    active = _free_id(class_names, _manifest_id(_read_manifest(live)), _signature(live, class_names), class_names)
    _write_state(class_names, {"active": active})
    return active


def forget_active(team):
    """队伍被删掉之后调用: 库里的存档都留着, 只是不再有哪份算生效中。"""
    class_names = normalize_team(team)
    if get_variant_root(class_names).is_dir():
        _write_state(class_names, {})


def list_variants(team):
    """生效的那份排第一, 后面是库里其它存档。每项: id / label / manifest / active。"""
    class_names = normalize_team(team)
    result = []
    active = active_variant_id(class_names)
    if active is not None:
        manifest = _read_manifest(get_custom_team_folder(class_names))
        result.append({"id": active, "label": variant_label(manifest), "manifest": manifest, "active": True})
    root = get_variant_root(class_names)
    for variant_id in _stored_ids(class_names):
        if variant_id == active:
            continue
        manifest = _read_manifest(root / variant_id)
        result.append({"id": variant_id, "label": variant_label(manifest), "manifest": manifest, "active": False})
    return result


def stash_current(team):
    """把队伍目录现在的样子收进库里(覆盖同 id 的旧副本), 返回存档 id。"""
    class_names = normalize_team(team)
    live = get_custom_team_folder(class_names)
    active = active_variant_id(class_names)
    if active is None:
        return None
    root = get_variant_root(class_names, create=True)
    temp = make_work_folder(root, ".stash-")
    try:
        shutil.copytree(live, temp / "copy", ignore=COPY_IGNORE)
        target = root / active
        if target.exists():
            shutil.rmtree(target)
        (temp / "copy").rename(target)
    finally:
        shutil.rmtree(temp, ignore_errors=True)
    return active


def activate_variant(team, variant_id):
    """换成库里的另一份: 当前这份先收回库里, 再把选中的那份铺到队伍目录。返回是否真的换了。"""
    class_names = normalize_team(team)
    if variant_id == active_variant_id(class_names):
        return False
    source = get_variant_root(class_names) / variant_id
    if not source.is_dir():
        raise ValueError(f"找不到存档 {variant_id}")
    live = get_custom_team_folder(class_names)
    parent = get_custom_team_root(create=True)
    stash_current(class_names)
    temp = make_work_folder(parent, f".{get_team_key(class_names)}-")
    try:
        shutil.copytree(source, temp / "new", ignore=COPY_IGNORE)
        if live.exists():
            live.rename(temp / "old")
        try:
            (temp / "new").rename(live)
        except Exception:
            if (temp / "old").exists():
                (temp / "old").rename(live)
            raise
    finally:
        shutil.rmtree(temp, ignore_errors=True)
    _write_state(class_names, {"active": variant_id})
    clear_team_char_cache(class_names)
    logger.info(f"team {class_names} switched to variant {variant_id}")
    return True


def _refresh_manifest(folder, manifest):
    """fork: 同一份代码换了说明/版本再导入, 不重复存但说明跟着最新一次导入(上游行为)。code_mode 保留原样。"""
    path = Path(folder) / TEAM_MANIFEST_FILE
    try:
        current = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        current = {}
    current.update({key: value for key, value in manifest.items() if key != "code_mode"})
    path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")


def add_variant_from_archive(info):
    """把一份导入包(inspect_team_archive 的结果)放进库里, 不动队伍目录。

    返回 (存档 id, 是否新增)。内容和生效的那份或库里某份完全一样就不重复存。"""
    class_names = normalize_team(info["team"])
    signature = _codes_signature(info["codes"], class_names)
    live = get_custom_team_folder(class_names)
    active = active_variant_id(class_names)
    if active is not None and _signature(live, class_names) == signature:
        _refresh_manifest(live, info["manifest"])
        return active, False
    root = get_variant_root(class_names, create=True)
    for variant_id in _stored_ids(class_names):
        if variant_id != active and _signature(root / variant_id, class_names) == signature:
            _refresh_manifest(root / variant_id, info["manifest"])
            return variant_id, False
    base = _manifest_id(info["manifest"])
    variant_id, number = base, 1
    while (root / variant_id).is_dir() or variant_id == active:
        number += 1
        variant_id = f"{base}_{number}"
    temp = make_work_folder(root, ".add-")
    try:
        folder = temp / "copy"
        folder.mkdir()
        for class_name in class_names:
            code = info["codes"][class_name]
            (folder / f"{class_name}.py").write_text(code, encoding="utf-8")
            (folder / f"{class_name}{TEAM_IMPORT_SUFFIX}").write_text(code, encoding="utf-8")
        manifest = dict(info["manifest"])
        manifest["code_mode"] = TEAM_CODE_MODE_IMPORT
        (folder / TEAM_MANIFEST_FILE).write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        folder.rename(root / variant_id)
    finally:
        shutil.rmtree(temp, ignore_errors=True)
    return variant_id, True


def import_archive_as_active(info):
    """导入一份代码并让它生效。队伍原来的代码收进库里, 不会被盖掉。

    返回 (存档 id, 原来那份是否被收进了库)。"""
    class_names = normalize_team(info["team"])
    live = get_custom_team_folder(class_names)
    if not live.is_dir():
        import_custom_team(info)
        variant_id = _free_id(class_names, _manifest_id(info["manifest"]),
                              _codes_signature(info["codes"], class_names), class_names)
        _write_state(class_names, {"active": variant_id})
        return variant_id, False
    variant_id, _added = add_variant_from_archive(info)
    return variant_id, activate_variant(class_names, variant_id)


def delete_variant(team, variant_id):
    class_names = normalize_team(team)
    if variant_id == active_variant_id(class_names):
        raise ValueError("生效中的这份不能删, 先换到别的存档")
    target = get_variant_root(class_names) / variant_id
    if target.is_dir():
        shutil.rmtree(target)
