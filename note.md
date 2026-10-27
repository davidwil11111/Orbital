# Windows test note — batch 1026 (specs 132, 134, 136)

**Branch under test:** `origin/batch/1026-specs-132-134-136` (pull it; this note is the branch's last commit,
and the code under test is its parent `7f905ba`). It is main (v0.18.0) + `feat/agent-sources-dsh-desktop`
(1c03bca) + specs 132, 134 (Part A + Part B) and 136.

**No CI build this round** (the CI runner is busy with another session). Test from source:

```bash
git fetch origin && git checkout batch/1026-specs-132-134-136 && git pull
cd web && npm ci && npx tsc -b && cd ..
python -m pip install -e .            # if the venv is not current
```

Items marked **[packaged]** only act in a frozen build. For those, build locally with
`bash scripts/build-desktop.sh` and install `installer/Output/Orbital-Setup-*.exe`. That
build is ad-hoc and not the release artifact (CLAUDE.md cardinal rule), so a CI `.exe`
check is still owed before this ships. Say in the results which items ran on source
and which on the local installer.

Everything below was built and tested on macOS only. The Windows code paths were
unit-tested with fakes. This pass is the first time any of it runs on Windows.

Paths:
- Source runs:
  - daemon log: `<data-dir>\logs\daemon.log`
  - hang log: `<data-dir>\logs\hang.log`
- Packaged:
  - app data: `%APPDATA%\Orbital\`
  - stderr log: `%APPDATA%\Orbital\logs\orbital-stderr.log`
  - PID file: `%USERPROFILE%\orbital\daemon.pid` (+ `daemon.pid.meta`)

Write results to `note-windows-results.md` on this branch (pass/fail per item, with
evidence: log lines, timings, `tasklist` output). Commit and push them. If you fix
something, commit the fix separately, with a regression test, and say so in the results.

---

## 0. Unit tests on Windows (do first)

```bash
python -m pytest tests/unit/ tests/platform/ -q -o faulthandler_timeout=120 \
  --deselect tests/platform/test_e2e_agent_isolation.py \
  --deselect tests/platform/test_macos_provider_integration.py
```

Pass: green apart from the 3 documented env-fails in CLAUDE.md. Report every other
failure with its traceback. These files have Windows-only tests that never ran on the Mac:
- `tests/unit/test_proc_runner_windows.py` (real Job Object, W1 grandchild, survivor)
- `tests/unit/test_proc_runner.py`
- `tests/unit/test_shell_runner_134.py`
- `tests/unit/test_desktop_recovery_134.py`
- `tests/unit/test_pid_identity_134.py`
- `tests/unit/test_project_store_atomic_134.py`
- `tests/unit/test_skill_catalog.py` (symlink cases skip when symlinks aren't allowed; say whether they ran)
- `tests/unit/test_setup_engine_sweep_136.py`
- the background-jobs tests (see section D)

Frontend: `cd web && npm test && node scripts/check-i18n.mjs`.

---

## A. Spec 134 Part A — a long shell command must never freeze Orbital (the user's bug)

The reported symptom, on Windows: the agent starts waiting on a long script, the UI shows
"thinking", the whole app stops responding to clicks, then "crashes" and won't come back.
The root cause found: at the 120 s timeout Python killed only PowerShell, then waited with
no timeout for the grandchild still holding the output pipes. The fix runs every command
in its own process group (Job Object on Windows) with output going to a file, and kills
the whole tree on timeout only.

Set up a project with **safe mode off** (the default for new projects) and a working LLM.

**A1 — W1 repro.** Ask the manager to run exactly:
`Write-Output started; python -c "import time; time.sleep(300)"`
(or `ping -n 400 127.0.0.1` if python isn't on PATH).
Pass:
- The tool result arrives at about **120 s**, not 300 s. It reads "command timed out after
  120 seconds and was stopped, together with every process it started … run it in the
  background (`background: true`) …" and includes `started`.
- `tasklist | findstr /i "python ping"` right after shows **none** from this command.
- The UI stays clickable the whole time: switch sessions and open Settings during the wait.
- While it runs, `curl http://127.0.0.1:<port>/api/v2/projects` answers in well under 1 s.

**A2 — Survivors are kept after a normal exit.** Ask the manager to run:
`Start-Process -WindowStyle Hidden notepad; Write-Output ok`
Pass: the result arrives at once with `ok`, and Notepad is **still running** afterwards.
If Notepad dies, the Job Object got kill-on-close, which is a bug. Close Notepad by hand.

**A3 — Chatty output.** `1..3000000 | % { "progress line $_" }`
Pass:
- The result is a head + `... [truncated N lines, saved to …\orbital\output\shell-output\cmd_….log] ...` + tail.
- That file exists and is complete.
- The Orbital/python process memory stays roughly flat (Task Manager; note before and peak).
- The window never goes "Not Responding".

**A4 — Sandboxed path.** Repeat A1 in a project with **safe mode on** (worker account,
`run_and_capture`).
Pass:
- Same 120 s return.
- No leftover `python.exe` / `ping.exe` under either account.
- If `daemon.log` shows `AssignProcessToJobObject failed; timeouts fall back to the psutil
  tree kill`, record it, and confirm the grandchild still died.

**A5 — Stop during a long call.** Start A1 again and press Stop at about 20 s.
Pass: the UI recovers. Record whether the command is still running until 120 s
(expected: yes, Stop can't interrupt the thread; it frees at the timeout) and confirm
nothing is left after 120 s.

**A6 — Stall detector.** Optional, needs a source run. Not easy to trigger on purpose.
If anything above froze the UI for more than 5 s, check `logs\hang.log` and attach it:
it holds every thread's stack.

**A7 — Recovery [packaged].**
1. With Orbital running, End task in Task Manager, so `daemon.pid` + `.meta` are left behind.
   Relaunch. Pass: it boots, with no "already running" refusal.
2. Relaunch while a live, responsive instance runs. Pass: the existing window comes
   to the front; no second daemon starts.
3. `orbital-stderr.log` keeps the earlier lines and has one `=== Orbital launch … ===`
   marker per launch.
4. Optional: if you can make the UI hang (e.g. suspend the process with Resource Monitor),
   relaunch. Pass: an "Orbital is not responding — End it and start again?" dialog
   (bilingual by OS language). Yes ends it and boots a fresh one.
5. Corrupt `%APPDATA%\Orbital\projects.json` by truncating it to half its size while
   Orbital is quit. Launch. Pass: the projects come back (restored from
   `projects.json.bak`), the broken copy is kept as `projects.json.corrupt-<n>`, and
   `daemon.log` has an ERROR line saying so.

---

## B. Spec 132 — type `/` in the composer to pick a skill

**B1 — Catalog on Windows homes.** Create a skill at
`%USERPROFILE%\.agents\skills\win-test\SKILL.md` with this content:
```
---
name: win-test
description: Windows catalog probe
---
# win-test
Reply with WINTEST-OK.
```
Optionally also a junction `%USERPROFILE%\.claude\skills\win-test` →
`%USERPROFILE%\.agents\skills\win-test`.
Pass:
- `GET /api/v2/projects/<pid>/skill-catalog` lists `win-test` **once**.
- Its `path` is a normal Windows path, with no `\\?\` prefix and no mixed slashes.

**B2 — Pick and send.**
1. Type `/win`. The menu shows `win-test`; pick it.
2. A chip appears. Type "go" and press Enter.
Pass:
- The manager reads the SKILL.md and replies with WINTEST-OK.
- The user bubble shows the chip, not raw `<use_skill …>` markup.
- Repeat pinned to a codex or claude-code worker: same result.

**B3 — Paths start with `/` or a drive.** Type `/c/Users/me/a.txt look` and press Enter.
Pass: it sends. Typing `C:\…` never opens the menu.

**B4 — `/new` is gone.** A typed `/new` is sent as plain text, and no menu row appears for it.

---

## C. Spec 136 — Settings open without waiting on detection

**C1 — Timings.** Have several agent CLIs installed (claude, codex, gemini, cursor, etc.,
as many as the machine has).
1. Open Global Settings → Sub-agents once and wait for it to settle.
2. Wait more than 60 s.
3. Open it again, then open Project Settings.
Pass: both draw their cards in **under 1 s**. Record the timings, and the
`check_all sweep: N agents in X.XXs (slowest: …)` lines from `daemon.log`.

**C2 — Refresh.** Click Refresh. Record its duration. On the Mac it is ~5 s, down from 16 s.
Watch for contention: on Windows, 8 Node CLIs start through `cmd.exe` at once. Pass: no
errors in the log, every card resolves, and no console windows flash.

**C3 — Background correction.**
1. Install or uninstall one CLI from a terminal (e.g. `npm i -g @google/gemini-cli`),
   with Settings open.
2. Wait more than 60 s, then reopen Settings.
Pass: it first shows the old state, then corrects itself within a few seconds without
a reload.

**C4 — Sign-in timeout.** If Cursor is installed and its `agent status` is slow: the card
must never flip to "Login" just because the check timed out. Expected instead: keep the
last state, or show "Verify sign-in in Cursor".

---

## D. Spec 134 Part B — background jobs

What it does:
- The manager's `shell` tool takes `background: true`. The call returns at once with a job id,
  the output file (`orbital\jobs\<job_id>\output.log`) and a stop command
  (`taskkill /T /F /PID <pid>`). The turn ends after that message's tool calls.
- Orbital wakes the agent when every job in the group has ended, or at once when one fails,
  and checks in every 15 minutes while a job runs.
- Codex and Pi workers start jobs with a launcher Orbital writes into the workspace:
  `orbital\bin\bg.cmd` → `bg.ps1`, which starts the job through WMI `Win32_Process.Create`
  so it sits outside the CLI's own Job Object.
- Jobs do not survive quitting: Quit asks first, quitting ends every job, and after a crash
  the next launch ends leftovers and leaves a note in the chat that does not wake the agent.

Nothing in this section has ever run on Windows. Watch Task Manager's **Details** tab
throughout.

**D0 — Tests.** `tests/platform/test_jobs_windows.py` must pass, not skip (real Job Object
cancel of a grandchild, `stop_all` closing the Job Object, `bg.cmd` recording output and
exit code 3). Also `tests/unit/test_jobs_*.py`, `test_shell_background.py`,
`test_owed_wakes.py`, `test_loop_yield_after_batch.py`, `test_desktop_quit_jobs.py`.

**D1 — Open path (safe mode off).** Ask the manager:
"Run as a background job:
`python -c "import time; [print(i, flush=True) or time.sleep(10) for i in range(30)]"`,
and tell me when it ends."
Pass:
- The tool result reads `Started background job job_… (pid N) … To stop it: taskkill /T /F /PID N`.
- The turn ends at once and the UI stays clickable.
- A "1 background job" card shows Running with a Cancel button.
- About 5 min later the agent is woken, reports exit 0, and the card reads Done.
- No console window flashes when the job starts.

**D2 — Two jobs in one message, one failing.** Ask for two background calls in one message:
`python -c "import time; time.sleep(5); raise SystemExit(7)"` and
`python -c "import time; time.sleep(40)"`.
Pass:
- One card with two rows.
- A wake at about 5 s ("1 of 2 … did not succeed; 1 still running", exit 7).
- A second wake at about 40 s.

**D3 — Cancel kills the whole tree.** Start in the background:
`python -c "import subprocess,time; subprocess.Popen(['ping','-n','300','127.0.0.1']); time.sleep(300)"`
Then click Cancel on the card.
Pass:
- The row reads Stopped within about 1 s.
- No leftover `python.exe` or `PING.EXE` from it.
- The agent is woken with "cancelled after …".

**D4 — Quit confirmation [packaged].** Start a 300 s background job (the D3 command).
1. Close the window with ×. Pass: no prompt (it hides to the tray).
2. Tray → Quit. Pass: a prompt "1 background job(s) are still running. Quitting stops them."
   (OK = Quit; MessageBox can't relabel its buttons). Zh Windows shows the zh text.
3. Cancel. Pass: Orbital keeps running **and the tray icon is still there**.
4. Tray → Quit → OK. Pass: Orbital exits and no `python.exe` / `PING.EXE` from the job is left.
5. Relaunch. Pass: the session shows "Background job job_… (`…`) was stopped because
   Orbital closed." and the agent does not reply on its own.
From source there is no tray: instead stop the daemon with Ctrl+C mid-job. Pass: the log says
`stopped 1 background job(s) on shutdown`, the tree is gone, and the same note appears on restart.

**D5 — Killed app.** Start the D3 command in the background. End task on `Orbital.exe` only
(packaged), or `taskkill /F /PID <daemon python pid>` (source); not "End process tree".
Pass:
- The job's `python.exe` and `PING.EXE` die too (the kill-on-close Job Object).
- On relaunch, the same non-waking "stopped because Orbital closed" note appears.
If the job survived the kill, record it, and confirm the relaunch killed it instead.

**D6 — Safe mode on (sandboxed worker account).** Same request as D1, in a safe-mode project.
Pass: "Started background job …" (it goes through `orbital\bin\bg.ps1` → WMI), then the wake
and the card as in D1, and Cancel works.
If it fails with an access error, paste the exact text: WMI `Win32_Process.Create` from the
worker account is the suspect. If Cancel fails, note which account owned the process.

**D7 — Codex worker** (if Codex is set up). In a session pinned to Codex:
"run `ping -n 20 127.0.0.1` in the background the way your Orbital instructions say, then end
your turn; tell me the result when it finishes."
Pass:
- Codex runs `orbital\bin\bg.cmd "…"` on its own.
- `orbital\jobs\job_…\job.json` has `"agent": "codex"` and the session id.
- The ping survives Codex's turn end.
- About 20 s later Codex receives "[Background jobs …] 1 background job finished" and replies.
If the ping dies when Codex's turn ends, report it: Codex's job cleanup reached the WMI process.
Known limit to confirm: the WMI-started job gets the user's default environment, not
Codex's shell. Record whether `python`/`node` resolve inside the job the same as in Codex.

**D8 — Timeout hint.** A foreground `python -c "import time; time.sleep(300)"` returns at
120 s, and the message names `background: true`.

**D9 — Quoting through bg.cmd.** In D7 (or by hand in a terminal, with `ORBITAL_SESSION_ID`
set to a real session id), run
`orbital\bin\bg.cmd "echo a & echo b"` and `orbital\bin\bg.cmd "python -c \"print('x y')\""`.
Pass: `output.log` has `a`, `b` / `x y`, and the `exit` file holds `0`. Record any argument
mangling exactly.
