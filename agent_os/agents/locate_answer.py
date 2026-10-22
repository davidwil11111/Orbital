# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""The locate card: the prompt a user pastes into an agent Orbital could not
find, and the parser for what the agent answers.

Detection misses because the daemon's PATH is not the user's shell PATH. The
agent, running in that shell, can resolve itself (``command -v``) and name the
directories a version manager added — the user does not have to know either.
The answer is YAML with a fixed set of keys; the parser is deliberately
tolerant (a code fence, prose around the block, a bare path) because the text
comes straight from a chat window, and strict about the one thing that
matters: the command must be an absolute path.

Pure functions, no I/O: the store validates against the disk, the engine runs
the check command.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# What the card copies to the clipboard. ``{name}`` is the agent's display
# name. Launcher first, versioned file second: the launcher survives the
# agent's self-updates (claude-code moves its real binary on every one), so
# that is the path Orbital keeps.
_PROMPT = """Orbital (a desktop app) could not find {name} on this machine. Report where you are installed so it can run you.

Use a shell command to find yourself (`command -v`, `which`, or `Get-Command` on Windows). Reply with exactly this YAML and nothing else:

agent: <your product name>
command: <absolute path of the executable Orbital should run. Prefer the stable launcher or symlink over the versioned file it points to>
target: <the real file the launcher points to, if it is a symlink or wrapper; otherwise omit>
version: <your version>
env: <map of environment variable NAMES Orbital must set for you to run unattended (names only, no values); {{}} if none>
path: <directories that must be on PATH for you to start, for example the node directory a version manager added; [] if none>
"""

_KEYS = ("agent", "command", "target", "version", "env", "path", "protocol")
_KEY_LINE = re.compile(r"^(" + "|".join(_KEYS) + r")\s*:\s*(.*)$", re.IGNORECASE)
# A POSIX absolute path, a Windows drive path or a UNC path, up to whitespace
# or a bracket/quote. Trailing punctuation an agent may append is trimmed.
_ABS_PATH = re.compile(r"(?:/[^\s()'\"<>\[\]]+|[A-Za-z]:\\[^\s()'\"<>\[\]]+|\\\\[^\s()'\"<>\[\]]+)")
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_FENCE = re.compile(r"```[a-zA-Z]*\s*\n(.*?)```", re.DOTALL)


class LocateAnswerError(ValueError):
    """The pasted text does not name an agent executable."""


@dataclass(frozen=True)
class LocateAnswer:
    command: str                      # absolute path of the launcher to run
    target: str | None = None         # the real file, information only
    version: str | None = None
    env_names: tuple[str, ...] = ()   # variables the user must fill, no values
    path_prepend: tuple[str, ...] = ()  # directories for the front of PATH
    agent: str | None = None


def locate_prompt(name: str) -> str:
    return _PROMPT.format(name=name)


def is_absolute_path(value: str) -> bool:
    v = value.strip()
    return bool(v) and (v.startswith("/") or v.startswith("\\\\")
                        or re.match(r"^[A-Za-z]:[\\/]", v) is not None)


def _first_abs_path(value: str) -> str | None:
    m = _ABS_PATH.search(value)
    if not m:
        return None
    return m.group(0).rstrip(".,;:")


# Where an unquoted ``command:`` value stops being the path: the arrow or
# parenthesis an agent appends ("(symlink) -> /real/file"), never whitespace —
# ``C:\Program Files\...`` has spaces inside the path itself. A parenthesised
# group that the path continues through (``Program Files (x86)\node``) is
# part of the path and does not terminate it.
_PATH_TERMINATOR = re.compile(r"\s+(?:->|→)(?=\s)|\s+\((?![^()]*\)[\\/])")


def _path_value(value: str) -> str | None:
    """The absolute path a ``command:``/``target:`` value names, whole.

    A quoted value is taken verbatim (the quotes are the agent telling us
    where the path ends). An unquoted value that starts with an absolute
    path runs to the first arrow/parenthesis or the end of the line, so a
    Windows path with spaces survives. Anything else falls back to the first
    absolute path found in the text, as before.
    """
    v = value.strip()
    if not v:
        return None
    if v[0] in "'\"":
        end = v.find(v[0], 1)
        if end > 1:
            inner = v[1:end].strip()
            return inner if is_absolute_path(inner) else None
        return _first_abs_path(v)
    if is_absolute_path(v):
        m = _PATH_TERMINATOR.search(v)
        head = v[:m.start()] if m else v
        head = head.rstrip().rstrip(".,;:")
        return head or None
    return _first_abs_path(v)


def _target_after_arrow(value: str, command: str) -> str | None:
    """The target an agent writes after the command on the same line
    (``/launcher (symlink) -> /real/file``), parsed like a path value."""
    rest = value.strip()
    idx = rest.find(command)
    if idx >= 0:
        rest = rest[idx + len(command):]
    m = re.search(r"(?:->|→)\s*(.+)$", rest)
    if m:
        return _path_value(m.group(1))
    return _first_abs_path(rest)


def _strip_quotes(value: str) -> str:
    v = value.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
        return v[1:-1]
    return v


def _is_placeholder(value: str) -> bool:
    """An unfilled template value: ``<map of ...>``."""
    v = value.strip()
    return v.startswith("<") and v.endswith(">")


def _inline_list(value: str) -> list[str] | None:
    """``[a, b]`` → items; ``[]`` → []; anything else → None."""
    v = value.strip()
    if not (v.startswith("[") and v.endswith("]")):
        return None
    inner = v[1:-1].strip()
    if not inner:
        return []
    return [_strip_quotes(p) for p in inner.split(",") if p.strip()]


def _inline_map_names(value: str) -> list[str] | None:
    """``{A: '', B: x}`` → [A, B]; ``{}`` → []; anything else → None."""
    v = value.strip()
    if not (v.startswith("{") and v.endswith("}")):
        return None
    inner = v[1:-1].strip()
    if not inner:
        return []
    return [p.split(":", 1)[0].strip().strip("'\"") for p in inner.split(",") if p.strip()]


def _extract_block(text: str) -> list[str]:
    """The YAML lines: the first fenced block if any, else the run of
    ``key:`` lines (plus their indented continuations) wherever it sits."""
    fenced = _FENCE.search(text)
    source = fenced.group(1) if fenced else text
    lines = source.splitlines()
    out: list[str] = []
    started = False
    for raw in lines:
        line = raw.rstrip()
        if _KEY_LINE.match(line.strip()) and not line[:1].isspace():
            started = True
            out.append(line.strip())
            continue
        if not started:
            continue
        if not line.strip():
            # A blank line ends the block only if no key follows it.
            out.append("")
            continue
        if line[:1].isspace():
            out.append(line)
            continue
        break   # prose after the block
    while out and out[-1] == "":
        out.pop()
    return out


def _parse_block(lines: list[str]) -> dict[str, object]:
    """Minimal YAML subset: ``key: scalar``, ``key:`` followed by ``- item``
    or ``NAME: value`` continuation lines, inline ``[..]`` and ``{..}``."""
    fields: dict[str, object] = {}
    current: str | None = None
    for line in lines:
        if not line.strip():
            continue
        if not line[:1].isspace():
            m = _KEY_LINE.match(line)
            if not m:
                continue
            current = m.group(1).lower()
            fields[current] = m.group(2).strip()
            continue
        if current is None:
            continue
        item = line.strip()
        existing = fields.get(current)
        if not isinstance(existing, list):
            existing = [] if not isinstance(existing, str) or not existing else [existing]
            fields[current] = existing
        existing.append(item[1:].strip() if item.startswith("-") else item)
    return fields


def _env_names(value: object) -> tuple[str, ...]:
    names: list[str] = []
    if isinstance(value, str):
        if not value or _is_placeholder(value):
            return ()
        inline = _inline_map_names(value)
        if inline is None:
            inline = _inline_list(value)
        if inline is None:
            inline = [value]
        candidates = inline
    elif isinstance(value, list):
        candidates = value
    else:
        return ()
    for c in candidates:
        c = _strip_quotes(str(c))
        name = re.split(r"[:=]", c, maxsplit=1)[0].strip().strip("'\"")
        if name and _ENV_NAME.match(name) and name not in names:
            names.append(name)
    return tuple(names)


def _path_entries(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        if not value or _is_placeholder(value):
            return ()
        items = _inline_list(value)
        if items is None:
            items = [value]
    elif isinstance(value, list):
        items = value
    else:
        return ()
    out: list[str] = []
    for item in items:
        item = _strip_quotes(str(item)).rstrip("/\\") or str(item)
        if is_absolute_path(item) and item not in out:
            out.append(item)
    return tuple(out)


def parse_locate_answer(text: str) -> LocateAnswer:
    """Turn an agent's answer (or a bare path) into a :class:`LocateAnswer`.

    Raises :class:`LocateAnswerError` with a message fit for the card.
    """
    if not isinstance(text, str) or not text.strip():
        raise LocateAnswerError("Paste the agent's answer (or the path to its executable) first.")
    stripped = text.strip()
    # A single line that is just a path (``C:\...`` is not a ``key:`` line:
    # only the known keys match).
    if "\n" not in stripped and not _KEY_LINE.match(stripped) \
            and is_absolute_path(_strip_quotes(stripped)):
        return LocateAnswer(command=_strip_quotes(stripped))

    fields = _parse_block(_extract_block(stripped))
    raw_command = fields.get("command")
    if isinstance(raw_command, list):
        raw_command = " ".join(raw_command)
    if raw_command is None or not str(raw_command).strip() or _is_placeholder(str(raw_command)):
        # No YAML block at all but an absolute path somewhere in the text.
        if not fields:
            lone = _first_abs_path(stripped) if "\n" not in stripped else None
            if lone:
                return LocateAnswer(command=lone)
        raise LocateAnswerError(
            "The answer has no `command:` line naming the executable.")
    command = _path_value(str(raw_command))
    if command is None:
        raise LocateAnswerError(
            f"`command: {str(raw_command).strip()}` is not an absolute path. "
            "Orbital needs the full path to the executable, for example the "
            "output of `command -v` or `which`.")

    raw_target = fields.get("target")
    target = _path_value(str(raw_target)) if isinstance(raw_target, str) \
        and raw_target and not _is_placeholder(raw_target) else None
    # The Claude answer puts the target after an arrow on the command line.
    if target is None:
        target = _target_after_arrow(str(raw_command), command)

    raw_version = fields.get("version")
    version = _strip_quotes(str(raw_version)) if isinstance(raw_version, str) \
        and raw_version and not _is_placeholder(raw_version) else None
    raw_agent = fields.get("agent")
    agent = _strip_quotes(str(raw_agent)) if isinstance(raw_agent, str) \
        and raw_agent and not _is_placeholder(raw_agent) else None

    return LocateAnswer(
        command=command,
        target=target,
        version=version,
        env_names=_env_names(fields.get("env")),
        path_prepend=_path_entries(fields.get("path")),
        agent=agent,
    )
