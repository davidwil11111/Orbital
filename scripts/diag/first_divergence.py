#!/usr/bin/env python3
# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""First differing byte between two LLM requests, in DeepSeek's prompt order
(spec 140 I2).

Reads request dumps written by ``AGENT_OS_DUMP_LLM_REQUESTS=<dir>``
(``agent_os/agent/providers/openai_compat.py``), renders each one the way
DeepSeek lays a prompt out, and reports where the two first differ: byte
offset, estimated token offset, which segment it falls in (static prefix
section / tools / semi_stable section / instructions / Layer-1 file /
history) and the text on both sides.

Prompt order. DeepSeek's official encoder (``encoding/encoding_dsv4.py`` in
the ``deepseek-ai/DeepSeek-V4-Flash`` Hugging Face repo) renders BOS, then
each message in order with nothing between consecutive system messages, and
a system message that carries ``tools`` is followed by ``"\\n\\n" +
render_tools(tools)`` (a ``## Tools`` block, one ``json.dumps(tool,
ensure_ascii=False)`` per line). Where the API puts the request-level
``tools`` was measured on deepseek-v4.1-flash via OpenCode Go (spec 140,
``run/1027-B/probes/probe_layout.py``): adding tools to a warmed 28k-token
prompt of two system messages kept only ~2k cached (the first system
message), so the tools block follows the FIRST system message, ahead of every
later one. A mid-history system row changed between two requests kept 30,976
of 31,017 tokens cached, so system rows render IN PLACE (no hoisting) on that
route. Those are the defaults; ``--tools-after all`` and ``--hoist`` render
the alternatives. Turn markup past the system block is approximate; it is
identical on both sides, so offsets stay comparable.

Token estimate: DeepSeek's documented ratio, 1 English character ~ 0.3 token,
1 Chinese character ~ 0.6 token.

    python -I scripts/diag/first_divergence.py A.json B.json
    python -I scripts/diag/first_divergence.py --table <dump dir>   # first call of every session, pairwise

A dump is named after the session id its routing header carried.
Standard library only, so it runs under ``python -I``.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
from itertools import combinations

BOS = "<｜begin▁of▁sentence｜>"
USER = "<｜User｜>"
ASSISTANT = "<｜Assistant｜>"
EOS = "<｜end▁of▁sentence｜>"
SEP = "\n\n---\n\n"  # prompt_builder._SEP
TOOLS_HEAD = "## Tools\n\n### Available Tool Schemas\n\n"
TOOLS_TAIL = ("\n\nYou MUST strictly follow the above defined tool name and "
              "parameter schemas to invoke tool calls.")
_L1 = re.compile(r"^\[(PROJECT_STATE|DECISIONS|LESSONS|INDEX)\.md — snapshot")
_CJK = re.compile(r"[⺀-鿿가-힯豈-﫿＀-￯]")


def est_tokens(text: str) -> int:
    cjk = len(_CJK.findall(text))
    return round(0.6 * cjk + 0.3 * (len(text) - cjk))


def _first_line(text: str, n: int = 60) -> str:
    return text.strip().split("\n", 1)[0][:n]


def _content_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
    return "" if content is None else str(content)


def _system_label(i: int, text: str) -> str:
    if text.startswith("[Project Instructions]"):
        return "instructions"
    m = _L1.match(text)
    if m:
        return f"Layer-1 {m.group(1)}.md"
    return {0: "static prefix", 1: "semi_stable"}.get(i, f"system row #{i}")


def _tool_segments(tools) -> list[tuple[str, str]]:
    segs = [("tools header", "\n\n" + TOOLS_HEAD)]
    for k, t in enumerate(tools):
        fn = t.get("function", t) if isinstance(t, dict) else t
        name = fn.get("name", "?") if isinstance(fn, dict) else "?"
        segs.append((f"tool #{k} {name}",
                     json.dumps(fn, ensure_ascii=False) + ("\n" if k < len(tools) - 1 else "")))
    segs.append(("tools footer", TOOLS_TAIL))
    return segs


def render(body: dict, *, hoist: bool = False,
           tools_after: str = "first") -> list[tuple[str, str]]:
    """``[(label, text), ...]`` whose concatenation is the prompt.

    ``tools_after="first"`` (measured default) puts the tools block right
    after the first system message; ``"all"`` after the last leading system
    message. ``hoist`` moves every system row to the front first."""
    msgs = list(body.get("messages") or [])
    if hoist:
        msgs = ([m for m in msgs if m.get("role") == "system"]
                + [m for m in msgs if m.get("role") != "system"])
    tools = body.get("tools") or []
    n_lead = 0
    while n_lead < len(msgs) and msgs[n_lead].get("role") == "system":
        n_lead += 1
    tools_at = (0 if tools_after == "first" else n_lead - 1) if n_lead else -1
    segs: list[tuple[str, str]] = [("BOS", BOS)]
    if tools and tools_at < 0:
        segs += _tool_segments(tools)
    sys_i = 0
    for j, m in enumerate(msgs):
        role = m.get("role")
        text = _content_text(m.get("content"))
        if role == "system":
            label = _system_label(sys_i, text)
            if label in ("static prefix", "semi_stable"):
                parts = text.split(SEP)
                for k, part in enumerate(parts):
                    segs.append((f"{label} §{k} '{_first_line(part)}'",
                                 part + (SEP if k < len(parts) - 1 else "")))
            else:
                segs.append((label, text))
            if tools and j == tools_at:
                segs += _tool_segments(tools)
            sys_i += 1
        elif role == "user":
            segs.append((f"history #{j} user", USER + text))
        elif role == "tool":
            segs.append((f"history #{j} tool_result", f"<tool_result>{text}</tool_result>"))
        elif role == "assistant":
            calls = m.get("tool_calls") or []
            call_text = "".join(
                json.dumps(c.get("function", c), ensure_ascii=False) for c in calls)
            segs.append((f"history #{j} assistant",
                         ASSISTANT + (m.get("reasoning_content") or "") + text + call_text + EOS))
        else:
            segs.append((f"history #{j} {role}", text))
    return segs


def _offsets(segs):
    out, pos = [], 0
    for label, text in segs:
        b = len(text.encode("utf-8"))
        out.append((pos, pos + b, label, text))
        pos += b
    return out


def divergence(a: dict, b: dict, *, hoist: bool = False,
               tools_after: str = "first") -> dict:
    sa = render(a, hoist=hoist, tools_after=tools_after)
    sb = render(b, hoist=hoist, tools_after=tools_after)
    ta, tb = "".join(t for _, t in sa), "".join(t for _, t in sb)
    ba, bb = ta.encode("utf-8"), tb.encode("utf-8")
    n = min(len(ba), len(bb))
    off = next((i for i in range(n) if ba[i] != bb[i]), n)
    identical = off == n and len(ba) == len(bb)
    # back off to a character boundary so the context decodes cleanly
    while 0 < off < len(ba) and (ba[off] & 0xC0) == 0x80:
        off -= 1
    prefix_text = ba[:off].decode("utf-8", errors="ignore")

    def seg_at(segs):
        prev = None
        for start, end, label, _text in _offsets(segs):
            if start <= off < end:
                if off == start and prev is not None:
                    return f"end of {prev} / start of {label}", 0
                return label, off - start
            prev = label
        return (f"end of {prev} / end of prompt" if prev else "(empty)"), 0

    def ctx(raw: bytes) -> tuple[str, str]:
        before = raw[max(0, off - 160):off].decode("utf-8", errors="ignore")
        after = raw[off:off + 160].decode("utf-8", errors="ignore")
        return before, after

    la, ia = seg_at(sa)
    lb, ib = seg_at(sb)
    return dict(
        identical=identical, byte=off, chars=len(prefix_text), tokens=est_tokens(prefix_text),
        total_tokens_a=est_tokens(ta), total_tokens_b=est_tokens(tb),
        segment_a=la, in_segment_a=ia, segment_b=lb, in_segment_b=ib,
        context_a=ctx(ba), context_b=ctx(bb),
    )


def _load(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _print_pair(pa: str, pb: str, **kw) -> None:
    da, db = _load(pa), _load(pb)
    r = divergence(da["body"], db["body"], **kw)
    print(f"A: {pa}\n   session={da['meta'].get('session_id')} ~{r['total_tokens_a']} tok")
    print(f"B: {pb}\n   session={db['meta'].get('session_id')} ~{r['total_tokens_b']} tok")
    if r["identical"]:
        print("identical prompts")
        return
    print(f"first difference at byte {r['byte']} (char {r['chars']}, ~{r['tokens']} tokens)")
    print(f"  A segment: {r['segment_a']} (+{r['in_segment_a']} bytes)")
    print(f"  B segment: {r['segment_b']} (+{r['in_segment_b']} bytes)")
    for side, (before, after) in (("A", r["context_a"]), ("B", r["context_b"])):
        print(f"--- {side}: ...{before!r}")
        print(f"    {side} >>> {after!r}")


def first_calls(dump_dir: str) -> dict[str, str]:
    """session stem -> its first dumped request (``<stem>_1.json``)."""
    out = {}
    for f in sorted(glob.glob(os.path.join(dump_dir, "*_1.json"))):
        out[os.path.basename(f)[:-len("_1.json")]] = f
    return out


def _print_table(dump_dir: str, **kw) -> None:
    fc = first_calls(dump_dir)
    print("| A | B | first diff ~tok | A total ~tok | segment (A) | segment (B) |")
    print("|---|---|---:|---:|---|---|")
    for a, b in combinations(sorted(fc), 2):
        r = divergence(_load(fc[a])["body"], _load(fc[b])["body"], **kw)
        tok = "identical" if r["identical"] else r["tokens"]
        print(f"| {a} | {b} | {tok} | {r['total_tokens_a']} | {r['segment_a']} | {r['segment_b']} |")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("paths", nargs="*")
    p.add_argument("--table", metavar="DUMP_DIR")
    p.add_argument("--hoist", action="store_true",
                   help="move every system row to the front (not what OpenCode Go does)")
    p.add_argument("--tools-after", choices=("first", "all"), default="first",
                   help="tools after the first system message (measured) or after all leading ones")
    a = p.parse_args(argv)
    kw = dict(hoist=a.hoist, tools_after=a.tools_after)
    if a.table:
        _print_table(a.table, **kw)
        return 0
    if len(a.paths) != 2:
        p.error("give two dump files, or --table DIR")
    _print_pair(*a.paths, **kw)
    return 0


if __name__ == "__main__":
    sys.exit(main())
