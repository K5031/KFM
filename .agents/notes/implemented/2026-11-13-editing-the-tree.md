# Agent Note: Editing the tree

Status: implemented — on the chart the person adds an agent under any agent and sets an agent's model

Follows [Projects](2026-11-13-projects.md) and
[drafting a project](2026-11-13-drafting-a-project.md).

## Problem

Inside a fleet the person decided nothing: the lead hired whom it wanted, on the model
its rank is given in a settings file. A person who knows the team they want could not
build it, or give one agent a better model.

## Decision

A right-click on an agent of the chart offers two things, while its fleet runs.

- **Add agent**: a name, a task (or a duty) and, if it should not run on its rank's, a
  model. The agent is made one rank below the one clicked, as if that one had delegated
  it, and starts at once (`Manager.hire`). Its supervisor is told it has it, what its
  task is and how to answer it, so reports and questions go where they would for an
  agent it hired itself. A lead may still hire below what the person placed.
- **Model**: the model the agent runs on (`Unit.model`, kept in the fleet's record); it
  wins over the team's and the rank's, and empty returns the agent to those. An agent at
  work is started again on it at once, in its own conversation, and told so
  (`Manager.set_model`).

The model is typed as its id: what models exist is the harness's knowledge, and the unit
contract has no way to ask.

## Alternatives considered

- **Moving an agent to another supervisor by dragging it** (asked for first, then
  dropped by the user, 2026-11-13). It changes the agent's rank and that of everyone
  under it, has to be refused where that would go below K1, both supervisors and the
  agent have to be told in the middle of their work, the agent's standing instruction
  still names its old supervisor, and across fleets it means another manager and limit.
  Stopping the agent and adding one in the other place does the same.
- **The model takes effect the next time the agent's process starts.** Nothing is
  interrupted, but an agent on a duty may never start again, and the person who set a
  model would see nothing change.

## Consequences

- Setting the model of an agent at work interrupts its turn; what it ran in the
  background ends, as on a resume.
- A lead that hires and a person who places agents can build two teams for one job.
- An agent cannot be added to or changed in a fleet that is stopped.
- A model id that the harness does not know fails the agent when it starts; the chart
  shows it failed.
- The menu is reached by right-click only; nothing on an agent's box shows it is there.
- Checked on the second harness. Not checked: with real Kayden units, so that a
  conversation carries on under another model is taken from Kayden, not seen here.
