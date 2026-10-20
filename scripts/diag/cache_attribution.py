#!/usr/bin/env python3
# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Prompt-cache attribution over daemon logs + session JSONLs (spec 112 §6).

Three tables:

  §1.5  Where in a run does the agent edit a Layer-1 memory file, and what
        does the NEXT call lose? (``[CACHE_AUDIT]`` pairs joined to the
        session's edit/write tool calls.)
  §1.6  Mid-run pairs with NO edit that still lost >50% of the prefix, by the
        previous call's tools (the browser-snapshot rewrite).
  §1.7  Substantive runs (>=8 tool calls, or >=1 workspace file changed) that
        ended without writing any memory file — from the JSONLs alone.

Reads logs and JSONLs only; writes nothing. Standard library only, so it can
run under ``python -I``::

    python -I scripts/diag/cache_attribution.py \\
        --logs "~/Library/Application Support/Orbital/logs" \\
        --since 2026-10-01 \\
        --projects-json "~/Library/Application Support/Orbital/projects.json"

``--logs`` takes a directory (every ``daemon.log*`` in it, oldest rotation
first) or explicit files. ``--projects-json`` locates the workspaces whose
``orbital/sessions/*.jsonl`` the audit pairs are joined against; ``--workspace``
adds workspaces directly. ``--model-prefix`` narrows the audit rows (default:
every model).

Method (spec 112 §6): adjacent audit lines with identical
(model, input, cached, output) within 120 s are the pre-A4 streaming
double-log and are deduped; each audit is aligned to the next
``agent.diag response_shape`` within 5 s for session / iteration / tools;
same-session neighbours a→b (gap < 1 h, b.input >= 20k) give
``frac = b.cached / (a.input + a.output)``; same run = b.iteration >
a.iteration. JSONL timestamps are UTC, log timestamps are local time.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone

L1 = ("PROJECT_STATE.md", "DECISIONS.md", "LESSONS.md", "INDEX.md")
L1_ASKS = L1 + ("ASKS.md",)

TS = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),(\d{3}) ")
AUD = re.compile(r"\[CACHE_AUDIT\] model=(\S+) input=(\d+) cached=(\d+) output=(\d+)"
                 r"(?: cache_rate=\S+)?(?: caller=(\S+))?")
SHAPE = re.compile(r'agent\.diag (\{"event": "response_shape".*\})$')


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


def _parse_args(argv=None) -> argparse.Namespace:
    app = os.path.expanduser("~/Library/Application Support/Orbital")
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--logs", nargs="+", default=[os.path.join(app, "logs")],
                   help="log directory (all daemon.log* inside) or log files")
    p.add_argument("--since", default=None,
                   help="YYYY-MM-DD; ignore audit rows and runs before this local date")
    p.add_argument("--projects-json", default=os.path.join(app, "projects.json"),
                   help="projects.json whose workspaces hold orbital/sessions/*.jsonl")
    p.add_argument("--workspace", action="append", default=[],
                   help="extra workspace root(s) to scan for sessions")
    p.add_argument("--model-prefix", default="",
                   help="only audit rows whose model starts with this (e.g. deepseek)")
    p.add_argument("--caller", default="main",
                   help="only audit rows with this caller tag (A4); 'any' keeps all")
    return p.parse_args(argv)


def _log_files(specs: list[str]) -> list[str]:
    files: list[str] = []
    for spec in specs:
        spec = os.path.expanduser(spec)
        if os.path.isdir(spec):
            found = glob.glob(os.path.join(spec, "daemon.log*"))
            # Oldest rotation first: daemon.log.N (largest N) ... daemon.log
            def _key(f: str):
                tail = f.rsplit("daemon.log", 1)[1]
                return (0, -int(tail[1:])) if tail[1:].isdigit() else (1, 0)
            files.extend(sorted(found, key=_key))
        elif os.path.exists(spec):
            files.append(spec)
        else:
            print(f"warning: no such log path {spec}", file=sys.stderr)
    return files


def _workspaces(projects_json: str, extra: list[str]) -> list[str]:
    out = [os.path.expanduser(w) for w in extra]
    path = os.path.expanduser(projects_json)
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            print(f"warning: cannot read {path}: {exc}", file=sys.stderr)
            data = []
        if isinstance(data, dict):
            data = data.get("projects", list(data.values()))
        for p in data if isinstance(data, list) else []:
            if isinstance(p, dict):
                ws = p.get("workspace") or p.get("path")
                if ws:
                    out.append(ws)
    seen: set[str] = set()
    return [w for w in out if not (w in seen or seen.add(w))]


def _since(value: str | None):
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d").astimezone()


# ---------------------------------------------------------------------------
# Log parsing
# ---------------------------------------------------------------------------


def _parse_ts(m) -> datetime:
    return datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").replace(
        microsecond=int(m.group(2)) * 1000).astimezone()


def read_logs(files: list[str], *, since, model_prefix: str, caller: str):
    audits, shapes = [], []
    for lf in files:
        with open(lf, errors="replace") as fh:
            for line in fh:
                m = TS.match(line)
                if not m:
                    continue
                t = _parse_ts(m)
                if since is not None and t < since:
                    continue
                a = AUD.search(line)
                if a:
                    tag = a.group(5) or "main"
                    if caller != "any" and tag != caller:
                        continue
                    audits.append((t, a.group(1), int(a.group(2)), int(a.group(3)), int(a.group(4))))
                    continue
                s = SHAPE.search(line)
                if s:
                    try:
                        shapes.append((t, json.loads(s.group(1))))
                    except ValueError:
                        continue
    # Dedupe the pre-A4 streaming double-log.
    dedup: list = []
    for a in audits:
        if dedup and dedup[-1][1:] == a[1:] and (a[0] - dedup[-1][0]).total_seconds() < 120:
            continue
        dedup.append(a)
    audits = dedup
    # Align each audit to the next response_shape within 5 s.
    calls = []
    si = 0
    for t, model, inp, cached, out in audits:
        while si < len(shapes) and shapes[si][0] < t:
            si += 1
        j = si
        if j < len(shapes) and (shapes[j][0] - t).total_seconds() <= 5:
            d = shapes[j][1]
            calls.append(dict(t=t, model=model, input=inp, cached=cached, output=out,
                              session=d.get("session_id"), iteration=d.get("iteration"),
                              tools=d.get("tool_call_names") or []))
    calls = [c for c in calls if c["session"] and c["model"].startswith(model_prefix)]
    return audits, calls


# ---------------------------------------------------------------------------
# Session JSONLs
# ---------------------------------------------------------------------------


def session_files(workspaces: list[str]) -> dict[str, str]:
    files: dict[str, str] = {}
    for ws in workspaces:
        for f in glob.glob(os.path.join(ws, "orbital", "sessions", "*.jsonl")):
            files[os.path.basename(f)[:-6]] = f
    return files


def _row_ts(r: dict):
    try:
        rt = datetime.fromisoformat(str(r.get("timestamp", "")).replace("Z", "+00:00"))
    except ValueError:
        return None
    return rt if rt.tzinfo is not None else rt.replace(tzinfo=timezone.utc)


def _write_paths(row: dict) -> list[str]:
    paths = []
    for tc in row.get("tool_calls") or []:
        fn = tc.get("function", {})
        if fn.get("name") in ("edit", "write"):
            try:
                paths.append(json.loads(fn.get("arguments") or "{}").get("path", "") or "")
            except ValueError:
                pass
    return [p for p in paths if p]


class _EditIndex:
    """basenames of edit/write targets in a session's assistant rows within
    [t-15 s, t+5 s]."""

    def __init__(self, files: dict[str, str]):
        self._files = files
        self._cache: dict[str, list] = {}

    def at(self, session: str, t: datetime):
        f = self._files.get(session)
        if not f:
            return None
        if f not in self._cache:
            rows = []
            with open(f, errors="replace") as fh:
                for line in fh:
                    try:
                        r = json.loads(line)
                    except ValueError:
                        continue
                    if r.get("role") != "assistant" or not r.get("tool_calls"):
                        continue
                    rt = _row_ts(r)
                    paths = [os.path.basename(p) for p in _write_paths(r)]
                    if rt is not None and paths:
                        rows.append((rt, paths))
            self._cache[f] = rows
        out = []
        for rt, paths in self._cache[f]:
            if -15 <= (rt - t).total_seconds() <= 5:
                out.extend(paths)
        return out


# ---------------------------------------------------------------------------
# §1.5 + §1.6
# ---------------------------------------------------------------------------


def _pairs(calls):
    by_sess = defaultdict(list)
    for c in calls:
        by_sess[c["session"]].append(c)
    for s, cs in by_sess.items():
        cs.sort(key=lambda c: c["t"])
        for i in range(len(cs) - 1):
            a, b = cs[i], cs[i + 1]
            gap = (b["t"] - a["t"]).total_seconds()
            if gap > 3600 or b["input"] < 20000:
                continue
            frac = b["cached"] / max(1, a["input"] + a["output"])
            yield s, a, b, gap, frac


def table_edit_position(calls, edits: _EditIndex) -> None:
    stats = defaultdict(lambda: defaultdict(lambda: [0, 0, 0]))
    baseline = []
    no_jsonl = 0
    for s, a, b, gap, frac in _pairs(calls):
        uncached = b["input"] - b["cached"]
        same_run = (b["iteration"] or 0) > (a["iteration"] or 0)
        pos = "mid-run" if same_run else "run-boundary"
        if not any(x in ("edit", "write") for x in a["tools"]):
            if frac >= 0.9:
                baseline.append(uncached)
            st = stats["(no edit)"][pos]
            st[0] += 1
            st[1] += frac < 0.5
            st[2] += uncached if frac < 0.5 else 0
            continue
        paths = edits.at(s, a["t"])
        if paths is None:
            no_jsonl += 1
            continue
        keys = [p for p in paths if p in L1_ASKS] or ["(other file)"]
        for k in set(keys):
            st = stats[k][pos]
            st[0] += 1
            st[1] += frac < 0.5
            st[2] += uncached if frac < 0.5 else 0
    base = statistics.median(baseline) if baseline else 0
    print("\n== §1.5 previous call edited a file → what the next call lost ==")
    print(f"calls without jsonl match: {no_jsonl}; baseline uncached (clean pairs, median) = {base:.0f}")
    print(f"{'prev call edited':22} {'position':13} {'pairs':>6} {'next lost>50%':>14} {'uncached tok on those':>22}")
    for k in list(L1_ASKS) + ["(other file)", "(no edit)"]:
        for pos in ("mid-run", "run-boundary"):
            n, nl, u = stats[k][pos]
            if n:
                print(f"{k:22} {pos:13} {n:6} {nl:6} ({100 * nl / n:3.0f}%) {u:14,}")


def table_no_edit_losses(calls) -> None:
    cat: Counter = Counter()
    tok: Counter = Counter()
    gaps: list[float] = []
    for s, a, b, gap, frac in _pairs(calls):
        if any(x in ("edit", "write") for x in a["tools"]):
            continue
        if (b["iteration"] or 0) <= (a["iteration"] or 0):
            continue
        if frac >= 0.5:
            continue
        tools = a["tools"]
        if not tools:
            k = "no tool (text only)"
        elif any(t.startswith("browser") for t in tools):
            k = "browser"
        elif "shell" in tools:
            k = "shell"
        elif any("agent" in t for t in tools):
            k = "sub-agent"
        else:
            k = ",".join(sorted(set(tools)))[:40]
        cat[k] += 1
        tok[k] += b["input"] - b["cached"]
        gaps.append(gap)
    print("\n== §1.6 (no edit) mid-run pairs that lost >50%, by the previous call's tools ==")
    for k, n in cat.most_common(12):
        print(f"  {k:40} {n:5}  uncached {tok[k]:>12,}")
    if gaps:
        gaps.sort()
        print(f"  gap median {gaps[len(gaps) // 2]:.0f}s, p90 {gaps[int(len(gaps) * .9)]:.0f}s, "
              f">300s: {sum(g > 300 for g in gaps)}")
    else:
        print("  (no pairs)")


# ---------------------------------------------------------------------------
# §1.7 — runs that ended without writing any memory file (JSONL only)
# ---------------------------------------------------------------------------


def _is_run_start(row: dict) -> bool:
    """A run begins at a user row or at a daemon event row that wakes the
    loop (a sub-agent terminal, an interaction request)."""
    if row.get("role") == "user":
        return True
    meta = row.get("_meta") or {}
    return row.get("role") == "system" and isinstance(meta, dict) and meta.get("event") in (
        "sub_agent_terminal", "interaction_required")


def table_runs_without_memory_writes(files: dict[str, str], *, since) -> None:
    runs = substantive = wrote_state = wrote_other = wrote_none = none_but_ws = 0
    record_turns = record_edited = 0
    for f in files.values():
        current: list[dict] | None = None

        def _close(run: list[dict]) -> None:
            nonlocal runs, substantive, wrote_state, wrote_other, wrote_none, none_but_ws
            nonlocal record_turns, record_edited
            if not run:
                return
            tool_calls = 0
            mem: set[str] = set()
            ws_changed = False
            had_record = any((r.get("_meta") or {}).get("event") == "memory_record" for r in run)
            record_wrote = False
            for r in run:
                if r.get("role") != "assistant":
                    continue
                tool_calls += len(r.get("tool_calls") or [])
                for p in _write_paths(r):
                    base = os.path.basename(p)
                    norm = p.replace("\\", "/").lstrip("./")
                    if base in L1 and ("orbital/" in norm or norm == base):
                        mem.add(base)
                        if r.get("_kind") == "memory_record":
                            record_wrote = True
                    elif "orbital/" not in norm:
                        ws_changed = True
            runs += 1
            if had_record:
                record_turns += 1
                record_edited += record_wrote
            if tool_calls < 8 and not ws_changed:
                return
            substantive += 1
            if "PROJECT_STATE.md" in mem:
                wrote_state += 1
            elif mem:
                wrote_other += 1
            else:
                wrote_none += 1
                none_but_ws += ws_changed

        with open(f, errors="replace") as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("role") == "meta":
                    continue
                if since is not None:
                    rt = _row_ts(r)
                    if rt is not None and rt < since:
                        continue
                if _is_run_start(r) and r.get("_kind") != "memory_record":
                    _close(current or [])
                    current = [r]
                elif current is not None:
                    current.append(r)
        _close(current or [])
    print("\n== §1.7 substantive runs (>=8 tool calls or a workspace file changed) ==")
    print(f"{'runs (all)':44} {runs:6}")
    print(f"{'substantive runs':44} {substantive:6}")
    print(f"{'  wrote PROJECT_STATE':44} {wrote_state:6}")
    print(f"{'  wrote only other memory files':44} {wrote_other:6}")
    print(f"{'  wrote no memory file':44} {wrote_none:6}"
          + (f"  ({100 * wrote_none / substantive:.0f}%)" if substantive else ""))
    print(f"{'    of which changed workspace files':44} {none_but_ws:6}")
    print(f"{'runs with a [MEMORY] record turn (spec 112)':44} {record_turns:6}")
    print(f"{'  record turn edited a memory file':44} {record_edited:6}")


# ---------------------------------------------------------------------------


def main(argv=None) -> int:
    args = _parse_args(argv)
    since = _since(args.since)
    logs = _log_files(args.logs)
    workspaces = _workspaces(args.projects_json, args.workspace)
    files = session_files(workspaces)
    audits, calls = read_logs(logs, since=since, model_prefix=args.model_prefix,
                              caller=args.caller)
    print(f"logs={len(logs)} audits={len(audits)} aligned calls={len(calls)} "
          f"workspaces={len(workspaces)} sessions={len(files)}"
          + (f" since={args.since}" if args.since else ""))
    if calls:
        first_t = min(c["t"] for c in calls)
        last_t = max(c["t"] for c in calls)
        print(f"audit window {first_t:%Y-%m-%d} .. {last_t:%Y-%m-%d}")
    table_edit_position(calls, _EditIndex(files))
    table_no_edit_losses(calls)
    table_runs_without_memory_writes(files, since=since)
    return 0


if __name__ == "__main__":
    sys.exit(main())
