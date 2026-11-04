# ok-ww 專案分析（2026-11-04，基準 commit `ad251fbd`）

Fork 專用筆記，不屬於上游 mkdocs（`docs/`）。標 **[未驗證]** 的是從程式呼叫端推論、尚未以安裝好的 `ok-script` 核對的內容。

## 1. 技術棧

| 層 | 內容 |
|---|---|
| 語言 | Python 3.12（pyproject 寫 `>=3.11`，CI/文件/pyappify 都固定 3.12） |
| 框架 | `ok-script[ocr,qt]==3.0.3`：任務排程、截圖（WGC/BitBlt）、背景輸入（PostMessage）、模板比對、OCR、Qt GUI、MCP 開發伺服器 |
| GUI | `ok-pyside-minimal`（PySide6 精簡版）；3.0.0 起改用 ok-script Qt kit，任務分組顯示 |
| OCR | `onnxocr-ppocrv5`（PP-OCRv5，OpenVINO + NPU） |
| 偵測 | YOLOv8 聲骸模型 `assets/echo_model/echo.onnx`（單類別 `echo`），OpenVINO（NPU→CPU）或 ONNX Runtime（DML→CUDA→CPU） |
| 影像 | OpenCV、numpy |
| 其他 | `mcp==2.3.0`（ok-script 內建 MCP server 供 AI agent 操控實機）、`pyappify`（打包/安裝器/更新） |
| 測試 | `unittest` + `ok.test.TaskTestCase`，以 `run_test.py` 逐檔執行 |
| 文件 | mkdocs-material（`docs/`，5 語系） |
| CI | GitHub Actions，windows-latest |

## 2. 啟動與設定

- `main.py` → `OK(config).start()`；`main_debug.py` 開 debug；`main_web.py` 走 web GUI（pywebview）。
- `config.py` 的 `config` dict 是唯一宣告點：`onetime_tasks`/`trigger_tasks`（`[module, class]`）、`scene`=`WWScene`、`my_app`=`src.globals.Globals`、`template_matching`、`ocr`、`windows`（exe `Client-Win64-Shipping.exe`、hwnd `UnrealWindow`、`interaction: PostMessage`）、`supported_resolution`（16:9，最小 1280x720）。
- `global_configs`：`Game Hotkey`（Q/R/E/T/Space/LShift/Tab/F2/B）、`Character Config`（Iuno C6、Roccia C6、Chisa DPS、Suoming DPS）、`Monthly Card Config`。
- `Globals`：`logged_in` 旗標、小地圖箭頭、lazy YOLO 模型。`WWScene`：每幀快取（`in_team`、戰鬥狀態、CD 是否已刷新）。
- `src/game_launcher.py`：解析庫洛啟動器狀態檔判斷 sd/hd/uhd 資源包，回傳 `-krqlv=<pkg>`。
- `calculate_pc_exe_path`：UserAssist 登錄檔 → Uninstall 登錄檔找遊戲 exe。

## 3. 任務

類別鏈：`BaseTask` → `BaseWWTask`（1356 行，通用 helper）→ `CombatCheck`（戰鬥判定）→ `BaseCombatTask`（1378 行，隊伍/切人/CD/復活）。`WWOneTimeTask` 是普通 class 的 mixin（不繼承 BaseTask，已讀原始碼確認），`run()` 先重設戰鬥狀態、跑 `MouseResetTask`、啟用 PostMessage。

一次性任務（已註冊）：

| 任務 | 用途 |
|---|---|
| DailyTask | 一條龍：體力、刷本、夢魘巢穴、附加任務；以 `run_task_by_class` 呼叫其他任務 |
| FarmEchoTask | 刷 4C 聲骸（傳送、Boss 選擇、Yolo/繞圈/走路拾取） |
| NightmareNestTask | 夢魘巢穴 |
| TacetTask | 無音區 |
| ForgeryTask / SimulationTask | 鍛造/模擬挑戰（繼承 `DomainTask`） |
| MultiAccountDailyTask / MultiAccountConfigTask | 多帳號（Win32 登入 ComboBox 切帳號，`configs/MultiAccountConfig.json`） |
| MergeEchoTask / EnhanceEchoTask / ChangeEchoTask | 聲骸合成/強化（僅中文）/換主詞條（僅簡中） |
| GardenTask | 週常花園 |
| TacetEliminationTask | 3.7 活動「無音消除」三消解題（HSV + OCR，背景拖曳無效，需前景 Pynput） |

觸發任務：AutoCombatTask（0.1s）、AutoPickTask、AutoLoginTask、AutoDialogTask（`SkipDialogTask.py`）、FastTravelTask（預設關）、MouseResetTask。

程式碼存在但**沒有任何地方引用**（2026-11-04 grep `config.py`/`src`/`main*.py`）：`FarmMapTask`、`FiveToOneTask`、`DiagnosisTask`（config 中註解掉）、`CharacterCodeTask`、`src/gui/MultiAccountConfigTab.py`、`src/gui/CharacterCodeTab.py`。最近 commit「remove multi-account config tab」「Remove character code tab」從 config 拿掉了這兩個分頁，tests 仍引用它們。是否還被 ok-script plugin 機制載入 **[未驗證]**，不要假設已死或還活著。

常用 helper（新功能優先重用）：
- 狀態：`in_team()`、`in_team_and_world`、`in_world`、`in_realm`、`is_main`、`ensure_main`、`wait_in_team_and_world`
- 登入/彈窗：`wait_login`、`handle_monthly_card`（`sleep()` 被覆寫成會處理月卡）、`click_confirm`、`click_skip_dialog_confirm`
- 導航：`openF2Book`、`open_boss_book`、`click_on_book_target`、`wait_click_travel`、`zoom_map`、`change_time_to_night`
- 移動/拾取：`walk_to_box`、`walk_until_f`、`pick_f`、`pick_echo`、`yolo_find_echo`、`walk_to_yolo_echo`、小地圖角度導航
- 體力：`get_stamina`、`use_stamina`
- 戰鬥：`combat_once`、`load_chars`、`load_hotkey`、`switch_next_char`、`refresh_cd`/`get_cd`、`revive_action`、`teleport_to_heal`、`restart_combat_in_abyss`
- 例外：`NotInCombatException` → `CharDeadException` → `CharRevivedException`；`sleep_check()` 在 sleep 中途拋出以中止戰鬥

## 4. 辨識

- 模板：`assets/templates/<label>_<srcW>x<srcH>_<x>_<y>.png`（296 個，多為 3840x2160 擷取），以 `src/Labels.py`（str Enum，295 個）或字串引用。`box_*` 是命名區域。
- 流程：GUI「模板」分頁（或 `CocoTemplateStore` + `FeatureSet.generate_templates`）加截圖、框選、命名 → 更新 `ok_templates/coco_annotations.json`、`assets/`；`Labels.py` 需在同一個 commit 手動依字母序補一行（ok-script 3.0.3 沒有產生器，見 §10）。
- `process_feature.py` 對特定模板做前處理（黑白化、二值化、對話圖示）。
- 超寬螢幕：`hcenter_features`/`vcenter_features` 與 `hcenter=True`；跳過對話有 0.75x 寬模式模板。
- OCR：`self.ocr(box, match=...)`、`wait_ocr`、`find_boxes`、`add_text_fix`；依 `game_lang`（從視窗標題判斷）切語系。

## 5. 角色系統

- `CharFactory._char_dict_raw`：key 是 `Labels.char_<name>`（或 tuple，如男女主），value `{cls, char_type, ring_index, buff_time?}`。`get_char_by_pos` 在 `box_char_1..3` 用 `find_best_match_in_box(…, 0.6)` 辨識；信心 >0.92 沿用舊實例；失敗時退回 `BaseChar("unknown")`。
- `CharType`：MAIN_DPS / SUB_DPS / HEALER；預設 buff 14s（DPS）/ 28s（healer）。`SwitchPriority`：NO 0 / LOW 100 / NORMAL 200 / HIGH 300 / MUST 400。
- 生命週期：`combat_once` 迴圈 `get_current_char().perform()` → `do_perform()` → `switch_next_char()`。切人依協奏值（滿 = 有 intro）、buff 剩餘、`get_switch_priority`；healer 滿協奏切出後鎖 16s。
- CD：OCR 技能列數字，每幀一次；冰凍時間（大招/intro 動畫）以 `time_elapsed_accounting_for_freeze` 扣除。
- 角色設定旗標靠角色覆寫 `get_char_type()`/`get_buff_time()` 即時生效。
- `CustomCharLoader.py`：使用者自訂角色/隊伍程式碼（`configs/custom_teams/...`），AST 驗證後 importlib 載入。`load_custom_char_class`（單角色）不被 CharFactory 使用，只有 `CharacterCodeTask`（未註冊）與測試呼叫；CharFactory 只走 `load_team_char_class`（已 grep 確認）。
- 新增角色：標註頭像 `char_<name>` 與機制模板 → `src/char/<Name>.py` → `CharFactory` 註冊 → `tests/TestChar.py` 斷言 + mock 測試 →（選用）`config.py` 旗標 + i18n。

## 6. 測試

- 53 檔、520 個 test case、約 1 萬行；全套約 3 分鐘。`run_test.py` patch 掉 OCR 預載與 `tr`，每檔獨立 process。
- 截圖式（`TaskTestCase`，17 檔）：`set_image('tests/images/x.png')` 後斷言偵測結果。`tests/images` 124MB。
- Mock 式：`test_suoming*.py`、`test_rover.py`、`test_hsin.py`、`test_phrolova_switch.py`、`TestCustomCharLoader` 等。
- 全部都需要 `ok-script` → 只能在 Windows 跑。

## 7. i18n

zh_CN、zh_TW、ja_JP、ko_KR、es_ES；msgid 為英文原字串。流程：`task_i18n_helper.py scan` → 補所有語系 → 翻譯 → `compile` → `.po` + `.mo` 一起提交。

## 8. CI（fork 視角）

| Workflow | 觸發 | fork 處置 |
|---|---|---|
| `test.yml` | PR | 保留，無需 secrets |
| `build.yml` | `v*` tag | 需上游 secrets/身分，不要推 tag；建議在 fork Actions 停用 |
| `docs.yml` | push master | 可 build；deploy 需開 Pages，URL 寫死上游 |
| `sign_exe.yml`、`mirrorchyan_*.yml` | 手動/release | 上游專用，停用 |
| `issue-triage`、`pull-request-review`（gh-aw） | issue/PR | 需 Copilot token，停用 |
| `close_stale_issues.yml`、`sync_issue_labels.yml` | 排程/手動 | 可有可無 |

## 9. 開發歷史

- 起始 2024-06-08；2154 commits、1117 個 tag（最新 `v3.7.19`）；作者 ok-oldking 為主，另有 firedcto、RU、medicinemelancholy 等社群貢獻。
- 節奏：每月 60–100 commits，幾乎每次修正都發 tag；commit 訊息中英混雜，有時用 conventional prefix。
- 2026-08 起熱點：`src/task`（144 次變更）> `src/char`（83）> `src/gui`、`Labels.py`。最熱檔：`FarmEchoTask`、`BaseCombatTask`、`BaseWWTask`、`CharFactory`、`BaseChar`。
- 近期重點：ok-script 2.x → 3.0.3（Qt kit GUI、任務分組、原生遊戲圖示）；3.7 無音消除活動（以 MCP 開發）；Roccia C6 空中重擊；隊伍偵測/匯出；超寬螢幕修正；移除下載連結、贊助連結、多帳號與角色代碼分頁。
- 上游開發模式：Codex + ok-script MCP 實機驅動（`.agents/skills`、`.codex/`）。PR 必須附 AI 審查說明。

## 10. 核對紀錄

- [x] 2026-11-04：Windows venv（uv CPython 3.12.13）安裝 `requirements.txt` 成功，`ok-script 3.0.3`；53 個測試檔、520 tests 全過，0 skip（`TestAbyssRestart` 首跑遇 WSL interop 逾時，重跑 15/15 OK）。
- [x] WSL interop 從 `/mnt/h/dev/ok-ww` 直接呼叫 `./.venv/Scripts/python.exe` 正常，cwd 轉為 `H:\dev\ok-ww`。
- [x] ok-script 自帶 skill：`.venv/Lib/site-packages/ok/agent_resources/.agents/skills/`（develop、write-test、reload-tasks、generate-templates、setup-env、setup-project、use_venv）+ `agent_resources/docs/{mcp,feature-crops,agent-setup}.md`。模板管線是 `CocoTemplateStore` + `FeatureSet.generate_templates`。
- [x] `src/Labels.py`：ok-script 3.0.3 內沒有產生器（grep 無結果）；歷史 commit 顯示與 coco 標註同 commit 手動加行。上游 skill 寫「generated」不準確。
- [ ] `custom_tasks`/`plugin`：ok-script 有 `core/script_packager.py`（`ok_tasks/` + `manifest.json`）與 `core/plugins.py`（plugin 資料夾 / GitHub release 安裝）。fork 專用任務要不要放這裡，等第一個實際需求再決定。
- [ ] `src/gui/*Tab.py`、未註冊任務是否還被載入（需實機或讀 ok-script 載入流程）
- [ ] MCP runtime（`python -m ok mcp --config config:config`）與 `uac: true` 是否需要系統管理員身分：尚未實測
