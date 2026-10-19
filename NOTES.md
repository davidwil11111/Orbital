> Verification notes for this branch only. Delete this file before merging to main.

# Batch 1018 verification installer — specs 113, 114, 115, 116, 117

Branch `batch/1018-specs-113-117` @ **ecdd9e5** (off main bc206f7 = v0.17.0 RC + README). CI run
**39095677033** (`gh workflow run ci.yml --ref batch/1018-specs-113-117`, fourth dispatch; runs 39093722715, 39094475435 and 39095150875 had Windows-only reds in the batch's new tests (PATH separator and POSIX-shell assumptions), installers green each time).
The binary reports **0.17.0** (no bump on the batch). NOT merged to main. Specs: `BACKLOG/specs/11[3-7]-*.md`.

Artifacts (macOS drop: ~/Desktop/orbital-test-installers/batch-1018-ecdd9e5/ on the Mac; sha256 in its `SHA256SUMS`):
- `Orbital-0.17.0-macOS.dmg` — 411,783,072 bytes, sha256 c3f2603b…cc43; Gatekeeper: accepted, source=Notarized Developer ID (Qiren Zhou, UG8R3KRXSU); ticket stapled to the DMG (`stapler validate` ok on the dmg, not on the inner .app, same as the v0.16.0 drop); app CDHash 59683faa48fa90221e081f0ec01fa0c3b6accf71 (use it to confirm what is installed).
- `Orbital-Setup-0.17.0.exe` — 411,186,403 bytes, sha256 1f560861…cf27d (unsigned PE, as always).

Gates on the merged tree (local, 2026-10-18): pytest 5143 passed / 81 skipped (two sandbox files deselected as
always); tsc 0; vitest 2117 passed; `npm run build` ok; check-i18n 7 pre-existing zh warnings. CI: expect only
the known `test_browser_live::test_route_is_mounted_on_the_app` red on both backend jobs; installer jobs green.

## Before you install
- **Telemetry server is deployed** (2026-10-18, Railway deployment f935cd4d, orbital-telemetry @ f320763).
  `POST /feedback` is live: no token → 401, bad body → 422. Global Settings → Send feedback should succeed;
  each successful send stores one row in the production `feedback` table.
- Upgrading over /Applications/Orbital.app: your existing projects will show **安全模式 = 开** (absent key = on,
  by decision) and the one-time banner. Quit the app first if an agent is mid-turn.

## What to check by hand (macOS, this build)

1. **安全模式 switch (117 D2).** New project dialog: the switch is on the visible form above 高级选项, default
   off, hover and (i) show the copy. Create the project; Project Settings shows the same row off, and the
   Folder-access / Network sections are hidden.
2. **Open project (117 D3).** In that project ask the agent to run `ls ~/.ssh` and `git status`: both succeed, no
   `[sandbox]` text, no `sandbox-exec` in Activity Monitor during the command. Ask it to grep an absolute path
   outside the workspace: matches come back.
3. **Safe mode on, ask-on-denial (117 D4/D5).** Flip the switch on, autonomy 需确认. Same `ls ~/.ssh` → the
   `[sandbox]` line, then the approval card with the command and three buttons. 允许这一次 runs it and the
   result lands in the chat. 本项目始终允许 ls → next `ls ~/.ssh` runs without a card; Settings → 沙盒外运行
   lists `ls` with Remove. 关闭本项目的安全模式 flips the switch off.
4. **Hands-off never hangs (117 D6).** Autonomy 放手, safe mode on, same command: after 3 min the chat says it
   was denied for this run; Settings → 沙盒外运行 shows the parked request; answering it there runs it.
5. **Feedback (113 D).** Global Settings → 发送反馈: preview loads with a size line, keys/emails/paths
   redacted, Send works once the server is deployed. Also: a failed sub-agent login notice shows the 发送诊断日志
   link.
6. **Upgrade banner (117 D10).** With a pre-existing project the sidebar shows the one-time notice; dismiss
   survives a relaunch.
7. **Claude Code floor (116).** Settings → 子Agent: Claude Code card shows v2.1.302, no "needs update". To see
   the state: `npm i -g @anthropic-ai/claude-code@2.1.100` (old layout) in a terminal, click 刷新 → card shows
   the "过旧" notice + 更新 button, no Login button; picker row says 需要更新; a pinned send writes the
   translated "需要更新" row. Click 更新 → progress streams, card flips to the new version. Then `npm i -g
   @anthropic-ai/claude-code@latest` to be safe.
8. **Detection (115).** Settings → 子Agent lists bring-your-own agents you do not have as compact "未检测到"
   rows. Install a CLI under nvm (or put a shim only on a `.zshrc` PATH), click 刷新 without relaunching → it
   appears installed.
9. **Close button (115 O2).** Red close button: window disappears, NOT into the Dock's minimized zone; Dock
   icon keeps its dot. Dock click → window returns with live state (a streaming session keeps streaming).
   Cmd+Tab back → returns. Yellow minimize then Dock click → AppKit restores it. Cmd+Q quits. Watch for a
   stale-layer blink on reshow.
10. **Read-only grant above the workspace (117 D8).** Safe mode on; `request_access` read-only on the
    workspace's parent folder; approve; writes inside the workspace still work; the Folder-access list has
    Remove.

## Windows bench (needs the .exe on a Windows machine)
Get the installer from the CI run (the .exe is not in git), then check its hash matches the macOS drop:
```
gh run download 39095677033 -R zqiren/Orbital -n Orbital-Windows-installer -D orbital-1018
certutil -hashfile orbital-1018\Orbital-Setup-0.17.0.exe SHA256
# expect 1f56086101a7ce532ff51a3f24fd264036fda6e628c790e02f42209b8cecf27d
```
- 117: items 1–4 above; `dir %USERPROFILE%\Desktop` succeeds in an open project; no `AgentOS-Worker` process
  during the command. Safe mode on: an access-denied path raises the card.
- 114 (safe mode on): fresh install + Git for Windows, project on a repo you own: `git status` and `git log -1`
  succeed first try; elevated `icacls <repo>\.git\config` shows `AgentOS-Worker:(DENY)(WD,AD,WEA,WA)`. Upgrade
  path: install v0.16.0 first, confirm `git status` fails, upgrade, one command → daemon.log has "Rewriting
  stale W3 deny ACE" and git works. `git remote add` and writing `.git\hooks\pre-commit` as the worker still
  fail. In a worker shell `set GIT_CONFIG_COUNT` shows 1 / safe.directory / *.
- 116: npm-installed Claude Code 2.1.100 → "需要更新" + 更新 button → after update a pinned send answers.
  With `--ignore-scripts` (stub layout) the update failure notice names the manual npm command.

## Windows bench results (2026-10-19, Win10 Home zh-CN 19045, done; no further Windows access)
Full report: `docs/verification/1018-windows-bench-2026-10-19.md`. Ran the CI installer above (hash matched)
as an upgrade over v0.16.0, found 9 issues, fixed them on this branch (`a6fd51b`, `041b18d`, `d7a976c`) and
re-verified **from source** on the same machine (repo code against the installed app's data dir). The
fixed build's CI run is **39192198023** (`d7a976c`): frontend green; backend red only on the known
`test_browser_live::test_route_is_mounted_on_the_app` (windows-latest 4930 passed, macos-14 5033 passed);
both installer jobs green. Nobody has installed or run its `.exe` or `.dmg` yet; artifacts:
`gh run download 39192198023 -R zqiren/Orbital -n Orbital-Windows-installer` / `-n Orbital-macOS-dmg`.

What the 0.17.0 installer showed (before the fixes):
- 114 core works: v0.16.0 `git status` as the worker fails; after the upgrade it succeeds; GIT_CONFIG_COUNT
  set. But **`git remote add` / `git config --local` SUCCEEDED as the worker** (git renames `config.lock`
  over `config`; the worker had delete-child on `.git`), so W3 did not protect config at all.
- The NOTES expectation `(DENY)(WD,AD,WEA,WA)` is wrong for Win10: icacls prints both 0x116 and 0x100116 as
  `(W)`, so the text classifier saw "stale" forever and re-applied the deny on every command.
- 117: switch/settings/banner fine; but the Windows denial detector matched only cmd's "Access is
  denied.", not PowerShell's "Access to the path … is denied." (most denials raised no card), and in 需确认
  an approved command never raised the card (approve() skipped the check). Hands-off D6 3-min parking OK.
- 116: 过旧 notice / 需要更新 / translated row OK; **更新 left Claude Code uninstalled** (`claude update`
  died mid-npm on a slow network) while the card still said installed.

What the fixes change (shared code, so **macOS behaviour changes too** — re-check on the Mac):
- `ShellTool.executable_of` (the "always allow" key): unwraps `cmd /c`, `powershell -Command`, `bash -c`;
  returns "" for chained/piped/sub-expression commands (`;` `&` `|` `( )` `{ }` `$(` backtick, newline), so
  those never run unsandboxed via an allowlisted program and the card hides the "always" button
  (backend now sends `executable`). On the Mac: `ls ~/.ssh` still keys as `ls`; `git log | head` no
  longer matches an allowlisted `git`.
- `[sandbox]` hint text reworded (no "credential stores"; says the sandbox, not the file, blocked it);
  `[focus]` warning and the FOCUS prompt rule only apply in safe mode; the `[Unsandboxed run]` row is
  worded as the user's own decision. Mac item 2 (open project) should now be easier: before this, the
  model (deepseek) refused every out-of-workspace command even with safe mode off.
- `approve()` raises the sandbox card for an approved command that then hits the sandbox (Mac item 3 in
  需确认 goes: shell approval card, then the sandbox card).
- `update_autonomy` now updates every live session of the project (one was left hands-off).
- Claude Code 更新 on an npm install runs `npm install -g @anthropic-ai/claude-code@latest` (15-min
  budget) instead of `claude update`; every update re-probes; a vanished CLI ends as
  `cli_missing_after_update` with the manual command. Native installs still use `claude update`.
  Mac item 7: expect npm, not `claude update`, in the progress lines.
- A dispatch failing on auth ("OAuth session expired") shows 登录已过期 + Login on the card. No auth
  probes for agents that are not installed.
- UI: card rehydrates after Settings round-trip; pinned-send failure row arrives live; release build no
  longer logs every WS frame; 监督模式 copy changed (no 安全模式); not-detected PATH hint is per-OS.
- Windows only: W3 denies delete/change-permissions/take-ownership on config+hooks and
  delete-child/change-permissions/take-ownership on `.git` and the repo root (this folder only); writable
  roots get user + OWNER RIGHTS inheritable entries; classifier reads ACE masks via Win32
  (`platform/windows/acl.py`); commands run from a UTF-8 `.ps1` in `orbital/.tmp` (fixes lost double quotes,
  the 1024-char cap) whose trailer prints `[orbital:access-denied]` for any permission error record.

Verified live from source on Windows: worker `git add`+`commit` OK; remote add / config --local / renaming
`.git\hooks` or `.git` denied; one-time stale migration then no rewrites; PowerShell denials and exit-0
compound commands raise the card; 需确认 approve → card; open project runs out-of-workspace commands as the
user; card label "本项目始终允许 echo" for `cmd /c "echo …"` and no "always" for a chained command;
`Write-Output "a b"` → `a b`; card survives Settings round-trip; failure row live; console quiet.

**Still needs a Windows machine (cannot be done from the Mac):**
1. Install the `.exe` from run 39192198023 over 0.17.0 (UAC): startup log shows one "Rewriting stale W3
   deny ACE … (config)" per repo, then none; `icacls <repo>\.git\config` now reads
   `AgentOS-Worker:(DENY)(W,D,WDAC,WO)` (that is the new mask; `(W)` alone is the old one).
2. Update success path: npm on the bench machine died ~34 s into unpacking `claude-code-win32-x64` from any
   shell (rc 127, no output, not Orbital), so only the failure path was seen (it reported
   `cli_missing_after_update` correctly). Needs a Windows box where npm works.
3. 登录已过期 card (unit-tested only); fresh install on a clean machine; Git ≥ 2.35.2 "dubious ownership"
   (bench had Git 2.35.1, so `safe.directory=*` was set but not exercised).

Known leftovers: the workspace-grant icacls `/C` success check matches en+zh summary lines only; telemetry
buckets `cli_missing_after_update` as `unknown` (a new reason changes the server schema); commit `041b18d`
alone does not type-check (an i18n key moves in `d7a976c`). Bench machine state: test projects `bench114`
/ `open117` under `D:\orbital-1018-bench`, the installed app is still 0.17.0 (it will put the old deny
shape back on those two test repos), global npm Claude Code restored to 2.1.100 from npm's backup.

## Review follow-up (2026-10-19, Mac) — CURRENT INSTALLER: run 39208656981 @ 3843884
Supersedes 39192198023 (never installed). Commits on top of the bench fixes:
- `357ce1d` "Always allow X" is keyed on the program that actually runs: `./evil/ls`, `PATH=./evil:$PATH ls`
  and `./evil/bash -c ls` no longer count as `ls` (the agent could run its own program as the user, no
  card). Plain `ls`/`git`, `FOO=1 git push`, and a full path that is PATH's program key as before.
  Windows: a command run as the user reads its `.ps1` from the user's %TEMP%, not the worker-writable
  `orbital\.tmp` (a background worker process could swap it before PowerShell read it).
- `bffe826` a top-level `.git/commondir` (redirects where git reads config; planted, it made the user's
  `git status` run the agent's program) is denied on macOS and deleted by the per-command W3 pass on Windows.
- `3843884` tests only (Windows path handling in the new tests).

Mac daemon smoke (safe mode, `ls` allowlisted): `ls ~/.ssh` ran as the user, no card; `./evil/ls ~/.ssh`
sandboxed + card keyed `./evil/ls`; `PATH=./evil:$PATH ls ~/.ssh` sandboxed + card without "always";
`echo … > .git/commondir` denied, `git add/commit/log` in the same session fine. Local pytest 5275 passed.
CI 39208656981: Windows backend only the known test_browser_live red; macOS also
`test_event_loop_stalls_101::test_hydrate_on_inject_loads_off_the_loop` (0.33–0.43 s vs 0.3 s, 3 runs in a row
since 357ce1d, never before; same runner image; locally 0.005–0.011 s on both old and new code — unexplained).

Artifacts (Mac drop ~/Desktop/orbital-test-installers/batch-1018-3843884/, SHA256SUMS there):
- `Orbital-0.17.0-macOS.dmg` sha256 68800a0d…5ed5; app accepted, source=Notarized Developer ID; ticket
  stapled to the DMG; app CDHash 3b1ca2fb4893323178b68a253070b889ab8d207b.
- `Orbital-Setup-0.17.0.exe` sha256 e217042e…db93:
  `gh run download 39208656981 -R zqiren/Orbital -n Orbital-Windows-installer -D orbital-1018`

Windows checks for this build, on top of "Still needs a Windows machine" above: an always-allowed command
still runs as the user (approved script now in %TEMP%); `.\evil\x.exe` gets its own "always" label;
`echo x > .git\commondir` as the worker → the file is gone after the command and daemon.log says
"Removed a planted …commondir".

## Windows bench of run 39208656981 (2026-10-20, same Win10 machine)
Full report: `docs/verification/1018-windows-bench-2026-10-20.md`. Installed over 0.17.0. Pass: W3 stale
rewrite once per repo at startup then none (also none on the next start); config deny `(W,D,WDAC,WO)`
(`0xD0116`); worker git status/log OK, remote add denied; planted `.git\commondir` removed + logged;
allowlisted `whoami` runs as the user from `%TEMP%\orbital_cmd_*.ps1`; `.\evil\whoami.exe` stays
sandboxed; `.\evil\icacls.exe` card parks with `executable: .\evil\icacls.exe`; open project runs as
the user (fresh session). Not verifiable here: npm update success (machine's npm 9.4.1 dies silently
on the 2.1.303 tarball from any shell; npm 10.9.2 works), 登录已过期 (credentials gone, not expired),
dubious ownership (Git 2.35.1). New, pre-existing on main: all non-ASCII shell output becomes `?`
(PowerShell writes the OEM code page into the redirected file). Fixed: UTF-8 OutputEncoding preamble in
the `.ps1` + UTF-8 decode on the run-as-user path; verified live from source in both project kinds.

## Known / not verified
- Linux auto_detect additions (115) unverified. The spec-116 "finding 7" (why a 2.1.302 probe failed on the
  reporter's machine) is still open; the new auth-probe WARNING in daemon.log is the instrument.
- OpenCode Go key in the dev rig is expired; smokes ran on OpenRouter free models.
