# Windows test results — batch 1026 (specs 132, 134 A+B, 136)

Tested 2026-10-27 02:10 → 03:50 (local time, UTC+8) against `note.md` on this branch.

| Item | Value |
|---|---|
| Branch / code under test | `batch/1026-specs-132-134-136` @ `7f905ba` (+ this pass's fixes, see "Fixes") |
| Windows | Windows 10 Home China 22H2, build 19045 |
| Python / Node | Python 3.13.2 (python.org, per-user), Node v22 (`C:\Program Files\nodejs`) |
| Agent CLIs installed | claude-code 2.1.303 (**not signed in**), codex-cli 0.162.1 (signed in), dsh (a manual install at `D:\dsh-manual` whose ACP handshake fails; not this batch's install). No gemini / cursor / others. |
| LLM | the existing OpenCode Go card (`deepseek-v4.1-flash`) |
| Source runs | daemon from source (`create_app(data_dir=C:\Users\qiren\orbital-test-1026)`, `AGENT_OS_SPA_DIR=web\dist`), UI driven from the Claude desktop browser pane, API via a small script |
| Local frozen build | `bash scripts/build-desktop.sh` (PyInstaller + Inno) at this branch **with the fixes below**. The installer needs admin (UAC), so the packaged items ran the frozen bundle `dist\Orbital\Orbital.exe` directly, launched through `explorer.exe` like a double-click. Ad-hoc local build, **not** the CI artifact — a CI `.exe` check is still owed (CLAUDE.md cardinal rule). |

Which items ran where: **source** — 0, A1–A6, B, C, D0–D3, D5 (source variant), D4 (source variant), D6–D9. **Local frozen bundle** — A7.1, A7.2, A7.3, A7.5.

## Fixes made in this pass (each its own commit, each with a regression test)

| Commit | What | Found by |
|---|---|---|
| `974f6e4` | `bg.cmd` launcher: a command that ran `exit`/`exit /b N` never got an `exit` file (the recorder was the same `cmd /v:on` line); `/v:on` ate every `!`; and on this machine the WMI-created `cmd.exe` cannot start a nested `cmd.exe`/`powershell.exe` ("Access is denied" — python, ping start fine), so `powershell …`, `cmd /c …` and npm's own nested cmd failed inside a launcher job. WMI now starts `conhost.exe cmd.exe /c bg-run.cmd <job dir>` (hidden); `bg-run.cmd` reads the command from the job's `cmd` file and runs it in a child `cmd.exe`, parsed once, and records the child's exit code. | 0 (`test_jobs_windows.py::test_the_bg_cmd_launcher_records_output_and_exit_code` red), D9 |
| `ad08f0b` | macOS desktop-app discovery joined bundle paths with the host's `os.path`, so on a Windows host `/Users/u/X.app` + `Contents/…` got a `\`. Now `posixpath`. Test-only: a HOME-only `expanduser` test now also sets `USERPROFILE`; the picked-`.app` test compares normalized paths. | 0 (4× `test_desktop_apps`, 2× `test_agent_sources` red — would also be red on CI windows-latest) |
| `0971dd7` | `test_cold_model_probes_do_not_hold_the_list` was flaky on Windows (3/5 red, 0.50–0.52 s vs a 0.5 s budget): the fixture stubbed `check_agent` but still ran the real ACP `which` + check-command lookups (~0.4 s cold on Windows). Test-only; 6/6 green after. | 0 |
| `f38887b` | A failed Windows command reported exit **1** whatever the program returned (the `.ps1` trailer copied `powershell -Command`), foreground and background. Now `$LASTEXITCODE` when a native program failed, else 1. The live parity case `cmd /c exit 5` now expects 5. **Behaviour change** — revert this commit if exit-1-always was intended. | D2 (`SystemExit(7)` reported as "failed: exit 1") |
| `3eb4847` | A launcher job gets the account's *default* environment from WMI, so a safe-mode job (worker account) could not find the per-user python on the sandboxed shell's PATH. `bg.ps1` saves the caller's `PATH`; `bg-run.cmd` restores it. Also makes Codex/Pi jobs see Codex's PATH (the D7 "known limit"). | D6 (`'python' is not recognized`) |
| `db688f7` | In safe mode the manager's `shell(background=true)` handed its command raw to the launcher (cmd.exe), so `Write-Output hi` failed with "'Write-Output' is not recognized" while the same call runs in PowerShell in an open project and in the foreground. It now hands the launcher the same `.ps1` wrapper; `JobRegistry.adopt(command=…)` keeps the agent's command on the card and in the wakes. | D6 |
| `a301e9b` | Test hygiene: `test_desktop_cli.py::TestNormalLaunch::test_no_flag_does_not_run_setup` launched the real desktop app for 5 s against the real data dir, PID file and telemetry. It now runs in a scratch home with telemetry off. Pre-existing (v0.1.0). | Other observations |

---

## 0. Unit tests on Windows

- Python (first run, before fixes): **22 failed, 5855 passed, 233 skipped**. Of the 22: 15 are this machine's known env-fails (13 × `WinError 1314` symlink privilege — no Developer Mode — incl. `test_browser_manager::TestSignInProfileLock` ×2 and `test_setup_engine_sweep_136::test_version_cache_follows_a_repointed_symlink`; plus `test_ledger_incremental_101::test_same_size_rewrite_is_detected` and `test_subagent_transcript_endpoint::test_400_for_path_traversal_shaped_handle`, both red on main here too). The other 7 were real: 1 × `test_jobs_windows` (fixed, `974f6e4`), 4 × `test_desktop_apps` + 2 × `test_agent_sources` (fixed, `ad08f0b`). Plus the flaky `test_cold_model_probes_do_not_hold_the_list` (fixed, `0971dd7`).
- Python (re-run after all fixes, with `APPDATA` pointed at a temp dir): **14 failed, 5869 passed, 233 skipped** in 11m40s. All 14 are the known env-fails (13 × `WinError 1314` + `test_400_for_path_traversal_shaped_handle`); the ledger mtime test passed this time.
- The named Windows-only files: `test_proc_runner_windows.py`, `test_project_store_atomic_134.py`, `test_desktop_quit_jobs.py`, `test_owed_wakes.py`, `test_loop_yield_after_batch.py`, `test_stall_watch_134.py`, `test_tool_executor_134.py`, `tests/platform/test_jobs_windows.py` all **ran and pass** (test_jobs_windows: 6 tests, 3 of them added in this pass). POSIX-only cases in `test_proc_runner.py`, `test_shell_runner_134.py`, `test_desktop_recovery_134.py`, `test_pid_identity_134.py`, `test_jobs_registry.py`, `test_jobs_routes.py`, `test_shell_background.py` skip by design.
- `test_skill_catalog.py`: the 4 symlink cases were **skipped** ("symlink creation not permitted" — no Developer Mode). B1 covered the junction case live.
- Frontend: `npx tsc -b` clean; `npm test` **151 files / 2320 tests passed**; `check-i18n` 1395 keys, 7 missing zh (warning only).

## A. Spec 134 Part A — long shell commands

- **A1 PASS.** Tool call 02:55:50 → result 02:57:50 (**120 s**): "Error: command timed out after 120 seconds and was stopped, together with every process it started. If this is long-running work … run it with `background: true` …" + `started`. No `time.sleep(300)` python left afterwards. During the wait: switched projects and opened Global Settings (instant); `GET /api/v2/projects` 5–6 ms.
- **A2 PASS.** `ok` returned the same second (18:58:17Z); Notepad (PID 5056) still running afterwards (closed by hand).
- **A3 PASS.** (a) The note's `1..3000000 | % {…}` takes longer than 120 s under Windows PowerShell 5.1, so it hit the timeout path: head + `... [truncated 1670157 lines, saved to D:\orbital-1026-open\orbital\output\shell-output\cmd_fltyaej_.log] ...` + tail; file 37 MB. (b) Re-ran with `python -c "[print('progress line', i) for i in range(3000000)]"`: returned in ~3 s, `[truncated 3000001 lines, saved to …cmd_a6w8_c49.log]`, file complete (3,000,000 lines, 67.9 MB). Daemon memory 136.9 → 139.4 MB peak (flat). No freeze. Cosmetic: the count is one higher than the file's lines (`text.split("\n")` counts the empty string after the final newline; pre-existing, not Windows-specific).
- **A4 PASS.** Safe-mode project: the command ran as `AgentOS-Worker` (powershell 11776 → python 15196); result at 120 s with `started`; nothing left under either account; no `AssignProcessToJobObject failed` line in `daemon.log`.
- **A5 PASS (as expected).** Stop at ~20 s sent `POST /cancel` → 200; UI stayed usable. The command kept running until the 120 s timeout (19:12:06Z), then the run freed with `[cancelled by user]`; nothing left. UX note: the header shows "Running / thinking" for those ~100 s after Stop.
- **A6** — nothing froze, so no `hang.log` to attach.
- **A7 (local frozen bundle, see setup)**
  1. **PASS** — End task left `daemon.pid` (21448) + `.meta`; relaunch booted in 3 s (pid 10324), no "already running".
  2. **PASS (partly verified)** — relaunch while pid 3036 ran: the new launch (pid 5204) wrote its launch marker and exited; only 3036 remained, `daemon.pid` unchanged. 3036 owns a visible top-level `Orbital` window; whether it came to the *front* could not be checked — the workstation was locked during this step.
  3. **PASS** — `orbital-stderr.log` keeps earlier lines; one `=== Orbital launch <time> pid <n> ===` per launch (21448, 10324, 3036, 5204 …).
  4. **SKIPPED** — the "not responding" dialog needs an on-screen dialog; the session was locked.
  5. **PASS** — truncated `projects.json` 288 → 144 bytes while quit; launch restored it from `.bak`, kept `projects.json.corrupt-1793043800` (144 bytes), `daemon.log`: `ERROR … projects.json was unreadable (JSONDecodeError: Expecting ':' delimiter …); moved it to …corrupt-1793043800 and restored 1 project(s) from …projects.json.bak`.

## B. Spec 132 — `/` skill picker

- **B1 PASS.** `%USERPROFILE%\.agents\skills\win-test\SKILL.md` + junction `%USERPROFILE%\.claude\skills\win-test` → the catalog lists `win-test` **once** (`source: agents`), `path: C:\Users\qiren\.agents\skills\win-test\SKILL.md` (no `\\?\`, no mixed slashes).
- **B2 PASS.** `/win` → menu row `win-test — Windows catalog probe` → chip; "go" + Enter → the manager read the SKILL.md and replied `WINTEST-OK`; the bubble shows the chip and "go", no `<use_skill>` markup. Pinned to **Codex**: Codex read the file (`Get-Content -LiteralPath 'C:\Users\qiren\.agents\skills\win-test\SKILL.md'`) and replied `WINTEST-OK`.
- **B3 PASS.** `/c/Users/me/a.txt look` opened no menu and sent as text; `C:\Users\me\a.txt` opened no menu.
- **B4 PASS.** `/new` showed no menu row and was sent as a plain message.

## C. Spec 136 — Settings without waiting on detection

Only 3 agent CLIs are installed here (claude-code, codex, dsh), so the 8-CLI contention case could not be reproduced.

- **C1 PASS.** `check_all sweep: 15 agents in 0.93–1.14s (slowest: claude-code 0.69–0.84s)`. Warm `GET /settings/sub-agents` 9.6–9.9 ms; after > 60 s (cache expired) still 9.3–9.9 ms, with a background sweep after it (0.96 s). Global Settings drew the sub-agent cards **230 ms** after the click.
- **C2 PASS.** Refresh: `POST /settings/sub-agents/refresh` 1128 ms; all cards resolved; the only WARNINGs are real state (claude-code signed out; the manual dsh install's handshake fails). Console-window flashes not checked (not observable from here).
- **C3 SKIPPED** — installing a global npm CLI was refused by the session's permission classifier. Needs a manual run.
- **C4 N/A** — Cursor is not installed.

## D. Spec 134 Part B — background jobs

- **D0 PASS** (after fixes) — see section 0. `test_jobs_windows.py` runs (not skips) and passes.
- **D1 PASS.** `Started background job job_a906cd6a (pid 10548) in group grp_20bb85ef … To stop it: taskkill /T /F /PID 10548`; the turn ended at once (header "idle"); card "1 background job" Running + Cancel. Job tree `powershell → conhost → python`, **no visible window**. Woken at 19:20:26Z (start 19:15:26Z) with "exited 0 after 5m" + last 20 lines; card Done.
- **D2 PASS after `f38887b`.** One card, two rows; wake at ~6 s "1 of 2 background jobs did not succeed; 1 still running … failed: exit **7** after 6s", second wake at ~40 s. Before the fix the same run reported "failed: exit 1".
- **D3 PASS.** Cancel → row "Stopped" (已停止); python and PING.EXE gone within 0.6 s; agent woken with "cancelled after 15s".
- **D4** — packaged tray steps **SKIPPED** (tray menu + MessageBox are native UI; session locked). **Source variant PASS**: Ctrl+C on the daemon mid-job → `INFO … jobs: stopped 1 background job(s) on shutdown`, no PING left; after restart the session shows "Background job job_a44121ab (`…`) was stopped because Orbital closed." and the agent did not reply.
- **D5 PASS (source variant).** `taskkill /F /PID <daemon python>` (not the tree) mid-job → the job's python and PING.EXE died with it (kill-on-close Job Object). After restart: "Background job job_b4463e82 (`…`) was stopped because Orbital closed.", no agent turn. Packaged variant not run (the frozen bundle's data dir had no LLM card and copying one in was refused by the permission classifier).
- **D6 PASS after `3eb4847` + `db688f7`.** Before: the safe-mode job failed at once — `'python' is not recognized` (WMI default environment), and a PowerShell command failed with `'Write-Output' is not recognized` (launcher ran it in cmd.exe). After: job `job_86ada9ad` ran as `AgentOS-Worker` (`conhost → cmd → cmd`), output streamed, woken "exited 0 after 31s"; `Write-Output hi; Start-Sleep 2; …; Write-Output bye` ran (exited 0) and the card shows the agent's command. Cancel in safe mode: `POST …/jobs/job_3a0c1bf4/cancel` → 200, state cancelled, PING gone within 1 s. No WMI access error.
- **D7 PASS.** Codex ran `orbital\bin\bg.cmd "ping -n 20 127.0.0.1"` on its own (via `pwsh -Command`); `job.json` has `"agent": "codex"`, `"session_id": "open1026_6e27d901"`; the ping survived Codex's turn end (19:29:41Z) and finished exit 0 after 21 s; Codex was told and replied at 19:30:03Z ("20 packets … 0% loss"). With `3eb4847` the job gets Codex's PATH, so python/node resolve as in Codex.
- **D8 PASS** — the A1 timeout text names `background: true`.
- **D9 PASS.** From a cmd prompt with `ORBITAL_SESSION_ID` set: `bg.cmd "echo a & echo b"` → `a`, `b`, exit 0; `bg.cmd "python -c \"print('x y')\""` → `x y`, exit 0. **Known limit (not fixed):** an `&`/`|` *inside nested quotes* (`"python -c \"print('x & y')\""`) is split by `bg.cmd`'s `%*` forwarding — batch files cannot forward such arguments safely.

## Other observations

- **Claude desktop's AppData virtualization.** Processes started from Claude Code's tools on this machine read/write `%APPDATA%` inside `C:\Users\qiren\AppData\Local\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\` (MSIX), but processes started through `Start-Process`/`explorer.exe` see the real `%APPDATA%`. The user's Orbital data used by earlier sessions is in that LocalCache folder and is intact. A real `%APPDATA%\Orbital` was created at 02:20 by this pass (the first pytest run, launched with `Start-Process`, or the build) and then used by the frozen bundle in A7 — it is throwaway.
- **A test wrote the real desktop data dir (fixed, `a301e9b`).** The real `%APPDATA%\Orbital` appeared at 02:20:03 with `version.json`, `browsers\`, `projects.json` (one fresh Quick Tasks), `settings.json`. Bisected with a fake `APPDATA` to `tests/unit/test_desktop_cli.py::TestNormalLaunch::test_no_flag_does_not_run_setup` (unchanged since v0.1.0): it runs `python -m agent_os.desktop.main` for 5 s with the real environment, so it wrote the desktop data dir, reclaimed the machine-wide `~/orbital/daemon.pid`, and recorded an `app_start` "fresh_install" telemetry event. It now gets a scratch `APPDATA`/`HOME`/`USERPROFILE` and `AGENT_OS_TELEMETRY_DISABLED=1`; verified the probe `APPDATA` stays empty and the PID file is untouched.
- A stale `agent_os\frontend\dist` (March, git-ignored) is the daemon's default SPA dir for source runs, so a source daemon without `AGENT_OS_SPA_DIR` serves an old UI whose Stop button posts to a removed `/stop` route (405). Dev-environment only.

## Cleanup

- Test projects `open1026` (`D:\orbital-1026-open`), `safe1026` (`D:\orbital-1026-safe`) and the scratch data dir `C:\Users\qiren\orbital-test-1026` are left for evidence.
- `win-test` skill + junction removed after B.
- Real `%APPDATA%\Orbital` (throwaway, created by this pass) left in place — delete it if you never run Orbital outside Claude.
