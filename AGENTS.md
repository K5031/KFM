# KFM

Kayden Fleet Manager: runs teams of Kayden agents toward a goal or a duty, without
supervision. Read before coding:
- `PHILOSOPHY.md`: what the fleet is for (ranks K1 to K5, teams, goals and duties).
- `.agents/notes/`: the design decisions, as Agent Notes.
- Kayden (`../Kayden`): the unit. KFM uses only its headless channel
  (`docs/headless.md`), its client for it (`kayden.frontends.remote`), its configuration
  files and its MCP support.

## Stack
Python 3.14 (`requires-python >= 3.12`), asyncio, uv, pytest + pytest-asyncio, ruff. The
`mcp` SDK serves the fleet tools.

## Rules
- Define expected behavior as a test before implementing it.
- A unit is an unchanged Kayden process. Fleet concepts (ranks, teams, supervisors) live
  here, never in Kayden. If KFM needs something from Kayden, it is a general Kayden
  feature or it does not exist.
- The manager bridges all communication: units never talk to each other directly.
- No speculative abstractions, no features the owner has not asked for, no unrequested
  refactoring.
- No migration or compatibility code. A fleet folder of another format is refused.
- Prefer established libraries over custom mechanisms.
- Never block the event loop.
- Tests never use a real model or the real `~/.kayden` and `~/.kfm`.

## Records
- Design decisions: Agent Notes in `.agents/notes/{proposed,implemented,rejected}/`, in
  Kayden's format (Problem, Decision, Alternatives considered, Consequences). Small
  choices go in `.agents/decisions.md`.
- `docs/` is the user wiki: plain language, by what a reader wants to do. Update it in the
  same change as user-facing behavior.

## Code style
Professional, typed, concise. Comments only where needed to understand the code.

## Commands
- `uv run pytest`
- `uv run ruff check`
