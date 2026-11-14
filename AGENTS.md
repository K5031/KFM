# KFM

Kayden Fleet Manager: runs teams of Kayden agents toward a goal or a duty, without
supervision. Read before coding:
- `PHILOSOPHY.md`: what the fleet is for (ranks K1 to K5, teams, goals and duties).
- `.agents/notes/`: the design decisions, as Agent Notes.
- `src/kfm/service.py`: the frontend contract, normative: everything a person can do with
  KFM. `local.py` is the service in a process, `server.py` and `remote.py` carry it over
  a socket, and the window (`desktop.py`) and the command line (`__main__.py`) are
  frontends of it.
- `src/kfm/api.py`: the unit contract, normative: what a fleet needs from the agent
  program its units run.
- Kayden (`../Kayden`): the built-in unit. `src/kfm/harnesses/kayden.py` uses its
  headless channel (`docs/headless.md`), its client for it (`kayden.frontends.remote`),
  its configuration files and its MCP support.

## Stack
Python 3.14 (`requires-python >= 3.12`), asyncio, uv, pytest + pytest-asyncio, ruff. The
`mcp` SDK serves the fleet tools.

## Rules
- Define expected behavior as a test before implementing it.
- A frontend knows KFM only through `kfm/service.py`: it never imports the manager, and
  a feature is added to the contract first, so that every frontend can have it.
- The manager knows a unit only through `kfm/api.py`. Whatever belongs to one agent
  program lives in its harness (`src/kfm/harnesses/`); a change the manager needs from
  units is a change to the contract, with the second harness in `tests/fakeharness.py`
  kept passing.
- A Kayden unit is an unchanged Kayden process. Fleet concepts (ranks, teams,
  supervisors) live here, never in Kayden. If KFM needs something from Kayden, it is a
  general Kayden feature or it does not exist.
- The manager bridges all communication: units never talk to each other directly.
- No speculative abstractions, no features the user has not asked for, no unrequested
  refactoring.
- No migration or compatibility code. A fleet folder of another format is refused.
- Prefer established libraries over custom mechanisms.
- Never block the event loop.
- Tests never use a real model or the real `~/.kayden` and `~/.kfm`.

## Documentation
`docs/` is the user wiki: plain language, by what a reader wants to do. Update it in the
same change as user-facing behavior. Working records (Agent Notes, decisions) go in
`.agents/`, never in `docs/`.

## Agent Notes
Design decisions live in `.agents/notes/` as **Agent Notes**: the why and what was given up, which code and docs can't carry.

### When to write one
- Only for lasting decision rationale that code, tests and docs don't explain. Skip mechanical or local changes; small choices go in `.agents/decisions.md`.
- For a new feature or a change of approach, write the proposed note before coding.
- If a note already owns the decision, update it. Don't duplicate.
- Before changing architecture, adding a dependency or switching approach, search the notes. If the change contradicts an accepted decision, flag it and ask.

### Layout
`.agents/notes/{status}/yyyy-mm-dd-topic-title.md`, where the date is when the topic was first proposed.
- `proposed/`: not built yet.
- `implemented/`: shipped. Kept current with the code.
- `rejected/`: declined. Keep only if it prevents a tempting mistake, otherwise delete.

The folder is the status. Moving a note means updating its `Status:` line, rewriting it to the new skeleton, and fixing every link to it, in the same change. Link between notes with relative markdown links. No index file.

### Format
```markdown
# Agent Note: <title>

Status: proposed | implemented | rejected — <one-line reason>

## Problem
## Decision          (proposed: "## Proposal")
## Alternatives considered
## Consequences      (proposed: "## Acceptance criteria" and "## Risks")
```
- `## Problem` is the motivation, readable without the solution.
- `## Decision` is present tense and describes what shipped.
- `## Alternatives considered` is mandatory: each real alternative and why it lost. Never invent alternatives.
- `## Consequences` records what the trade-off cost and what it bought.
- Add bespoke technical sections between these as needed.

### Keeping notes current
- When code changes a fact an implemented note states (path, name, default), update the note in the same change. Rewrite in place, don't append history.
- Never edit a note into a different decision. Write a new note, link both ways, and set the old one's status accordingly.
- State current facts only. No "previously" or "no longer", no commit or PR history.

## Code style
Professional, typed, concise. Comments only where needed to understand the code.

## Commands
- `uv run pytest`
- `uv run ruff check`
