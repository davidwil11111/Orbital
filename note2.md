# Windows test note 2 — spec 138 (onboarding from your agents' history)

**Branch under test:** `origin/batch/1026-specs-132-134-136`, at the commit that adds this
file. Spec 138 is merged on top of the batch that `note.md` covers; run `note.md` as
written and this note as well. The spec is
`BACKLOG/specs/138-value-first-onboarding-from-agent-history.md` on the Mac (not in git);
everything you need is below.

Test from source, as in `note.md`. Spec 138 has no `[packaged]`-only behaviour.

Everything here was built and tested on macOS only. The Windows-sensitive parts are
untested on Windows:
- reading agent history from Windows paths;
- the Codex `\\?\` prefix;
- drive-letter case and slash direction when matching folders;
- `%TEMP%` / AppData as never-a-project folders;
- the pi resume command line with a native file path.

Write results to `note2-windows-results.md` on this branch, using the same format as
`note.md`'s results: pass/fail per item, with evidence. If you fix something, commit the
fix separately with a regression test.

## What spec 138 does (so you know what "right" looks like)

On first run Orbital reads the user's existing Claude Code, Codex, pi, Kimi Code and
MiniMax Code history (locally, read-only) and onboards from it. The wizard order is:
1. **Agents** — the agents found on this computer, plus a locate card for any it missed.
2. **Projects** — the folders worked in, all checked; 1–2-session folders under
   "Show more", unchecked; new projects grouped from scattered conversations.
3. **Orbital agent key** — skippable; compulsory only when no agent is found.
4. **Accounts.**

After the wizard you land in the most recently active imported project, on a new
session. Its empty chat shows "Last time" and "Didn't finish" cards. Opening one opens
that conversation, and the user can continue it with any agent; the new agent gets the
conversation as a recap.

Imported projects get **no files** (`AGENTS.md`, `orbital\`) until the user opens a
session or sends in them.

## Safety rules for this pass (read before touching anything)

- **Never import a real project folder into a dev daemon.** Use scratch folders under
  e.g. `C:\orbital-scratch\`.
  - Deleting a project deletes its `orbital\` folder.
  - Opening a session in an imported folder writes `AGENTS.md` + `orbital\` into it.
  - Reading the real history in `%USERPROFILE%` is fine; it is read-only.
  - On the Projects step, **uncheck every real folder** and import only scratch folders.
- Run the daemon isolated with a fresh data dir so the wizard shows:
  `python scripts/dev_daemon_isolated.py --port 8338 --data-dir C:\orbital-scratch\data`
  It sets `AGENT_OS_TELEMETRY_DISABLED=1` and an in-memory keyring.
- Then start the frontend against it:
  `cd web && set ORBITAL_DEV_API_PORT=8338 && npx vite --port 5173`
- **`%USERPROFILE%\.orbital\sub_agent_config.json` is shared with the real app.**
  - The locate card's Save and any sub-agent model change land there.
  - Copy the file before you start and restore it at the end. On the Mac this pass
    left a stray pi location and a MiniMax model override in it.
- Keep agent turns tiny ("Reply with the single word PONG"); they bill the user's plans.

Paths (Windows defaults; each agent's own variable wins when set):

| Agent | History | Variable |
|---|---|---|
| Claude Code | `%USERPROFILE%\.claude\projects\<slug>\*.jsonl` | `CLAUDE_CONFIG_DIR` |
| Codex | `%USERPROFILE%\.codex\sessions\**\rollout-*.jsonl` + `state_5.sqlite` | `CODEX_HOME` |
| pi | `%USERPROFILE%\.pi\agent\sessions\--<cwd>--\*.jsonl` | `PI_CODING_AGENT_DIR` |
| Kimi Code | `%USERPROFILE%\.kimi-code\sessions\wd_*\session_*\` | `KIMI_CODE_HOME` |
| MiniMax Code | `%USERPROFILE%\.minimax\v2\sessions\…` + `v2\sqlite\runtime-state.sqlite` | — |

The history cache is `<data-dir>\history\sessions-cache.json`.

---

## 0. Unit tests (do first)

Run the full suite as in `note.md` §0. The spec 138 files that have never run on
Windows:
- `tests/unit/test_history_138.py`
- `tests/unit/test_history_sources_138.py`
- `tests/unit/test_import_scanner.py`
- `tests/unit/test_session_import.py`
- `tests/unit/test_imported_session_entries.py`
- `tests/unit/test_left_off_138.py`
- `tests/unit/test_onboarding_import_138.py`
- `tests/unit/test_deferred_seed_138.py`
- `tests/unit/test_telemetry_138.py`
- `tests/unit/test_pi_rpc_transport.py`
- `tests/unit/test_acp_sdk_transport.py`
- `tests/unit/test_history_scaffold_138.py`

Expect path-shaped failures if any: `/`-only assumptions, `os.path.realpath` on `C:` vs
`c:`, a `tmp_path` under `%TEMP%` being read as a temp root. Report each with its
traceback. Fix test-only assumptions; report product ones.

Frontend: `cd web && npm test && npx tsc -b && node scripts/check-i18n.mjs`.

---

## A. History is found on Windows paths

Set up first. Create a scratch git repo `C:\orbital-scratch\ws-e2e` (`git init`, one
commit), then in it run:
- `claude -p "Remember this code word for later: MANGO. Reply with just OK."`
- `codex exec --skip-git-repo-check "Reply with the word OK and nothing else"`

**A1 — Scan.** Start the isolated daemon, then:
`curl http://127.0.0.1:8338/api/v2/onboarding/history` (repeat until `"status":"ready"`).
Pass:
- `ready` within ~10 s; record the time and `sessions_total`.
- `by_agent` is non-zero for every agent that has history on this machine.
- The status call itself answers in well under 1 s while scanning.

**A2 — Codex `\\?\` prefix and folder identity.** Run
`curl http://127.0.0.1:8338/api/v2/onboarding/importable-projects`.
Pass:
- No `path` starts with `\\?\`.
- `C:\orbital-scratch\ws-e2e` appears **once**, with `by_agent` holding both
  `claude-code` and `codex`, and `is_git: true`.
- No folder appears twice under different case or slash variants.

**A3 — Never-a-project folders.** Pass: no candidate path, and no group
`suggested_folder`, is any of:
- `%USERPROFILE%`, Desktop, Documents or Downloads (including OneDrive-redirected ones);
- `%TEMP%` or anything under it;
- anything under `%APPDATA%` or `%LOCALAPPDATA%`.

The rules now cover these on Windows: AppData counts as temp; Desktop, Documents,
Downloads, the home folder and the drive root count as containers; all comparisons
ignore case. This was fixed on the Mac and is unit-tested with simulated case folding,
but never run on real Windows paths. If one still shows up, report the exact path and
whether it is OneDrive-redirected. Redirected Desktop/Documents folders live outside
`%USERPROFILE%\Desktop` and are NOT on the list yet.

**A4 — `CLAUDE_CONFIG_DIR`.**
1. Copy one `.claude\projects\<slug>` folder into `C:\orbital-scratch\claude-alt\projects\`.
2. Restart the daemon with `CLAUDE_CONFIG_DIR=C:\orbital-scratch\claude-alt` and a fresh
   data dir.

Pass:
- `/onboarding/history` counts only those Claude sessions.
- The imported project's session list shows the same sessions as its left-off cards.
  (These two lookups used to resolve the Claude folder separately. Fixed on the Mac; this
  checks Windows.)

**A5 — Worktree folding.**
1. In ws-e2e run `git worktree add ..\ws-e2e-wt`.
2. In the worktree run `claude -p "Reply with the word OK"`.
3. Restart the daemon with a fresh data dir.

Pass: the worktree's session counts toward `ws-e2e`; there is no separate `ws-e2e-wt`
candidate.

## B. Import writes nothing until first use

**B1 — Import.**
```bash
curl -X POST http://127.0.0.1:8338/api/v2/onboarding/import -H "content-type: application/json" \
  -d "{\"folders\":[\"C:\\\\orbital-scratch\\\\ws-e2e\"],\"groups\":[]}"
```
Pass:
- 200 with one `created` entry.
- `dir /a C:\orbital-scratch\ws-e2e` shows no `AGENTS.md` and no `orbital`.

**B2 — Viewing writes nothing.**
1. Open the project in the UI.
2. Wait 10 s on its new-session screen.
3. Open the Tasks tab and the Files tab.

Pass:
- Still no `AGENTS.md` or `orbital\`. (On the Mac the queue endpoint used to create
  `orbital\queue.json` here; fixed.)
- The message box is usable: no "Pause queue to start chatting".

**B3 — First use seeds.** Click the "Last time" card.
Pass: the conversation opens with its rows, and `AGENTS.md` + `orbital\sessions\imp_*.jsonl`
now exist.

## C. Left-off cards and continuing with another agent (the core of the spec)

**C1 — Cards.** On a new session in the imported project:
- "Last time" shows your MANGO message verbatim, with "You to Claude Code" and a relative
  time.
- The chips show branch `main` and no 0-valued chips.
- The Codex "OK" session is filtered as a probe; it is not a card.

**C2 — Continue with another agent.**
1. Open the MANGO card.
2. Switch the agent to **Codex** in the switcher.
3. Send "What code word did I ask you to remember earlier? Reply with just the word."

Pass: Codex answers **MANGO** in that session.

**C3 — Resume natively.**
1. Switch back to Claude Code.
2. Send "Say the code word once more, just the word."

Pass:
- The reply is MANGO.
- The daemon log shows Claude Code **resumed** the native session, not started fresh.

**C4 — Didn't finish.**
1. In ws-e2e start `claude` interactively, ask for a long essay, and press Esc mid-answer.
2. Restart the daemon (the history cache refreshes on file change, so a restart only
   makes it quick).

Pass: a "Didn't finish" card with **Stopped** shows for it, newest first, with at most 3
cards. Times show in local time.

## D. Wizard UI (vite against the isolated daemon, fresh data dir)

**D1 — Agents step.**
- Every installed supported agent is a checked row.
- Status reads "Signed in · N conversations", "Signed in" (no "0 conversations"), or
  "Installed · not signed in" with Sign in.
- CodeBuddy and Gemini CLI read "Installed · add its key later in Settings".
- Unchecking one and continuing hides it from the switcher of projects created afterwards.

**D2 — Locate a `.cmd` launcher.** Click a "Not found" chip for an agent that IS installed
through npm (its launcher is a `.cmd` shim).
1. The card shows the prompt with Copy.
2. Paste the agent's answer, which names the `.cmd`.
3. Click Test, then Save.

Pass:
- Test passes.
- After Save the agent moves into the found list.
- Restore `%USERPROFILE%\.orbital\sub_agent_config.json` afterwards.

**D3 — Projects step (look only; see the safety rules).**
- Real folders are listed and checked; the minor ones are under "N more folders…".
- Paths render with backslashes and no `\\?\`.
- A group's Change button opens the folder browser.
- **Uncheck every real folder** before pressing Import.

**D4 — Key step.**
- The provider box filters as you type ("kimi" → Moonshot (Kimi)).
- Arrow keys and Enter select; Escape closes.
- "Skip for now — use my existing agents" works.

**D5 — Finish and tour.**
- Finish lands on a new session of the most recently active imported project.
- The tour's first stop spotlights the cards, and a card is clickable during it.
- Clicking one opens the session and the tour moves to the agent-switcher stop.

**D6 — Chinese.** Switch to 简体中文 on step 1 and walk the steps. Pass: no text overflows
its control at the default window size or at a narrow window.

## E. pi, Kimi Code, MiniMax Code (only the ones installed on this machine)

For each installed one:
1. In a scratch folder **whose name has a space** (`C:\orbital-scratch\ws with space`),
   create a native session with one tiny prompt, using the agent's own CLI.
2. Import that folder; the session is listed as an imported row.
3. Open it and send a tiny follow-up pinned to the same agent.

Pass:
- The reply lands in that session.
- The log says the agent **resumed**; for pi, the same session id with the native file
  path passed intact.
- Then put the same session on a project in a different folder (the "members" case):
  a resume or a fresh session with recap, never an error.

pi is the risky one: Orbital passes `--session "<native file>"` (or `--fork` when the
session ran in a different folder). Check the spawned command line in the log keeps the
backslashes and the space intact.
