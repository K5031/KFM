# Philosophy

Where KFM is going, and the principles it is built on. This is direction, not a
description of what exists today: the [README](README.md) and the
[documentation](docs/README.md) say what is there.

## The goal

KFM is to achieve grand, abstract tasks with respectable quality, autonomously, without
human supervision, within the budgets and permissions a human sets. A fleet should be
capable of the impact of a real team: a marketing team, a development team, or any other
function.

## The unit

A fleet is made of agents, and the agent is [Kadence](https://github.com/K5031/Kadence).
What a fleet asks of its unit:

- It can be started headless, with no screen and no person in front of it.
- It works on its own, until its work is done or indefinitely, depending on what it was
  set to.
- It reports back to whoever started it.
- It respects the budget it is given.
- It can be paused or stopped at any time.

Agents do not talk to each other directly. KFM carries everything between them.

## The hierarchy

Agents are ranked K1 to K5. An agent supervises only the rank below it: K5 supervises K4,
K4 supervises K3, and so on. The ranks are an upper limit, not a requirement: a fleet
uses only as many tiers as the work needs.

Each rank can run on its own model, and so can each team.

A human says roughly what they want. The agent at the top judges the work and decides
how to meet it: do it itself, or hire agents below it, who may hire below themselves.
The structure can also be set by the human.

## What a fleet is given

- **A task** is something to achieve. It ends when it is achieved.
- **A duty** is a role with no end, such as "be the marketing team". The fleet finds its
  own work and keeps going until a human says otherwise.
- **A build** is large work done fast by many agents, to a plan the human approved
  first. It is the middle between saying exactly what to do and stepping away entirely.

## A project

Agents are grouped into teams. In a business, the teams would be Marketing, Development,
PR, HR and the like. A project is such an organisation: several teams side by side, each
with its own lead, limit and work.

The human is the top of a project. Its secretary is the one agent they talk to about the
whole of it: she maps out the teams, says how things stand, and passes on what the human
wants changed. She manages nobody. Team leads can reach each other and work things out
between them.

## How agents are treated

- **Abilities, facts and structure, not rules.** An agent is given what it can do, what
  is true of its situation, and a shape to work in. It is not told how to work. Rules
  cap what a capable model does by itself, have side effects, and go stale as models
  improve.
- **KFM is not an agent.** It carries messages, holds limits and keeps the record. It
  does not plan, hire, check or decide: that is the agents' work, and code that does it
  in their place is fixed behaviour for cases nobody foresaw.
- **General before special.** A new need is met with a mechanism that already exists
  before it gets an option, a tool or a mode of its own.
- **Permission is asked when it is needed.** An agent works freely in the folder it was
  given and asks before anything outside it. Actions with real-world effect, such as
  publishing, payments and outbound messages, need a human's approval unless it was
  granted.
- **The human decides what is spent.** Every fleet and every agent has a limit, and
  reaching it stops the work.

## What is still ahead

- A team that keeps what it learns, so that a new agent does not start from nothing.
- Workers that live for one task, with the lead as the one that holds the direction.
- The build described above.
- Long runs with real models: a fleet is judged by a duty kept over weeks, not by one
  task done once.
