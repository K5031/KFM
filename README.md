# KFM

Kadence Fleet Manager runs teams of [Kadence](https://github.com/K5031/Kadence) agents that
work without supervision. You say what you want; a lead agent hands parts of the work to
agents below it, checks what comes back and reports to you. A project holds several such
teams and a secretary you talk to about all of them. Everything stays inside the spending
limits and permissions you set.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/images/window-dark.png">
  <img src="docs/images/window-light.png" alt="The KFM window on a project with three running fleets, one of them a tree of six agents">
</picture>

A project's teams in the [window](docs/README.md#the-window): who supervises whom, what
each agent is doing and what it has spent.

## What it does

- **A hierarchy that sizes itself.** A lead hires agents one rank below it as the work
  needs, and they hire below themselves. Reports and questions go up; only what a lead
  cannot settle reaches you.
- **A task, or continuous.** A task ends with the lead's report. A continuous team does
  standing work with no end ("be the support team"): the team finds its own work and keeps at it.
- **Projects with a secretary.** Describe a project and its secretary proposes the
  teams. Afterwards she is the one agent you ask how things stand or tell what to
  change; team leads can also write to each other.
- **Limits you can rely on.** A spending limit for each fleet and each agent. Agents
  work freely in their folder, inside a sandbox, and ask you before anything outside it.
- **Watch it work.** Each agent's reasoning, the tool it is using and the message it is
  writing show as they happen.
- **Runs in the background.** Fleets belong to a service that outlives the window and
  the terminal. Stop a fleet and resume it later, with every agent's conversation kept.

It is an early version, built and tested mostly against scripted agents.

## Quick start

Requires Linux, Python 3.12+ and [uv](https://docs.astral.sh/uv/). KFM runs Kadence
agents, so Kadence is checked out beside it and needs a model provider and a key (see
[its quick start](https://github.com/K5031/Kadence#quick-start)).

```bash
git clone https://github.com/K5031/Kadence.git
git clone https://github.com/K5031/KFM.git
cd KFM
uv tool install --editable ".[desktop]"
export ANTHROPIC_API_KEY=sk-ant-...
kfm-desktop
```

In the window, make a project, give it a folder and say what it should do. Or from a
terminal, in the folder the agents should work in:

```bash
kfm run "Audit this repository's docs and fix what is out of date" --budget 5
```

Agents can change anything in the folder they are given, so give them one made for the
work, not your home folder.

## How it works

Each agent is a separate Kadence process with its own conversation, memory and limit. It
knows only its task and what is reported to it. KFM carries messages between agents,
holds the limits and keeps the record of what happened; it decides nothing about the
work. What an agent is told are facts about where it stands and what its tools do, not
instructions on how to work.

[PHILOSOPHY.md](PHILOSOPHY.md) says where this is going.

## Documentation

The [documentation](docs/README.md) covers installing, running fleets from the terminal,
the window, settings, and what is not there yet.

## License

MIT; see `LICENSE`.
