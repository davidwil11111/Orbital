# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""The locate card's parser: what an agent answers to the "where are you
installed" prompt, turned into a launcher path, PATH entries and env names.

Samples are the real answers Codex (via the ChatGPT app) and Claude Code
gave on 2026-10-21; the parser must also take a bare path, a fenced block
and prose around the block, and must refuse a missing or relative command.
"""

import pytest

from agent_os.agents.locate_answer import (
    LocateAnswerError,
    locate_prompt,
    parse_locate_answer,
)

CODEX_ANSWER = """agent: Codex
command: /opt/homebrew/bin/codex
target: /opt/homebrew/lib/node_modules/@openai/codex/bin/codex.js
version: 0.157.1
env: {}
path:
  - /opt/homebrew/opt/node@24/bin
"""

CLAUDE_ANSWER = """```yaml
agent: Claude Code
command: /Users/x/.local/bin/claude (symlink) -> /Users/x/.local/share/claude/versions/2.1.306 (native arm64 Mach-O binary, no wrapper)
version: 2.1.306
env: {}
path: []
```
"""


class TestSamples:
    def test_codex_sample(self):
        a = parse_locate_answer(CODEX_ANSWER)
        assert a.command == "/opt/homebrew/bin/codex"
        assert a.target == "/opt/homebrew/lib/node_modules/@openai/codex/bin/codex.js"
        assert a.version == "0.157.1"
        assert a.env_names == ()
        assert a.path_prepend == ("/opt/homebrew/opt/node@24/bin",)
        assert a.agent == "Codex"

    def test_claude_sample_first_absolute_path_wins(self):
        a = parse_locate_answer(CLAUDE_ANSWER)
        assert a.command == "/Users/x/.local/bin/claude"
        # The versioned file moves on every self-update; it is information only.
        assert a.target == "/Users/x/.local/share/claude/versions/2.1.306"
        assert a.version == "2.1.306"
        assert a.path_prepend == ()

    def test_bare_path(self):
        a = parse_locate_answer("  /opt/homebrew/bin/codex \n")
        assert a.command == "/opt/homebrew/bin/codex"
        assert a.path_prepend == () and a.env_names == ()

    def test_bare_windows_path(self):
        a = parse_locate_answer(r"C:\Users\me\AppData\Local\Programs\claude\claude.exe")
        assert a.command == r"C:\Users\me\AppData\Local\Programs\claude\claude.exe"

    def test_prose_around_the_block(self):
        text = ("Sure — here is where I live:\n\n" + CODEX_ANSWER
                + "\nLet me know if you need anything else.\n")
        a = parse_locate_answer(text)
        assert a.command == "/opt/homebrew/bin/codex"
        assert a.path_prepend == ("/opt/homebrew/opt/node@24/bin",)

    def test_fence_without_language_tag(self):
        a = parse_locate_answer("```\n" + CODEX_ANSWER + "```")
        assert a.command == "/opt/homebrew/bin/codex"


class TestEnvAndPath:
    def test_env_names_only_values_ignored(self):
        text = ("command: /usr/local/bin/codebuddy\n"
                "env:\n  CODEBUDDY_API_KEY: \"\"\n"
                "  CODEBUDDY_INTERNET_ENVIRONMENT: internal\n"
                "path: []\n")
        a = parse_locate_answer(text)
        assert a.env_names == ("CODEBUDDY_API_KEY", "CODEBUDDY_INTERNET_ENVIRONMENT")

    def test_env_as_inline_map_and_list(self):
        a = parse_locate_answer("command: /x/bin/a\nenv: {A_KEY: '', B_KEY: ''}\n")
        assert a.env_names == ("A_KEY", "B_KEY")
        b = parse_locate_answer("command: /x/bin/a\nenv:\n  - A_KEY\n  - B_KEY=secret\n")
        assert b.env_names == ("A_KEY", "B_KEY")

    def test_path_inline_list_and_quotes(self):
        a = parse_locate_answer(
            "command: /x/bin/a\npath: ['/opt/homebrew/opt/node@24/bin', \"/Users/me/.nvm/versions/node/v24.1.0/bin\"]\n")
        assert a.path_prepend == ("/opt/homebrew/opt/node@24/bin",
                                  "/Users/me/.nvm/versions/node/v24.1.0/bin")

    def test_unfilled_template_placeholders_are_empty(self):
        text = ("command: /x/bin/a\n"
                "env: <map of environment variable NAMES ...>\n"
                "path: <directories that must be on PATH>\n")
        a = parse_locate_answer(text)
        assert a.env_names == () and a.path_prepend == ()

    def test_relative_path_entries_are_dropped(self):
        a = parse_locate_answer("command: /x/bin/a\npath:\n  - bin\n  - /opt/x/bin\n")
        assert a.path_prepend == ("/opt/x/bin",)


class TestErrors:
    def test_missing_command(self):
        with pytest.raises(LocateAnswerError, match="command"):
            parse_locate_answer("agent: Codex\nversion: 1.0\n")

    def test_relative_command(self):
        with pytest.raises(LocateAnswerError, match="absolute"):
            parse_locate_answer("command: codex\n")

    def test_empty(self):
        with pytest.raises(LocateAnswerError):
            parse_locate_answer("   \n")

    def test_protocol_none_style_answer_is_still_a_command_error(self):
        with pytest.raises(LocateAnswerError, match="absolute"):
            parse_locate_answer("agent: Codex\ncommand: codex\nprotocol: none\n")


class TestPrompt:
    def test_prompt_names_the_agent_and_asks_for_yaml(self):
        p = locate_prompt("Claude Code")
        assert p.startswith("Orbital (a desktop app) could not find Claude Code on this machine.")
        for key in ("agent:", "command:", "target:", "version:", "env:", "path:"):
            assert key in p
        assert "command -v" in p


class TestWindowsPathsWithSpaces:
    """Reported 2026-10-22: ``C:\\Program Files\\...`` (and ``E:\\Minimax\\MiniMax
    Code\\...``) were cut at the first space, so the card said
    ``No file at C:\\Program``. The path is the whole value, quoted or not."""

    def test_quoted_command_is_taken_verbatim(self):
        a = parse_locate_answer(
            "agent: Kimi Code\n" + r'command: "C:\Program Files\nodejs\node.exe"' + "\nversion: 1.0\n")
        assert a.command == r"C:\Program Files\nodejs\node.exe"
        assert a.target is None

    def test_single_quoted_command_is_taken_verbatim(self):
        a = parse_locate_answer(r"command: 'E:\Minimax\MiniMax Code\MiniMax Code.exe'" + "\n")
        assert a.command == r"E:\Minimax\MiniMax Code\MiniMax Code.exe"

    def test_unquoted_command_runs_to_the_end_of_the_line(self):
        a = parse_locate_answer(
            "agent: MiniMax Code\n" + r"command: E:\Minimax\MiniMax Code\MiniMax Code.exe" + "\n")
        assert a.command == r"E:\Minimax\MiniMax Code\MiniMax Code.exe"

    def test_unquoted_command_stops_at_a_parenthesised_note(self):
        a = parse_locate_answer(r"command: C:\Program Files\nodejs\node.exe (the launcher)" + "\n")
        assert a.command == r"C:\Program Files\nodejs\node.exe"

    def test_program_files_x86_is_one_path(self):
        a = parse_locate_answer(r"command: C:\Program Files (x86)\nodejs\node.exe" + "\n")
        assert a.command == r"C:\Program Files (x86)\nodejs\node.exe"

    def test_arrow_target_with_spaces_on_the_command_line(self):
        a = parse_locate_answer(
            r'command: "C:\Program Files\X\x.cmd" -> "C:\Program Files\X\node_modules\x\bin\x.js"' + "\n")
        assert a.command == r"C:\Program Files\X\x.cmd"
        assert a.target == r"C:\Program Files\X\node_modules\x\bin\x.js"

    def test_unquoted_arrow_target_with_spaces(self):
        a = parse_locate_answer(
            r"command: C:\Program Files\X\x.cmd (shim) -> C:\Program Files\X\bin\x.js" + "\n")
        assert a.command == r"C:\Program Files\X\x.cmd"
        assert a.target == r"C:\Program Files\X\bin\x.js"

    def test_target_key_with_spaces(self):
        a = parse_locate_answer(
            r"command: C:\Tools\x.cmd" + "\n" + r"target: C:\Program Files\X\bin\x.js" + "\n")
        assert a.target == r"C:\Program Files\X\bin\x.js"

    def test_posix_samples_still_parse_the_same(self):
        a = parse_locate_answer(CLAUDE_ANSWER)
        assert a.command == "/Users/x/.local/bin/claude"
        assert a.target == "/Users/x/.local/share/claude/versions/2.1.306"

    def test_prose_before_the_path_still_finds_it(self):
        a = parse_locate_answer("command: the launcher is /opt/bin/codex here\n")
        assert a.command == "/opt/bin/codex"
