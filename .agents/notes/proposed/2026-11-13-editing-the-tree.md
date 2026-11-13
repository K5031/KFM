# Agent Note: Editing the tree

Status: proposed — the person adds agents to a fleet, sets each one's model and moves them, on the chart

Follows [Projects](../implemented/2026-11-13-projects.md) and
[drafting a project](../implemented/2026-11-13-drafting-a-project.md).

## Problem

Inside a fleet the person decides nothing: the lead hires whom it wants, on the model its
rank is given in a settings file. A person who knows the team they want cannot build it,
move an agent under another supervisor, or give one agent a better model.

## Proposal

On the chart (the user, 2026-11-13) the person adds agents under a lead or under any
agent, moves an agent to another supervisor by dragging it, and sets each agent's model.
A lead may still hire below what the person placed.

Order: adding an agent and its model, then moving.

## Alternatives considered

None yet beyond leaving it to the leads, which is what KFM does today.

## Acceptance criteria

- An agent added by hand appears under the supervisor chosen, and that supervisor knows
  it has it.
- A moved agent reports to its new supervisor; both supervisors are told.
- An agent's model is set on the chart and holds after a resume.

## Risks

- **Moving an agent in a running fleet** changes whom it reports to and its rank, and the
  ranks of everyone under it; a move that would push an agent below K1 is refused. Moving
  one to another fleet means another manager and limit.
- A lead that hires and a person who places agents can build two teams for one job.
