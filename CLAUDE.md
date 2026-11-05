# CLAUDE.md — ok-ww fork（GlycerinLOL）

《鳴潮》影像辨識自動化工具，基於 PyPI `ok-script` 框架。Windows-only。
這是 `ok-oldking/ok-wuthering-waves` 的個人 fork：**自用開發 + 定期同步上游**。
詳細架構分析見 `fork-notes/project-analysis.md`；`AGENTS.md`（上游）一併適用，但本機 venv 是 Windows venv：一律用 `./.venv/Scripts/python.exe`，AGENTS.md / `use-local-venv` 的 POSIX 分支（`./.venv/bin/python`、退回 `python`）不適用。

## 環境（最重要）

- 執行期只能在 Windows：`ok-script` 無條件依賴 `pywin32`，WSL/Linux 裝不起來，不要嘗試。
- Repo 位置：`H:\dev\ok-ww` = WSL 的 `/mnt/h/dev/ok-ww`。Claude 在 WSL 編輯，透過 Windows interop 呼叫 Windows venv。
- 呼叫 Windows Python：cwd 在 `/mnt/h/...` 時直接跑 `./.venv/Scripts/python.exe ...`（interop 會把 cwd 轉成 `H:\dev\ok-ww`，已驗證）。
- 不要用 `cmd.exe`（不支援 UNC/轉譯路徑）；要跑 `.ps1` 用 `powershell.exe -NoProfile -ExecutionPolicy Bypass -File run_tests.ps1`。
- 建 venv：`/mnt/c/Users/brian/AppData/Roaming/uv/python/cpython-3.12.13-windows-x86_64-none/python.exe -m venv .venv`，再 `./.venv/Scripts/python.exe -m pip install -r requirements.txt`。只支援 Python 3.12。
- 不要 `pip install ".[dev]"`（會觸發 cython build）；需要時直接 `pip install polib pytest`。
- WSL 網路是 NAT：WSL 端連不到 Windows `127.0.0.1` 上的 ok-script MCP server。MCP client 腳本一律用 Windows venv 跑。

## 指令

- 單檔測試（CI 同款，標準 runner）：`./.venv/Scripts/python.exe -u run_test.py tests/TestChar.py`
- 全部測試（從 WSL，實測 53 檔 / 520 tests 全過、約 3 分鐘，背景執行）：`for f in tests/*.py; do ./.venv/Scripts/python.exe -u run_test.py "$f" > "$SCRATCH/$(basename $f).log" 2>&1; echo "$? $f"; done`（`$SCRATCH` = session scratchpad）。Windows 端用 `run_tests.ps1`。
- 判讀結果看 `Ran N tests` / `OK` / `FAILED`；log 含非 UTF-8 位元組，grep 要加 `-a`，否則會被當二進位檔而漏抓。
- 呼叫 `.exe` 若只回 `UtilAcceptVsock: accept4 failed 110` 這類 WSL 錯誤，是 interop 逾時（多半因 WSL 記憶體吃緊），重跑即可，不是測試失敗。
- 啟動：`main.py`（release）/ `main_debug.py`（debug）——見下方「實機規則」
- i18n：`./.venv/Scripts/python.exe .agents/skills/ok-script-i18n/scripts/task_i18n_helper.py scan|check|compile ...`

## 動手前先讀上游 skill

Claude Code 不會自動載入 `.agents/skills/`，依任務手動讀對應 `SKILL.md`：

| 要改的東西 | 先讀 |
|---|---|
| `src/task/*` 任務 | `.agents/skills/ok-script-tasks/SKILL.md` + `references/` |
| `src/char/*` 角色 | `.agents/skills/ok-ww-characters/SKILL.md` + `references/character-patterns.md` |
| 任何 UI 字串 / config key | `.agents/skills/ok-script-i18n/SKILL.md` |
| 實機開發/驗證流程 | `.agents/skills/ok-script-develop/SKILL.md` |

已知上游 skill 過時處（**只記錄，不要改上游檔案**，會增加衝突面）：
- `character-patterns.md` 提到的 `Healer.py`、`Priority.FAST_SWITCH`、`res_cd`/`liberation_cd` 不存在；實際是 `SwitchPriority`、`get_switch_priority()`、`CharFactory._char_dict_raw` 的 `{cls, char_type, ring_index, buff_time}`。
- `deploy/SKILL.md` 路徑寫 `.agent\`，實際是 `.agents\`。且 fork 不使用 deploy（見下）。

框架本身的權威說明在已安裝的 ok-script 內（以它為準，上游 skill 只是補充）：
`.venv/Lib/site-packages/ok/agent_resources/.agents/skills/` 的 `develop`、`write-test`、`reload-tasks`、`generate-templates`，以及 `.venv/Lib/site-packages/ok/agent_resources/docs/mcp.md`。
- MCP runtime：`./.venv/Scripts/python.exe -m ok mcp --config config:config`（未實測）；連線資訊在 `logs/mcp/connections/<pid>-<port>.json`，token 不要印出。工具：`list_tasks`、`reload_tasks`、`run_task`、`get_run_status`、`stop_task`、`screenshot`。
- ok-script 明確要求：開發流程不用 Computer Use，擷取/輸入一律走 ok-script API + MCP。

## 程式慣例

- 新任務：一檔一 class；`super().__init__(*args, **kwargs)` 先呼叫；metadata 寫在 `__init__`（`name`/`description`/`default_config`/`config_description`/`config_type`/`icon`/`group_name`）。一次性任務混入 `WWOneTimeTask` 並先呼叫其 `run()`；觸發型繼承 `TriggerTask` 並設 `trigger_interval`。
- 先重用 `BaseWWTask`/`BaseCombatTask`/`CombatCheck` 現成 helper（`ensure_main`、`in_team_and_world`、`openF2Book`、`wait_click_travel`、`walk_to_box`、`use_stamina`、`wait_ocr`、`wait_click_feature`…），不要自己寫 polling。
- 等待用 `wait_until`/`wait_ocr`/`wait_click_feature`；所有迴圈要有 timeout，迴圈內呼叫 `next_frame()` 或 `sleep()`。
- 熱鍵一律讀 `self.key_config['Echo Key']` 等，不要寫死按鍵。
- 座標用相對比例（`box_of_screen`、`click_relative`），超寬螢幕元素要 `hcenter=True` 或加入 `config.py` 的 `hcenter_features`。
- Config key 用穩定英文（會存成 JSON）；UI 字串包 `self.tr(...)`，`.po` 改完必 compile `.mo`，兩者一起提交。
- OCR 比對文字要同時涵蓋簡中/繁中/英文（依任務 `supported_languages`）。
- Log 用 `self.log_info/log_debug/log_error`，進度用 `self.info_set`。
- 角色：`BaseChar` 子類別實作 `do_perform()`，結尾 `switch_next_char()`；必須在 `CharFactory._char_dict_raw` 註冊；計時用 `time_elapsed_accounting_for_freeze`。

## 生成物：不准手改

- `assets/templates/*.png`、`assets/coco_annotations.json`、`ok_templates/coco_annotations.json` 由 ok-ww GUI「模板」分頁或 `CocoTemplateStore` + `ok.feature.FeatureSet.generate_templates` 產生；不要手裁 PNG、不要改檔名（檔名帶幾何資訊）。
- `src/Labels.py`：ok-script 3.0.3 沒有產生器，上游做法是同一個 commit 手動依字母序補一行。模板、coco、`Labels.py` 一起提交。不要憑空新增 `Labels` 名稱；沒有截圖就回報缺什麼，不要捏造。
- `ok_templates/` 的完整截圖可以直接當 `TaskTestCase.set_image()` 的測試素材，不必複製到 `tests/images/`。
- `.github/workflows/*.lock.yml` 是 `gh aw compile` 產物。

## Fork / 上游同步

- `origin` = 本 fork，`upstream` = ok-oldking。同步：`git fetch upstream && git merge upstream/master`（merge，不 rebase；master 已推送）。
- 同步後必跑 `./.venv/Scripts/python.exe -u run_test.py tests/TestForkCharacterCodeTab.py`。fork 保留了上游已移除的「角色代碼」分頁（`config.py` 的 `custom_tabs`）：
  - `config.py` 衝突 → 保留雙方，`custom_tabs` 那段留著。
  - 上游刪了 `src/gui/CharacterCodeTab.py` 或它 import 的模組（`CustomCharLoader` 等）→ merge 不會報衝突、會靜默刪除，測試會抓到；用 `git checkout ORIG_HEAD -- <檔案>` 拿回來再修相容性。
  - ok-script 拿掉 `custom_tabs` 支援 → 第三個測試失敗，需改用其他掛載方式，先回報使用者。
- 功能開 branch（`feat/...`、`fix/...`），完成後 merge 回 master。
- 降低衝突：優先新增檔案，少改熱檔（`BaseCombatTask.py`、`BaseWWTask.py`、`FarmEchoTask.py`、`CharFactory.py`、`BaseChar.py`）；`config.py` 任務清單只在尾端追加；不做順手格式化/重構。
- 本檔用 `git add -f CLAUDE.md` 追蹤（上游 `.gitignore` 忽略它，同步不會衝突）。不要改上游 `.gitignore`；本機雜項（`.omc/`）放 `.git/info/exclude`。
- 不要推 `v*` tag 到 origin（會觸發 `build.yml` release，fork 沒有 secrets）；不要用 `deploy` skill。

## 驗證分級

1. 離線測試（Windows venv + `run_test.py`）：改到的模組要跑對應測試；新邏輯留一個最小可跑的測試（mock 式參考 `tests/test_suoming.py`，截圖式參考 `tests/TestForte.py`）。
2. 實機驗證（ok-script MCP + 遊戲執行中）才算證明遊戲行為正確。import 成功、mock click、測試通過都**不等於**實機成功；沒實機驗證就明說。
- `src/char/*`、combat helper 改動需重啟 runtime，`reload_tasks` 不會刷新它們。

## 實機規則

- 未經使用者在該輪明確同意，不得啟動 `main.py`/`main_debug.py`、不得透過 MCP 執行會對遊戲送出輸入的任務（有封號風險）。
- 不碰 `configs/`（含私人帳號資料，已 gitignore）。
