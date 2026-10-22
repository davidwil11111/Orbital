# Windows test results — batch 1022 (specs 126–131)

Tested 2026-10-22 23:38 → 2026-10-23 00:28 (local time) against the CI-built installer only.

| Item | Value |
|---|---|
| Branch / tip | `batch/1022-specs-126-131` @ `2c271fb` (installer built from `dd03041`, CI run 39781257878) |
| Installer | `Orbital-Setup-0.17.0.exe`, 411,404,790 bytes, artifact `Orbital-Windows-installer` (not expired, downloaded via `gh run download`) |
| sha256 | `3e043a7d299376bff765f63327dd722d3fec31fbbef6f4ef5ea0de8cb6864360` |
| Windows edition (winver) | Windows 10 Home China, 22H2, build 19045.6466 |
| Node on PATH | v22.16.0 (`C:\Program Files\nodejs\node.exe`), npm 9.4.1 |
| Claude Code CLI | 2.1.303, **not signed in** (`claude auth status` → loggedIn:false; `claude -p` → "OAuth session expired and could not be refreshed") |
| Codex CLI | codex-cli 0.162.1, logged in (ChatGPT) |
| Kimi Code / MiniMax Code CLIs | not installed (`where kimi` / `where mcode` empty) |
| DEEPSEEK_API_KEY | none in the environment at the start; the user later supplied an OpenCode Go key in chat, used for V2/V3 together with `DEEPSEEK_BASE_URL=https://opencode.ai/zen/go/v1` (see A/V2) |
| Pre-existing install | Orbital 0.17.0 at `C:\Orbital\`, installed 2026-10-20, 4 projects + 1 OpenCode Go card |
| CI run 39781257878 | overall **failure**: `Backend unit tests` red on macos-14 AND windows-latest (`tests/unit/test_browser_live.py::test_route_is_mounted_on_the_app`); Frontend, Build Windows installer, Build macOS .dmg all green |
| Evidence folder | `C:\Users\qiren\AppData\Local\Temp\claude\evidence-1022\` (screenshots, pytest/tsc/vitest logs, Inno log, keyless Codex rollout, fresh-run daemon.log) |

How the UI was driven: the packaged app serves the SPA on `http://127.0.0.1:8000`; I drove it from the Claude desktop browser pane. Things that need a native OS dialog (folder picker, UAC, SmartScreen, OAuth login) could not be clicked, see SKIPPED lines. No code was changed. The old `%APPDATA%\Orbital` was moved aside for section B and restored afterwards (see Cleanup).

---

## A. Spec 127 — DeepSeek Harness on Windows

### V1 — Install

- PASS — Install button on the dsh card starts a job (`POST /settings/sub-agents/dsh/install` → 202, job `c2673eb8fb6a`); card shows "Installing…"; first run took ~30 s (23:58:40 → 23:59:08).
- PASS — progress lines stream over the WebSocket (`sub_agent_install_progress`, captured on a monitored re-run: `added 81 packages, and audited 82 packages in 2s`, `7 packages are looking for funding`, `1 high severity vulnerability`, … then `sub_agent_install_done` with `version: 0.1.0-rc.6`). Note: the card renders only the latest line and npm emits its lines in one burst at the end, so on screen the card showed "Installing…" for the whole run and the lines were only visible for a moment.
- PASS — `%APPDATA%\Orbital\agents\dsh\.install-complete` exists (`{"version": "0.1.0-rc.6"}`).
- PASS — card flipped to "Installed" without relaunching the app (screenshot `v1-card-installed.jpg`); `GET /settings/sub-agents` → `installed: true`, `binary_path: …\agents\dsh\node_modules\.bin\dsh-acp-demo.cmd`, `install.state: installed`.
- PASS — `%APPDATA%\Orbital\agents\dsh\node_modules\.bin\dsh-acp-demo.cmd` exists (plus the extensionless sh script and `.ps1`).
- PASS — `npm ci --omit=dev` exit 0; `@deepseek-ai/node-addon-landlock-run` and `koffi` are both present under `node_modules` and no npm ERR/optional-dependency failure was emitted (only the audit notice). `@deepseek-ai/dsh-sandbox-windows-acl` resolved as the sandbox package.
- Observation (not a pass criterion) — the Sub-agents "Refresh" button did not notice a removed `.install-complete` + `node_modules` (card kept saying "Installed" while the API already said `not_installed`); a page reload showed the right state.

### V2 — Dispatch (run 01:06–01:12 with the OpenCode key the user supplied)

Setup: the user supplied an OpenCode Go key, not a direct DeepSeek key, so both card fields were used: `DEEPSEEK_API_KEY` = that key and `DEEPSEEK_BASE_URL` = `https://opencode.ai/zen/go/v1` (OpenCode Go's endpoint from `agent_os/config/providers.json`; it serves `deepseek-v4-flash` / `deepseek-v4-pro` under the same ids dsh uses, and `@deepseek-ai/dsh-llm-deepseek` honours `DEEPSEEK_BASE_URL`). Entered through the card UI (two `POST …/dsh/credential` → 200, both fields show "Stored", card badge "API key saved"; screenshot `v2-dsh-card-key-and-baseurl-stored.jpg`). The pin switcher then lists DeepSeek Harness with no "Needs login" (`v2-pin-switcher-dsh-ready.jpg`). Model left at `deepseek-v4-flash`, permission mode `workspace-write`.

- PASS — pinned dsh in a new `smoke1022` session, sent "reply with the single word PONG": reply `PONG` arrived in ~3 s; the session JSONL has the sub-agent rows (`role: system, source: daemon` dispatch marker with `_meta.handle: dsh`, `transcript_path: D:\orbital-1022-smoke\orbital\sub_agents\dsh\ecd2b0f9.jsonl`, then the `sub_agent_terminal` row with `reply: PONG`). Worker transcript in `v2-v3-dsh-worker-transcript.jsonl`.
- FAIL — a stray console window appears: the dispatch spawns `C:\Windows\system32\cmd.exe /c C:\Users\qiren\AppData\Roaming\Orbital\agents\dsh\node_modules\.bin\dsh-acp-demo.cmd --config D:\orbital-1022-smoke\orbital\sub_agents\dsh\cordis-35272219.yml` (PID 7856, parent = Orbital.exe 8988, created 01:07:55) with a **visible** top-level window titled `C:\Windows\system32\cmd.exe`, and that window stays open for as long as the dsh ACP process (`node …\@deepseek-ai\dsh-acp-demo\lib\bin.js`, PID 21788, child of the cmd) lives — it was still on screen after all three turns. The Codex and manager dispatches in section F produced no such window, so this is specific to the `.cmd` shim launch path (no-window creation flags not applied to the cmd.exe wrapper). This is the spec 055 regression the pass criterion names.
- PASS — no `.cmd` / quoting error: the shim resolved and launched node with the `--config` path intact (cmd line above), no spawn/quoting warning in `daemon.log`.
- NOT OBSERVABLE — "daemon.log shows the ACP initialize": no `initialize`/ACP line is logged at INFO level for this transport (only `Skipping ensure_memory_md for … handle=dsh (transport=acp-sdk mode=pipe does not forward system_prompt)`); the initialize is evidenced by the successful turn instead. Log slice: `daemon-v2-v3-dsh-dispatch.log`.
- FAIL (Windows) — during the dispatch the spec-128 recap build failed on a file lock and the dispatch went out **without** the recap / source-log block: `2026-10-23 01:07:56,252 WARNING agent_os.daemon_v2.sub_agent_manager: recap build failed for proj_49b7a416a975/dsh — dispatching without` with `agent_os.utils.file_lock.FileLockError: Session file is locked by another process: D:\orbital-1022-smoke\orbital\sessions\smoke1022_658e817c.jsonl.lock` caused by `msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)` → `PermissionError: [Errno 13]` (traceback: `_build_recap` → `resolve_session_for_read` → `_load_session_from_disk` → `Session.load` → `_file_lock.acquire`). The daemon itself holds that session's lock while dispatching, so on Windows the non-blocking re-acquire from a worker thread fails. Not seen on the Codex dispatches (those were first turns in sessions whose lock was not contended at that moment), so timing-dependent.
- PASS — second turn in the same session ("reply with the single word PING") → `PING`. No new process was spawned and no `resume_unsupported` line appeared: the daemon kept the ACP process from turn 1 alive and reused it, so this run did not exercise the resume path at all (the note expects `resume_unsupported` + a fresh run; here the live process simply took the second prompt).
- Not done — "Record the passing CI run id in `agent_os/agents/assets/dsh/dsh.yaml`": V1 and V2 pass apart from the console-window regression, but the task forbids code changes, so the manifest was left alone. Also note that path does not exist; the manifest is `agent_os/agents/manifests/dsh.yaml`.

### V3 — Sandbox

- PASS — third turn asked dsh to create `inside-1022.txt` in the workspace and `C:\Users\qiren\dsh-escape.txt`. Inside: `D:\orbital-1022-smoke\inside-1022.txt` created with `INSIDE` (verified on disk). Outside: refused, no file created (`C:\Users\qiren\dsh-escape.txt` does not exist); dsh reported `Error: [sandbox: file access denied under workspace-write mode] [sandbox: escalation available — retry … the approval prompt asks the user]` and did not escalate because approval prompts are disabled for the session. So on Windows 10 Home 22H2 (build 19045) the `dsh-sandbox-windows-acl` confinement holds for file writes; the manifest's confinement note stands. (Shell-level escape was not tested.)

### V4 — Fallback card (Node removed from PATH)

Setup: reset the install (removed marker + `node_modules`), quit Orbital, relaunched `C:\Orbital\bin\Orbital.exe` from a shell whose PATH had no `nodejs`, `Roaming\npm` or nvm entries (on Windows the daemon inherits the launcher's PATH; `merge_shell_path_into_environ` is a no-op there). Hand-installed copy for the locate step: `D:\dsh-manual\` (a copy of the V1 install tree).

- PASS — Install fails fast with an actionable preflight message in the card: `Install failed: Installing DeepSeek Harness needs Node.js 20.12+ and npm, but no 'node' was found on the daemon's PATH. Install Node.js and restart Orbital so it picks up your shell PATH.` API: `install.state: failed`.
- FAIL (UI) — the "Installing…" spinner row stays on the card next to the "Install failed:" text indefinitely (still there >60 s later and after Test/Save on the locate card). Screenshot `v4-install-failed-spinner-persists.jpg`.
- PASS — the locate card (copy-prompt + paste box, text "Or point Orbital at a DeepSeek Harness you installed yourself: paste the launcher path (or the YAML an agent returns for it) below.") appears under the failed card. Screenshot `v4-locate-card-under-failed-install.jpg`.
- PASS — pasting `D:\dsh-manual\node_modules\.bin\dsh-acp-demo.cmd` → Test → `Found DeepSeek Harness at D:\dsh-manual\node_modules\.bin\dsh-acp-demo.cmd (no version check for this agent)` (`POST …/locate/test` → 200 ok:true). Screenshot `v4-locate-test-found-cmd.jpg`.
- FAIL — Save of that same path is refused: `PUT /settings/sub-agents/dsh/locate` → **400** `dsh-acp-demo.cmd is a Windows shim script; Orbital cannot run it. Paste the real executable instead (for example dsh-acp-demo.exe, or the file the shim points to).` Screenshot `v4-locate-save-rejects-cmd-shim.jpg`. This contradicts the manifest (`dsh.yaml` auto_detect for windows is the `.cmd`, with the comment that the `.cmd` is the only spawn target) and the V1 install path itself (binary_path is the `.cmd`). Root: `SubAgentConfigStore.validate_override` rejects `_WINDOWS_SHIMS` on Windows for every agent (added for claude, spec 116). The `.ps1` is refused the same way; the extensionless `dsh-acp-demo` (a POSIX sh script) is **accepted** by Save (200) even though CreateProcess cannot run it, so the only thing a user can save on Windows is the one file that will not launch. There is no `.exe` for dsh. Test and Save disagree (Test accepts the `.cmd`, Save rejects it).
  daemon.log: nothing is logged for the 400 (it is an HTTP response only); the only install line is
  `2026-10-23 00:03:12,856 WARNING agent_os.agents.installer: sub-agent install failed for dsh: Installing DeepSeek Harness needs Node.js 20.12+ and npm, but no 'node' was found on the daemon's PATH. Install Node.js and restart Orbital so it picks up your shell PATH.`
- PASS (via the extensionless path, saved through the API) — with an override stored, the card shows the "Location chosen by you" chip plus "Clear", and `override_path` is set. Screenshot `v4-locate-chip-after-override.jpg`. Override cleared afterwards (`DELETE …/locate` → 200).
- SKIPPED — "dispatch works" after locate: no DEEPSEEK_API_KEY (and with Node off PATH the npm shim could not start node anyway).

Restored afterwards: relaunched with normal PATH, `POST …/dsh/install` → reinstalled in ~25 s, card Installed, `binary_path` back to the `.cmd`.

---

## B. Keyless onboarding + session import (fresh `%APPDATA%\Orbital`)

Setup: quit Orbital, renamed `%APPDATA%\Orbital` → `Orbital.pre1022`, relaunched. The first screen is the API-key step (before any project exists), so "create a project, skip the key step" became "skip the key step, then add projects from the import list / API".

1. Keyless first run
   - PASS — "Skip for now — use my existing agents" moves on to "Connect Your Accounts" (browser sign-in, import projects) and then straight into the app; no "add a key" nag or modal appeared anywhere afterwards. The composer footer says `Talking to Codex. Add an API key and Orbital will help manage this project.`
   - PASS — pin switcher labels: Orbital = "API key required", Claude Code = "Needs login", Codex = selected with quota (screenshot `b1-keyless-pin-switcher.jpg`). New keyless projects default the pin to codex.
   - FAIL — "the worker-run onboarding starts": the first message in a new keyless project (`keyless1022`, `D:\orbital-1022-keyless`, "Look through this folder and tell me what's here.") was dispatched to the codex worker, but every shell command the worker tried was rejected by Codex's execution policy, so it could not read the folder and replied "I couldn't inspect the folder because this session's execution policy blocked even a read-only directory listing." The project's autonomy is `hands_off` → Orbital starts Codex with `approvalPolicy: never`, `sandbox: workspace-write` (`codex_transport._POLICY_BY_AUTONOMY`); on this Windows box Codex 0.162.1 then answers each `pwsh.exe -Command …` with `Rejected(… rejected: blocked by policy)` instead of asking. Evidence: `b1-keyless-codex-rollout.jsonl` (custom_tool_call_output: `exec_command failed: CreateProcess { message: "Rejected(\"`\"C:\\Users\\qiren\\AppData\\Local\\Microsoft\\WindowsApps\\pwsh.exe\" -NoProfile -Command 'Get-ChildItem -Force'` rejected: blocked by policy\")" }`). The same Codex worker answered a plain "reply PONG" fine, so dispatch itself works; only command execution is blocked. The user's `~/.codex` has no `config.toml`.
     daemon.log (fresh run) around it — no error is logged, the dispatch completes normally:
     `2026-10-23 00:21:33,947 INFO agent_os.daemon_v2.sub_agent_manager: Skipping ensure_memory_md for project=proj_8f03ab54e987 handle=codex (transport=codex-appserver mode=interactive does not forward system_prompt).`
     `2026-10-23 00:21:34,219 INFO agent_os.daemon_v2.agent_manager: inject_system_message(proj_8f03ab54e987): hydrating session uuid keyless1022_31636a04 from disk for terminal sub-agent event`
     Related startup warning: `WARNING agent_os.agent.transports.codex_transport: CodexTransport: codex 0.162.1 != pinned 0.157.1 — the app-server protocol is version-pinned; re-run \`codex app-server generate-json-schema\` and the codex test suite before trusting th…`

2. Worker setup prompt paths (the prompt the codex worker received, read from its native rollout `~/.codex/sessions/2026/10/23/rollout-2026-10-23T00-21-34-01a1646a-….jsonl`)
   - FAIL — the spec-128 "Previous session logs" block in the dispatch prompt renders the path JSON-encoded, i.e. with doubled backslashes: `- Orbital chat log: "D:\\orbital-1022-keyless\\orbital\\sessions\\keyless1022_31636a04.jsonl"` (`recap.format_source_logs` uses `json.dumps(path)`). That is exactly the `C:\\Users\\…` double-escaping the check forbids.
   - PASS/observation — the AGENTS.md preamble (`# AGENTS.md instructions for D:\orbital-1022-keyless`, `<cwd>D:\orbital-1022-keyless</cwd>`) uses single backslashes. No forward-slash form appears anywhere in the prompt.

3. Claude Code session import
   - PASS — a genuine Claude Code CLI session (created with `claude -p` in `D:\orbital-1022-cc`; the CLI's auth failure still writes `~/.claude/projects/D--orbital-1022-cc/4ec86f12-….jsonl`) is picked up when the folder is added as a project: session `imp_claude-code_4ec86f1296ea4dca` listed with the "Imported" badge, opens as "Imported from Claude Code · Oct 23, 2026" with its user row rendered (screenshot `b3-imported-claude-code-session.jpg`). (Workspaces whose only Claude Code sessions were Orbital-driven — BatchAI, open117 — show no import row, as designed.)
   - BLOCKED — resume: sending a turn → `claude-code failed: Error: 'claude-code' is installed but not signed in — open Settings → Sub-agents` (`WARNING agent_os.api.routes.agents_v2: Pinned dispatch to claude-code failed for proj_e8a262883ee5/imp_claude-code_4ec86f1296ea4dca: Error: 'claude-code' is installed but not signed in — open Settings → Sub-agents`). Signing in needs the interactive OAuth flow, which I cannot complete.
   - PASS — `CLAUDE_CONFIG_DIR`: relaunched Orbital with `CLAUDE_CONFIG_DIR=D:\cc-config-1022` holding `projects\D--orbital-1022-cc2\5ec86f12-….jsonl` (a copy with `cwd` rewritten); adding `D:\orbital-1022-cc2` as a project imported `imp_claude-code_5ec86f1296ea4dca` from that folder, not from `%USERPROFILE%\.claude`.
   - SKIPPED — the resume pre-check (`SDKTransport.resume_source_exists`, which also honours `CLAUDE_CONFIG_DIR`) under the custom folder: the sign-in gate fires before it, see above.

4. Codex session import
   - PASS — the BatchAI Codex rollout (`01a1264e-1dce-7941-908b-1d5c60af212a`) imported as `imp_codex_01a1264e1dce7941`, rows render ("Imported from Codex · Oct 10, 2026", screenshot `b4-imported-codex-session.jpg`).
   - PASS — a new turn ("reply PONG") resumed the thread: reply arrived, the original rollout file `~/.codex/sessions/2026/10/10/rollout-2026-10-10T22-53-36-01a1264e-….jsonl` was appended (mtime 00:19:09, no new rollout file created), and the session's `sub_agent_thread` record carries `session_id 01a1264e-…` + `rollout_path`.

Import scanner, Windows-specific bug found while doing this:
   - FAIL — Codex entries in "Import existing projects" show paths with the extended-length prefix: `\\?\D:\orbital-1022-smoke`, `\\?\C:\Users\qiren\AppData\Roaming\Orbital\scratch`, `\\?\C:\…\Tour Test`. The values come verbatim from Codex's `~/.codex/state_5.sqlite` `threads.cwd` (Rust `canonicalize` output for threads Orbital itself spawned). Consequences: (a) "Add" stores the prefixed string as the project workspace (`projects.json`: `"workspace": "\\\\?\\D:\\orbital-1022-smoke"`); (b) dedupe against the Claude Code entry for the same folder fails, so `scratch` is listed twice (Claude Code row "Already imported", Codex row still "Add"). No daemon.log line; the scanner is `agent_os/onboarding/import_scanner.py` (`SELECT cwd, updated_at FROM threads …`).

---

## C. Spec 126 — imported Codex automations

- SKIPPED — no Codex automation is defined on this machine (`~/.codex/automations` does not exist; no `automation.toml` anywhere under `~/.codex`).

---

## D. Spec 129 — claude-code keeps listening between dispatches

- SKIPPED — needs claude-code pinned and dispatchable; Claude Code is installed but not signed in (OAuth expired) and sign-in is interactive.

---

## E. Specs 131 and 128

- 131 PASS — after a run ends, the memory record turn is not a second reply bubble: the "Found it — one real hit…" reply is the only assistant bubble; the memory write renders as the collapsed `memory update · edit, write` capsule under it (screenshot `f-grep-run-and-memory-capsule.jpg`). Session JSONL has the memory rows as `role: system, source: daemon` / `_kind: memory_record`.
- 131 PASS — with the project default pin set to codex (`PUT /projects/{id}` → `default_pinned_target: codex`), a never-sent new session opened pinned to Codex ("Message codex…", screenshot `e131-pin-switcher-new-session.jpg`); picking Orbital in the switcher cleared the project default (`GET /projects/{id}` → `default_pinned_target: null`).
- 128 SKIPPED (partial) — the full check needs two dispatchable CLI agents (A pinned, then B). Only codex can be dispatched here (claude-code not signed in, dsh has no key). What was observed: A=codex replied PONG in a session, I switched the pin to Orbital and asked what codex last did; Orbital answered by reading the Orbital worker transcript `D:\orbital-1022-smoke\orbital\sub_agents\codex\9bd8ab74.jsonl`. The spec-128 "Previous session logs" block is only built on sub-agent dispatch (`sub_agent_manager.py:1453–1480`), so it did not appear in that manager turn. The block does get built for sub-agent dispatch (seen in B.2 above, with the double-escaping bug), and Codex's native rollout for that session exists (`~/.codex/sessions/2026/10/23/rollout-2026-10-23T00-13-15-01a16463-….jsonl`, cwd `D:\orbital-1022-smoke`), so the native path should be resolvable once a second agent can be pinned.

---

## F. General Windows smoke on this .exe

- PASS — install over the existing v0.17.0: silent `/SILENT /NORESTART /SUPPRESSMSGBOXES /CLOSEAPPLICATIONS` upgrade, exit 0, `Installation process succeeded.`, icacls lockdown and `--setup-sandbox` both exit 0 (`inno-install-1022.log`). `bin\Orbital.exe` replaced (new build time 2026-10-22 12:54). Data migrated: all 4 projects and the OpenCode Go card were still there on first launch; later runs added `smoke1022`.
- SKIPPED — SmartScreen: the installer was started from a script (no Mark-of-the-Web from `gh` download, silent mode), so no SmartScreen dialog was shown; the only dialog was the UAC consent for the elevated relaunch (`PrivilegesRequired=admin`).
- PASS — create a project (`smoke1022`, via `POST /projects` because the New Project dialog's "Choose a folder" is a native picker) and run a grep prompt with the OpenCode Go card: the `grep` tool ran (ripgrep bundled at `C:\Orbital\bin\_internal\agent_os\vendor\rg\rg.exe`) and answered `src/config.py, line 2: NEEDLE_1022_XYZ = 42`.
- PASS — Codex and Claude Code cards detect and show sign-in state correctly: Claude Code "Installed / Not logged in / v2.1.303" (matches `claude auth status`), Codex "Installed / Logged in / codex-cli 0.162.1" (matches `codex login status`).
- PASS — Codex dispatch from a project session (reply "PONG", sub-agent rows in the session JSONL with `_meta.transcript_path`).
- PASS — no stray console window at launch, during the dsh install (npm), on manager dispatch, or on Codex dispatch (visible top-level windows checked each time: only "Orbital"). The one exception is the dsh dispatch itself, reported under A/V2 (visible `cmd.exe` window for the lifetime of the dsh process).
- PASS — zero 400/500 lines and zero ERROR-level lines in `daemon.log` for the whole session, in both the restored log and the fresh-run log. The only client-visible 4xx was the locate-Save 400 in A/V4, which the daemon does not log. The one traceback in the log is the WARNING-level `recap build failed … FileLockError` during the dsh dispatch, reported under A/V2.
- Observation — three `WARNING agent_os.agents.setup_engine: auth probe failed for codex (codex_auth): \`…codex.CMD login status\` -> exit code 1` lines at startup while the card correctly says "Logged in" (probe raced the startup; later probes succeeded).

---

## H. Locate card: paths with spaces / Kimi Code & MiniMax Code

- PASS (bug confirmed) — on the Kimi Code card, pasting the YAML form `command: "C:\Program Files\nodejs\node.exe"` → Test shows `No file at C:\Program.` (screenshot `h-kimi-yaml-path-truncated.jpg`; API `locate/test` → `{"ok":false,"error":"No file at C:\\Program.","binary":"C:\\Program"}`). The unquoted form `command: C:\Program Files\nodejs\node.exe` fails identically.
- Note on the step as written — pasting the bare path `C:\Program Files\nodejs\node.exe` alone on one line does **not** reproduce it: Test says `Found Kimi Code 22.16.0 at C:\Program Files\nodejs\node.exe` (the bare-path branch does not use `_ABS_PATH`; this is the workaround the note describes). Screenshot `h-kimi-bare-path-found.jpg`. Nothing was saved.
- PASS (confirmed) — Kimi Code and MiniMax Code cards show "Not detected" with only Copy / paste box / Test / Save; there is no Install button (screenshot `h-kimi-minimax-no-install-button.jpg`). Only dsh (and pi) have `orbital_install`.

---

## Unit gates (run on this machine, branch tip 2c271fb)

- `python -m pytest tests/unit/ -q` → **15 failed, 5356 passed, 101 skipped** in 10:16 (`pytest-unit-1022.log`). None of the 15 are CLAUDE.md's 3 documented env-fails (those live in `tests/platform/`, which was not part of the requested command). Classification:
  - 12 × `OSError: [WinError 1314]` (no symlink privilege on this Win10 Home account, local-only): `test_attachment_formatter::test_symlink_pointing_outside_workspace_rejected`, `test_browser_manager::TestSignInProfileLock::test_locked_profile_error_mentions_sign_in_window`, `test_browser_manager::TestSignInProfileLock::test_stale_lock_keeps_install_message`, `test_files_v2_preview::TestPreviewRoute::test_symlink_escape_is_refused`, `test_files_v2_reveal::TestRevealRoute::test_symlink_pointing_outside_is_400`, `test_files_v2_write::TestWriteFileContent::test_symlink_escape_rejected`, `test_import_scanner::test_already_imported_matches_through_a_symlink`, `test_multi_root_reads::test_symlink_out_of_secondary_rejected`, `test_scoped_registry::TestWriteScopeGating::test_symlink_escaping_workspace_blocked`, `test_sessions_tool::TestSessionSymlinkSecurity::{test_external_sessions_root_is_never_authorized_or_read, test_entry_symlink_escaping_sessions_root_is_excluded_and_unreadable, test_worker_alias_symlink_cannot_hide_resolved_worker_identity}`.
  - 2 × known local baseline on this box: `test_ledger_incremental_101::test_same_size_rewrite_is_detected` (`assert 111 == 222`, coarse mtime tick), `test_subagent_transcript_endpoint::test_400_for_path_traversal_shaped_handle` (`assert 200 in (400, 404)`).
  - 1 × **new, not in any baseline**: `test_memory_editor_trigger::test_over_budget_waits_for_a_change_and_an_old_enough_watermark` — `assert lp._schedule_over_budget_edit(["state"]) is True` → `False` (tests/unit/test_memory_editor_trigger.py:181). Not red in CI's run (CI's only backend red is `test_browser_live::test_route_is_mounted_on_the_app`, which passed here), so it looks timing/mtime-related on Windows; needs a look.
- `cd web && npx tsc -b` → exit 0, no output (zero errors).
- `cd web && npm test` (vitest, `--run`) → 148 files, **2253 passed**, 0 failed.

---

## Fixes applied after this run (second commit on the branch)

Each fix has a unit test that fails on the old code; the daemon-level checks ran on an isolated dev daemon (`scripts/dev_daemon_isolated.py`, port 8331, scratch data dir) built from the fixed tree.

- H — `agent_os/agents/locate_answer.py`: a `command:` / `target:` value is now the whole path. Quoted values are taken verbatim; an unquoted value that starts with an absolute path runs to the first ` ->`/`→`/` (note)` or the end of the line, with `Program Files (x86)\…` kept intact. Tests: `TestWindowsPathsWithSpaces` (10 cases). Dev daemon: `command: "C:\Program Files\nodejs\node.exe"` and the unquoted form both Test as `Found … v22.16.0`.
- A/V4 Save — `sub_agent_config_store.validate_override(allow_shim=…)` + `PUT …/locate`: an agent whose manifest knows only shim launchers on Windows (dsh, and also codex/pi/kimi/minimax whose `auto_detect.windows` is `…\npm\<cli>.cmd`) may be pointed at a `.cmd`; claude-code (which also ships `claude.exe`) keeps the spec-116 refusal. Dev daemon: `PUT …/dsh/locate` with `D:\dsh-manual\…\dsh-acp-demo.cmd` → 200, `binary_path` set.
- A/V4 dispatch after locate (found while verifying) — `sub_agent_manager._render_spawn_composition` + `installer.bundled_asset_path`: with a located launcher the install dir has no staged `cordis.template.yml` and the dispatch died with "Composition template not readable"; the renderer now falls back to the template bundled with the build. Tests: `test_composition_template_fallback.py`. Dev daemon: the dispatch spawned and ran (it then failed on auth only because the dev daemon had no base URL for the OpenCode key).
- A/V4 spinner — `SubAgentSettings.tsx`: a job whose `sub_agent_install_failed`/`_done` arrived before the POST's 202 resolved no longer re-enters the installing state, and a refetched entry in `failed` state clears it. Test: "does not re-enter the installing state when the job fails before the POST resolves".
- A/V2 console window — `acp_sdk_transport._spawn_agent_process_no_window`: the transport spawns the ACP agent itself with `creationflags=win_no_window_flags()` (the SDK's `spawn_agent_process` has no hook), mirroring the SDK's shutdown sequence. Tests: `test_acp_spawn_no_window.py` (+ the repo-wide `test_no_console_window` guard). Dev daemon: the dsh dispatch's `cmd.exe` (pid 6720, child of the daemon) and its `node` child have `MainWindowHandle = 0`; no console in the visible-window list.
- A/V2 recap file lock — `agent_manager._load_session_with_lock_retry`: disk reads of a session (recap build, hydrate-on-inject) retry a `FileLockError` up to 20 × 50 ms before giving up. Tests: `test_session_load_lock_retry.py`. Dev daemon: no "recap build failed" on the dispatch.
- B.2 double-escaped path — `recap._portable_path`: the "Previous session logs" block renders drive and UNC paths with forward slashes before JSON-quoting them (`"D:/orbital-1022/orbital/sessions/k.jsonl"`); POSIX paths are unchanged. Tests in `TestFormatSourceLogs`.
- B import `\\?\` prefix — `import_scanner.strip_extended_prefix`: applied to every Codex-sourced cwd (sqlite index and session_meta fallback) and inside `_path_key`, so candidates show and store the plain path and dedupe against the Claude Code entry for the same folder. Tests: `test_strip_extended_prefix_shapes`, `test_codex_extended_prefix_cwd_is_normalised_and_deduped`. Dev daemon: `GET /onboarding/importable-projects` lists `D:\orbital-1022-smoke` / `D:\orbital-1022-keyless` without the prefix.
- Unit gate — `memory_editor`: the cleanup marker now also records each live file's byte size, so a rewrite inside one mtime tick (Windows NTFS) still counts as a delta. `test_over_budget_waits_for_a_change_and_an_old_enough_watermark` passes here now; `test_write_marker_records_watermark_and_preserves_it` updated for the four `*_size` keys. Older markers without size keys fall back to mtimes.

Full gates after the fixes: `npx tsc -b` clean; Vitest 148 files / 2254 tests green; `pytest tests/unit/` → 14 failed / 5381 passed / 101 skipped. The reds are the 12 symlink-privilege env-fails, the path-traversal transcript endpoint baseline, and one new environmental red: `test_dsh_installer::TestDeclaredCredentials::test_dsh_declares_both_credentials_with_state` reads the real OS keychain and now sees the DEEPSEEK_API_KEY stored on the dsh card during V2 (`configured is True`); it fails identically on the unchanged tree and passes once that key is removed from the card. The ledger and memory-trigger reds from the first run are gone.

Not fixed, needs a product decision:
- B.1 keyless worker-run onboarding on Windows: Codex 0.162.1 under `approvalPolicy: never` (hands-off autonomy) rejects every `pwsh.exe` command with "blocked by policy", so the worker cannot read the folder. Changing the autonomy→policy mapping or Codex's Windows sandbox feature is a behaviour change beyond a bug fix; left as reported.
- The Sub-agents "Refresh" button not noticing a removed install marker (cosmetic; a reload shows the right state).

## Cleanup / state left behind

- Orbital data: `%APPDATA%\Orbital` is the original data again (restored; 5 projects, dsh installed). `%APPDATA%\Orbital.fresh1022-leftover\` is the keyless-run data dir, kept for evidence — delete when done. (During the restore the `rm` of the fresh dir failed while the daemon still held it and the original dir was briefly moved *inside* it; nothing was lost, it was moved back out.)
- Test folders you may delete: `D:\orbital-1022-smoke` (project `smoke1022` is in the real registry — remove it from Orbital first if unwanted; it now also holds `inside-1022.txt` from V3 and the dsh worker transcript/sessions under `orbital\sub_agents\dsh\`), `D:\orbital-1022-keyless`, `D:\orbital-1022-cc`, `D:\orbital-1022-cc2`, `D:\cc-config-1022`, `D:\dsh-manual`.
- The OpenCode key and the base URL are stored on the dsh card (OS keychain). Use the card's "Remove" next to each "Stored" badge if you do not want them kept; dsh is left installed.
- The dsh ACP process from the V2 session (`cmd.exe` 7856 + `node` 21788, with the visible console window) is still running under Orbital; it goes away when Orbital exits or the session's worker is stopped.
- Claude Code / Codex side effects: `~/.claude/projects/D--orbital-1022-cc/` (from the `claude -p` attempt); one new Codex rollout each for `D:\orbital-1022-smoke` and `D:\orbital-1022-keyless`, and the BatchAI rollout `01a1264e-…` got two extra turns (PONG + memory).
- Downloaded installer: `C:\Users\qiren\AppData\Local\Temp\claude\dl-batch1022-1792683519\Orbital-Setup-0.17.0.exe`.
- The i18n language dropdown was switched to English in the browser pane's own localStorage only (per-device), not in the desktop app's webview.
