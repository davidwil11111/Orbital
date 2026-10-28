# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Lenient text reading for anything that goes into the prompt (spec 141 §1 A).

Files the user or the agent's shell can touch are untrusted input. On a
Chinese-locale Windows machine a shell append (Python ``open(..., "a")``
without ``encoding=``, PowerShell 5.1 ``Add-Content``, ``cmd`` ``echo >>``)
writes GBK into an otherwise-UTF-8 file; a strict ``encoding="utf-8"`` read of
that file raised ``UnicodeDecodeError`` on every LLM call and bricked the
session. This module is the single owner of the *bytes on disk → text*
boundary:

1. Strip a UTF-8 BOM, then try strict UTF-8 on the whole file (fast path —
   every file Orbital writes itself).
2. On failure decode LINE BY LINE (the failing shape is a mixed file: UTF-8
   head + ANSI tail): UTF-8 strict, then on Windows the ANSI code page from
   ``locale.getpreferredencoding(False)`` strict, else UTF-8 with
   ``errors="replace"``.
3. Log one WARNING per path per process naming the encodings used.

Newlines are translated the way a text-mode ``open()`` does (``\r\n`` and
``\r`` → ``\n``), so a swapped call site sees exactly the text it saw before.
Read errors (``OSError``) still yield ``None`` — the same contract the call
sites had. Writes are not touched: they stay UTF-8.
"""

from __future__ import annotations

import codecs
import locale
import logging
import os
import sys
import threading

logger = logging.getLogger(__name__)

_UTF8 = "utf-8"
_REPLACE_LABEL = "utf-8 (replacement chars)"

_warned_paths: set[str] = set()
_warned_lock = threading.Lock()


def _is_windows() -> bool:
    return sys.platform == "win32"


def _preferred_encoding() -> str:
    return locale.getpreferredencoding(False) or ""


def _ansi_codepage() -> str | None:
    """The Windows ANSI code page (cp936 on zh-CN), or None when it adds
    nothing over UTF-8 (non-Windows, UTF-8 mode, unknown codec)."""
    if not _is_windows():
        return None
    enc = _preferred_encoding()
    if not enc:
        return None
    try:
        name = codecs.lookup(enc).name
    except LookupError:
        return None
    if name in ("utf-8", "utf-8-sig", "ascii"):
        return None
    return enc


def decode_lenient(data: bytes, path: str | None = None) -> tuple[str, tuple[str, ...]]:
    """Decode ``data`` per the module contract.

    Returns ``(text, fallbacks)`` where ``fallbacks`` names the encodings used
    besides plain UTF-8 — empty on the fast path. Never raises on bad bytes.
    """
    if data.startswith(codecs.BOM_UTF8):
        data = data[len(codecs.BOM_UTF8):]
    try:
        return data.decode(_UTF8), ()
    except UnicodeDecodeError:
        pass

    ansi = _ansi_codepage()
    used: list[str] = []
    parts: list[str] = []
    for line in data.splitlines(keepends=True):
        try:
            parts.append(line.decode(_UTF8))
            continue
        except UnicodeDecodeError:
            pass
        if ansi is not None:
            try:
                parts.append(line.decode(ansi))
                if ansi not in used:
                    used.append(ansi)
                continue
            except UnicodeDecodeError:
                pass
        parts.append(line.decode(_UTF8, errors="replace"))
        if _REPLACE_LABEL not in used:
            used.append(_REPLACE_LABEL)

    fallbacks = tuple(used)
    if path is not None:
        _warn_once(path, fallbacks)
    return "".join(parts), fallbacks


def _warn_once(path: str, fallbacks: tuple[str, ...]) -> None:
    key = os.path.abspath(path)
    with _warned_lock:
        if key in _warned_paths:
            return
        _warned_paths.add(key)
    logger.warning(
        "non-UTF-8 text in %s: decoded line by line with utf-8 + %s",
        path, " + ".join(fallbacks),
    )


def _universal_newlines(text: str) -> str:
    if "\r" not in text:
        return text
    return text.replace("\r\n", "\n").replace("\r", "\n")


def read_text_lenient_detail(path: str, limit: int | None = None) -> tuple[str, tuple[str, ...]] | None:
    """Like :func:`read_text_lenient` but also returns the fallback encodings
    used (empty tuple on the fast path). ``None`` on ``OSError``."""
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return None
    text, fallbacks = decode_lenient(data, path)
    text = _universal_newlines(text)
    if limit is not None:
        text = text[:limit]
    return text, fallbacks


def read_text_lenient(path: str, limit: int | None = None) -> str | None:
    """Read ``path`` as text without ever raising on bad bytes.

    ``limit`` caps the returned text at that many characters (the
    ``f.read(n)`` the call sites used). ``None`` on ``OSError``.
    """
    res = read_text_lenient_detail(path, limit)
    return None if res is None else res[0]


def read_text_lenient_raising(path: str) -> str:
    """Lenient decode, but ``OSError`` propagates — for the few call sites
    whose callers relied on ``open()`` raising (e.g. ``Session.load``)."""
    with open(path, "rb") as f:
        data = f.read()
    return _universal_newlines(decode_lenient(data, path)[0])


def _reset_warned_for_tests() -> None:
    with _warned_lock:
        _warned_paths.clear()
