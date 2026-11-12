# Agent Note: A fleet of headless units, bridged by the manager

Status: implemented — the manager runs every unit as an unchanged headless Kayden and carries all communication; agents reach the fleet through MCP tools

## Problem

PHILOSOPHY.md asks for teams of Kayden agents in ranks, each supervising the rank below,
that take a rough task from a person and finish it without supervision, inside budgets
and permissions. Kayden already runs headless, works on a goal or a duty by itself,
reports, asks for help and respects a budget. What is missing is everything between
units: who starts whom, how a report or a question travels, who enforces the hierarchy
and the fleet's spending, and how the person follows it. Kayden must stay free of fleet
concepts, so none of this can be added to it.

## Decision

- **A unit is `kayden --headless`** in its own data folder (`<fleet>/units/<id>/`). KFM
  writes that folder's `config.toml` from a base Kayden configuration (the provider and
  its options) with the unit's model, spending limit and permission level, and its
  `settings.toml` with the work folder and one MCP server, the fleet tools. Every unit
  works in the same folder.
- **The manager (`kfm.manager`) owns every process** and drives each over Kayden's
  headless channel with Kayden's own client (`kayden.frontends.remote.Channel`). The
  process tree is flat; the hierarchy is data (`kfm.fleet`).
- **A unit's task is its duty**: a goal (ends with `finish_goal` and a report) or standing
  work. The instruction is a brief (`units.brief`): the unit's name and rank, its task,
  and how the fleet works from where it stands.
- **Up the hierarchy, the manager carries what Kayden already emits.** A unit that
  finishes its goal, asks for help, fails or stops ends a turn; the manager turns that
  into a message in its supervisor's conversation (`[Report from Scout (K4, u2)] ...`),
  which wakes the supervisor. For the lead, whose supervisor is the person, it becomes a
  fleet event the terminal shows.
- **Down the hierarchy, agents use the fleet tools**, an MCP server in the manager
  (`kfm.tools`) that every unit connects to with its own token: `delegate` (a new agent
  one rank below), `message` (answer or redirect one of its agents, starting it again if
  it had stopped), `status`, `stop` (an agent and everything under it) and `report` (an
  update that does not end the work). The manager enforces the rules: one rank down,
  direct subordinates only, the lowest rank cannot delegate, a limit on the number of
  agents.
- **Spending** has two limits: each unit's own, enforced by its Kayden, and the fleet's,
  which the manager enforces from every unit's usage events by stopping all of them.
- **Permissions**: units run with `allow_workspace`. The manager grants the fleet tools
  itself; any other call a unit's permissions do not cover goes to the person as an
  approval.
- **The person** uses `kfm run "<task>"`: the fleet's activity as lines, what they type
  goes to the lead, `y`/`n` answers an approval. `fleet.json` records the units;
  `kfm status` prints it.

## Alternatives considered

- **Let each agent start its own children**, as Kayden's sub-sessions do. No manager is
  needed, but the hierarchy is then the process tree: nothing sees the whole fleet, a
  fleet-wide budget cannot be enforced, and a supervisor that dies takes its subtree with
  it. PHILOSOPHY.md already leans toward bridging.
- **A KFM frontend module inside each unit** that offers the fleet tools
  (`Frontend.tools`) and speaks to the manager over the unit's stdin and stdout. No HTTP,
  but it needs requests from child to parent on a channel built for the other direction,
  and it ties KFM to Kayden's internals instead of two documented surfaces (the headless
  channel and MCP servers).
- **A blocking `delegate`** that returns the report, as `start_session` does. Simple for
  one task, but a tool call held open for hours over HTTP is fragile, and a supervisor
  could not work or delegate more meanwhile.
- **One fleet tool server per unit, or a stdio relay process per unit**, to tell callers
  apart. A token in the URL does it with one server.

## Consequences

- Kayden needed no change, and a unit can be any Kayden version that speaks the headless
  channel.
- Checked with real models (2026-11-12): a K2 lead on Claude Opus 5.5 was asked for a
  three-line haiku in a file, written by an agent below it and checked by itself. It
  delegated to one K1 agent on Claude Sonnet 5.5, waited for its report, checked the file
  and reported to the person, in 30 seconds and without a prompt. The fleet spent $0.36:
  $0.27 the lead, $0.09 the agent that did the work.
- Everything passes through one process, so the fleet can be shown, limited and stopped
  from one place. If the manager dies, every unit quits (its stdin closes).
- A supervisor learns of its agents only through messages, so it costs a model request to
  read each report. That is the price of agents that see nothing of each other's context.
- Agents share one folder and can step on each other's files; the brief tells them to
  name file ownership in each task, and nothing enforces it.
- Not built: resuming a fleet after the manager stops, more than one fleet at a time, a
  supervising agent per team beyond the rank tree, prescribed fleet structures, approvals
  decided by a supervisor instead of the person, an interface beyond the terminal.
