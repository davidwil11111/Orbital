# Windows test note — batch 1022 (specs 126–131)

**Build under test:** `batch/1022-specs-126-131` @ `dd03041`, CI run 39781257878.
Installer: `~/Desktop/orbital-test-installers/batch1022-dd03041/Orbital-Windows-installer/Orbital-Setup-0.17.0.exe`
(sha256 starts `3e043a7d`). Test this CI-built `.exe` only, never a local build.

Branch is pushed to `origin/batch/1022-specs-126-131`. It contains main (v0.17.0),
the keyless onboarding + session import branch, and all five spec branches.

Windows paths you will need:
- App data: `%APPDATA%\Orbital\`
- Daemon log: `%APPDATA%\Orbital\logs\daemon.log`
- dsh install dir: `%APPDATA%\Orbital\agents\dsh\`

---

## A. Spec 127 — DeepSeek Harness on Windows (the only spec with no Windows pass yet)

Prereq: Node ≥ 20.12 on PATH.

**V1 — Install.** Global Settings → Sub-agents → DeepSeek Harness → Install.
Pass:
- progress lines stream in the card
- `%APPDATA%\Orbital\agents\dsh\.install-complete` exists
- card flips to installed without relaunching the app
- `%APPDATA%\Orbital\agents\dsh\node_modules\.bin\dsh-acp-demo.cmd` exists
- `npm ci` did not error on `node-addon-landlock-run` (linux-only optional) or `koffi`

**V2 — Dispatch.** Put a `DEEPSEEK_API_KEY` on the card, pin dsh in a project session,
send "reply with the single word PONG".
Pass:
- a reply arrives and the sub-agent row is in the session JSONL
- no stray console window pops up
- `daemon.log` shows the ACP initialize and no `.cmd` / quoting error
- send a second turn in the same session: it works as a fresh run
  (`resume_unsupported` is expected on every OS, not a failure)

**V3 — Sandbox.** Ask dsh to write one file inside the workspace and one at
`%USERPROFILE%\dsh-escape.txt`.
Pass: inside succeeds, outside is refused. If the outside write succeeds on this
Windows edition, record the edition; the manifest note must stop claiming confinement.

**V4 — Fallback card.** Remove Node from PATH, relaunch, click Install.
Pass: Install fails with an actionable preflight message, and the locate card
(copy-prompt + paste box) appears under it. Paste the path to a hand-installed
`dsh-acp-demo.cmd`, Test passes, Save works, the card shows the locate chip, dispatch works.

Record the passing CI run id in `agent_os/agents/assets/dsh/dsh.yaml` when V1–V2 pass.

---

## B. Keyless onboarding + session import (Windows-specific fixes in this branch, never run on a real Windows install)

1. **Keyless first run.** Fresh `%APPDATA%\Orbital` (rename the old one). Create a
   project, Skip the API-key step. Pass: the worker-run onboarding starts, the
   pin row says an API key is required where it should, no "add a key" nag after Skip.
2. **Worker setup prompt paths.** During onboarding, open the prompt the worker
   receives. Pass: paths use forward slashes, no `C:\\Users\\…` double-escaping.
3. **Claude Code session import.** With Claude Code installed and at least one past
   session in the workspace, import it. Pass: the session lists, rows render, resume
   works. Then set `CLAUDE_CONFIG_DIR` to a custom folder, relaunch, repeat:
   the resume check must look in that folder, not `%USERPROFILE%\.claude`.
4. **Codex session import.** Same with a Codex rollout. Pass: rows render, a new turn
   resumes the thread.

---

## C. Spec 126 — imported Codex automations (Windows home layout)

Only if the tester has a Codex automation defined in Codex desktop.
1. Import it. Pass: trigger appears enabled, Codex's `automation.toml` is now PAUSED,
   the trigger summary says Orbital took it over.
2. Set the cron to the next minute. Pass: `daemon.log` shows the fork line, the worker
   runs, the session shows the user row then the Codex reply, no `failed to start agent`.
3. Fire again. Pass: it resumes the fork, does not fork a second time.
4. Disable the trigger. Pass: `automation.toml` stays PAUSED and is not rewritten.

---

## D. Spec 129 — claude-code keeps listening between dispatches (step 5, any OS, Windows is a bonus)

Pin claude-code, hands-off autonomy. Prompt:
"Run `sleep 25; echo done` in the background, then end your turn and wait for it.
When it finishes, read this file and tell me its first line."
Pass:
- status goes background-running, then at ~25 s back to running with rows streaming live
- a `continued` row carries the answer, status idle
- exactly one memory reminder, after the continued row
- a second prompt sent during the self turn: the user row lands, no `continued` row is labelled as its reply

---

## E. Spec 131 and 128 (quick regression, any OS)

- 131: after a run ends, the memory record turn must NOT appear as a second reply bubble.
  On a never-sent session, pick Orbital in the pin switcher: the project default pin is cleared.
- 128: pinned A → switch to B → send. B's prompt carries A's native log path and the chat log
  path, and B can read them on demand (ask B "what was the last tool A ran?").

---

## F. General Windows smoke on this .exe

- Install over the existing v0.17.0: data migrates, projects and cards still there.
- SmartScreen is the only scary dialog.
- Create a project, run a prompt that uses `grep` (ripgrep bundled).
- Codex and Claude Code cards detect, sign-in state shows correctly.
- No stray console window at launch or at dispatch (spec 055).
- Zero 400/500 lines in `daemon.log` after the session.

---

## G. Information to request for the new Windows bug report

Ask the reporter for these, in this order of value:

1. **Send diagnostic logs from inside the app**: Global Settings → "Send diagnostic logs".
   It uploads the sanitised daemon log, sub-agent status, version and OS, and they can
   preview it first. Ask them to type what they did in the optional box. This gets us
   the install id to find the bundle.
2. **Exact build**: Global Settings shows the version. Better: the installer filename they
   ran and where they downloaded it (GitHub release vs the Chinese mirror). The mirror can
   lag a release, and the version string alone does not identify the build.
3. **`%APPDATA%\Orbital\logs\daemon.log`** as a file, if the in-app send fails or they
   prefer. Plus the session JSONL under `%APPDATA%\Orbital\` for the affected project.
4. **Windows edition and version** (`winver`): Home vs Pro matters for the sandbox ACL
   and for symlink/long-path behaviour.
5. **Which agent card** was pinned when it happened, and that card's state on the
   Sub-agents page (installed / signed-in / not detected). A screenshot of the card is enough.
6. **Steps + timestamp**: what they typed, what they expected, what they saw, and roughly
   when (local time), so we can line it up with the log.
7. **Screenshot or short recording** of the chat when it failed: the bubble shape tells us
   whether it is a transform bug or a backend one.
8. **Environment specifics if relevant to the bug**: Node version on PATH (`node --version`),
   whether Claude Code / Codex CLIs are installed and how (npm global vs installer), whether
   the workspace is on OneDrive or a network drive, and whether an antivirus prompted.
9. **Is it reproducible**: does it happen on a fresh project, after a relaunch, every time?

---

## H. Reported Windows bug 2026-10-22: locate card rejects Kimi Code / MiniMax Code

Report: both cards "未检测到"; pasting the agents' YAML answers gives
`No file at E:\KIMICODE\Kimi.` and `No file at E:\Minimax\MiniMax.`

Two problems:

1. **Ours — paths with spaces are cut at the first space.** `_ABS_PATH` in
   `agent_os/agents/locate_answer.py` stops at whitespace and excludes quotes, so
   `"E:\Minimax\MiniMax Code\MiniMax Code.exe"` parses as `E:\Minimax\MiniMax`
   even when quoted. Any `C:\Program Files\…` path hits this. Workaround today:
   paste the bare path alone on one line (that branch does not use the regex).
   Fix: honour a quoted `command:` value verbatim, and for an unquoted one take
   the rest of the line up to an arrow/paren instead of up to whitespace.
   Add `test_locate_answer` cases for quoted and unquoted Windows paths with spaces.
2. **Theirs — the agents named their desktop GUI exe, not the CLI.** Orbital runs
   `kimi` / `mcode` over ACP; `Kimi Code.exe` (Electron, v1.1.1) will not answer
   `--version` and the Test would hang 15 s or open the GUI. Detection looks for
   `%APPDATA%\npm\kimi.cmd` / `mcode.cmd`, so the CLIs are most likely not installed.
   The card's Install button (`npm install -g @moonshot-ai/kimi-code` /
   `@minimax-ai/code`, Node ≥ 22.19) is the intended route.

Ask the reporter for: `where kimi`, `where mcode`, `node --version`,
`dir %APPDATA%\npm`, and whether they ran the npm install or only the desktop apps.
Possible product follow-up: the locate prompt should say "the CLI, not the desktop app".

**Correction (user feedback):** there is NO Install button on these cards. Only dsh and
pi have `orbital_install`; kimi-code and minimax-code are bring-your-own, so the
not-detected row shows only the locate box. Their `install_command` exists in the
manifest but is only used in update-failure copy. Product follow-up: a bring-your-own
agent that is not detected should show its install command on the card (copyable),
or the locate hint should say "install the CLI first: npm install -g …".
