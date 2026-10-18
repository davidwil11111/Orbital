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

## Known / not verified
- Linux auto_detect additions (115) unverified. The spec-116 "finding 7" (why a 2.1.302 probe failed on the
  reporter's machine) is still open; the new auth-probe WARNING in daemon.log is the instrument.
- OpenCode Go key in the dev rig is expired; smokes ran on OpenRouter free models.
