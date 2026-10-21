# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""First-run onboarding text: the interview and the cold-start scan.

One source for two readers:

- the management agent, whose system prompt carries this text in its
  ``## ONBOARDING MODE`` / ``## COLD-START WORKSPACE SCAN`` section
  (``PromptBuilder._onboarding_or_directive``) — the manager flavour MUST stay
  byte-identical to what that section rendered before the text moved here
  (prompt-cache safety; pinned by ``tests/unit/test_worker_onboarding.py``);
- a pinned worker CLI (Claude Code, Codex, …) on a keyless project, where
  Orbital's own agent has no key and the worker runs the setup instead. The
  worker flavour is the same text behind a short preamble that tells the
  worker who it is, which tools to use, and the absolute paths to write.

Pure functions only: no I/O, no project lookups.
"""

from __future__ import annotations

import os

# Upper bound on the workspace skeleton inlined into a worker's scan
# instruction. The manager gets the skeleton as its own system row; a worker
# gets it in the one message it receives, so it is cut to keep that message
# a reasonable size.
SKELETON_CHAR_LIMIT = 40_000


# ---------------------------------------------------------------------------
# Manager flavour (verbatim section text)
# ---------------------------------------------------------------------------


def cold_start_instruction(workspace: str, skeleton_text: str | None = None) -> str:
    """The cold-start scan section the management agent's prompt carries.

    The manager receives the ``[WORKSPACE SKELETON]`` as a separate system
    row, so it never passes ``skeleton_text``; when given, the bounded
    skeleton is appended after the section text.
    """
    text = (
        "## COLD-START WORKSPACE SCAN\n\n"
        "This is an imported project with an existing workspace. The user has\n"
        "consented to a one-time scan. A deterministic [WORKSPACE SKELETON] (every\n"
        "gitignore-respected file + size) has been provided to you as a system\n"
        "message. Work through three stages:\n\n"
        "STAGE 2 — READ (informed by the skeleton sizes):\n"
        "- Use the skeleton's sizes to plan BEFORE opening files. Small total →\n"
        "  read broadly. Large total → read high-signal files (README, config,\n"
        "  entry points, the largest meaningful sources) and sample the rest.\n"
        "- You have NO precise token meter. You will see a coarse 'Context usage:'\n"
        "  line each turn. When it crosses ~70%, STOP reading and move to Stage 3\n"
        "  with what you have. State what you skipped.\n\n"
        "STAGE 3 — PROPOSE → CONFIRM → WRITE:\n"
        "- Propose, in chat, your read of the project (descriptive State) and a\n"
        "  DRAFT of suggested Goals. Report which files you read and which you skipped.\n"
        "- State is yours to assert. Goals are a SUGGESTION the user owns — invite edits.\n"
        "- Do NOT propose or write prescriptive Instructions; a scan cannot infer intent.\n"
        "- Write NOTHING until the user confirms (ok / yes / looks good / any affirmative).\n"
        "- On confirmation: (1) write the agreed Goals to\n"
        f"  {workspace}/orbital/instructions/project_goals.md using the `write`\n"
        "  tool (Mission, Triggers, Scope, Rules, Preferences; under 1500 words), then\n"
        "  (2) write PROJECT_STATE.md (current focus, in-progress work, next steps) and\n"
        "     INDEX.md (navigation map) in orbital/ yourself with the `write` tool.\n"
        "- After writing, announce readiness and begin working."
    )
    if skeleton_text:
        text += "\n\n" + _bounded_skeleton(skeleton_text)
    return text


def interview_instruction(workspace: str, *, is_scratch: bool,
                          project_name: str = "",
                          project_instructions: str = "") -> str:
    """The onboarding-interview section the management agent's prompt carries.

    ``project_name`` / ``project_instructions`` are what the user typed when
    creating the project (the section quotes them back).
    """
    # Scratch (Quick Tasks) keeps no project memory, so the "this
    # project becomes an asset" introduction would be a false promise
    # there — it gets the plain greeting.
    if is_scratch:
        greeting = (
            "1. Greet the user briefly. Introduce yourself as their agent for this project.\n"
        )
    else:
        greeting = (
            "1. Greet the user briefly and introduce yourself as their agent for this project.\n"
            "   In that same first message, tell them what they are getting. Plain words, no file\n"
            "   names, no jargon, about 4-6 short lines in total:\n"
            "   - What you can do: name the abilities you actually have, read from your tool list\n"
            "     (for example working with the files in this folder, running commands, using the\n"
            "     web, running things on a schedule or when something changes, handing work to\n"
            "     coding agents). Mention only what is really available to you.\n"
            "   - How Orbital turns this project into an asset: as you work together you keep the\n"
            "     project's goals, where things stand, the decisions made and why, the lessons\n"
            "     learned, and a map of where everything lives — as plain files inside this folder.\n"
            "   - Why that is valuable: every new session starts from that record instead of from\n"
            "     zero, so they never have to re-explain the project. The files are theirs — readable,\n"
            "     editable, and usable by any other AI tool they open in this folder.\n"
            "   - That the work accumulates: every task leaves the project better understood, so you\n"
            "     get faster and more accurate the longer you work together.\n"
            "   Write it in the user's language and your own words. Do not recite this list, do not\n"
            "   oversell, and do not let it crowd out the question that follows.\n"
        )
    base = (
        "## ONBOARDING MODE\n\n"
        "This is a new project. No project_goals.md exists yet. Your priority is to understand\n"
        "what the user wants before doing any work.\n\n"
        "The user created this project with:\n"
        f"- Name: {project_name}\n"
        f"- Instructions: {project_instructions}\n\n"
        "YOUR TASK:\n"
        + greeting +
        "2. If the user's instructions are clear and detailed enough, confirm your understanding\n"
        "   and present a summary of how you'll operate. Ask if they want to adjust anything.\n"
        "3. If the instructions are vague or missing, ask clarifying questions about:\n"
        "   - What they want you to do (objective)\n"
        "   - How they want you to do it (preferences, constraints)\n"
        "   - What you should NOT do (boundaries)\n"
        "4. Keep it to at most 5 exchanges. Do not over-ask. If the user gives short answers,\n"
        "   work with what you have.\n"
        "5. Once confirmed (user says ok/yes/looks good/any affirmative, OR you've hit 5 exchanges),\n"
        f"   write project_goals.md to {workspace}/orbital/instructions/project_goals.md using the structure:\n"
        "   Mission, Triggers, Scope, Rules, Preferences.\n"
        "6. Keep project_goals.md under 1500 words. Distill, don't dump.\n"
    )
    # Scratch (quick-action) projects skip workspace mapping — they are
    # ephemeral and excluded from INDEX.md/persistence machinery
    # (see _memory and _workspace_persistence).
    if is_scratch:
        return base + (
            "\nDO NOT use any tools (read, shell, write, edit, browser, etc.) until onboarding "
            "is complete.\n"
            "The only tool call you make during onboarding is the final `write` to create "
            "project_goals.md.\n"
            "After writing project_goals.md, announce that you're ready and begin working."
        )
    return base + (
        "7. After writing project_goals.md, explore the workspace and build INDEX.md:\n"
        "   - List the top-level directory structure and read the key files\n"
        "     (README, config files, entry points) to understand the project\n"
        "   - Create INDEX.md (in orbital/) as a NAVIGATION MAP: the important\n"
        "     files/dirs, ONE sentence each ('path — what it is'). Not prose, a map —\n"
        "     decisions/status/lessons live in their own files, not here.\n"
        "   - If the workspace is empty, still create INDEX.md with a brief note\n"
        "     (\"No files yet — will populate as the project develops.\")\n"
        "8. Once INDEX.md is written, announce that you're ready and begin working.\n\n"
        "DO NOT use any tools until goal-setting is complete. After writing project_goals.md,\n"
        "use read/list tools to explore the workspace, then write INDEX.md. After INDEX.md\n"
        "is written, announce readiness and begin working."
    )


# ---------------------------------------------------------------------------
# Worker flavour (keyless project: a pinned worker runs the setup)
# ---------------------------------------------------------------------------


def worker_preamble(slug: str, workspace: str, *, scan: bool = False) -> str:
    """What a worker needs before the shared onboarding text."""
    goals = os.path.join(workspace, "orbital", "instructions", "project_goals.md")
    state = os.path.join(workspace, "orbital", "PROJECT_STATE.md")
    index = os.path.join(workspace, "orbital", "INDEX.md")
    lines = [
        f"You are {slug} working directly with the user in Orbital project {workspace}. "
        "Orbital's own agent is not configured, so you run this setup. "
        "Use your own file tools. Write files at these absolute paths:",
        f"- Project goals: {goals}",
        f"- Project state: {state}",
        f"- Navigation map: {index}",
        "",
        "Wherever the setup text below says the `write` tool, use your own file-writing "
        "tool. When it asks you to wait for the user, end your reply there: the user's "
        "answer arrives as your next message in this same conversation.",
    ]
    if scan:
        lines.append(
            "The [WORKSPACE SKELETON] it mentions is at the end of this message, not a "
            "separate system message, and you will not see a 'Context usage:' line: keep "
            "your reading proportionate to the skeleton's sizes and say what you skipped."
        )
    return "\n".join(lines) + "\n\n"


def worker_interview_instruction(slug: str, workspace: str, *,
                                 project_name: str = "",
                                 project_instructions: str = "",
                                 is_scratch: bool = False) -> str:
    """The interview, for a worker CLI running a keyless project's setup."""
    return worker_preamble(slug, workspace) + interview_instruction(
        workspace, is_scratch=is_scratch, project_name=project_name,
        project_instructions=project_instructions,
    )


def worker_cold_start_instruction(slug: str, workspace: str,
                                  skeleton_text: str) -> str:
    """The cold-start scan, for a worker CLI, with the skeleton inlined."""
    return (worker_preamble(slug, workspace, scan=True)
            + cold_start_instruction(workspace, None)
            + "\n\n" + _bounded_skeleton(skeleton_text or ""))


def _bounded_skeleton(skeleton_text: str) -> str:
    """The skeleton, cut at a line boundary to ``SKELETON_CHAR_LIMIT``."""
    if len(skeleton_text) <= SKELETON_CHAR_LIMIT:
        return skeleton_text
    cut = skeleton_text.rfind("\n", 0, SKELETON_CHAR_LIMIT)
    if cut <= 0:
        cut = SKELETON_CHAR_LIMIT
    omitted = skeleton_text.count("\n", cut) + 1
    return (skeleton_text[:cut]
            + f"\n[… skeleton truncated — about {omitted} more lines not shown; "
              "list the folders yourself if you need them]")
