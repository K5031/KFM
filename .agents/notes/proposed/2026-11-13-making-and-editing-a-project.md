# Agent Note: Making and editing a project

Status: proposed — a project drafted from a description, and its tree edited by hand

Follows [Projects](../implemented/2026-11-13-projects.md).

## Problem

A project is made empty and its fleets are added one at a time, each with its name and
what it does written by the person. Someone who knows what they want ("run my game
studio") has to work out the departments themselves first.

Inside a fleet the person decides nothing: the lead hires whom it wants, on the model its
rank is given in a settings file. A person who knows the team they want cannot build it,
move an agent under another supervisor, or give one agent a better model.

## Proposal

Two ways to make a project (the user, 2026-11-13).

**Described.** The person types what they want. One agent drafts the organisation: which
fleets, each one's name and what it is there to do. The draft is shown as a chart the
person can change, and nothing starts until they say so. The drafting agent then ends: it
is not a lead over the fleets, since the person is the top. It does not size the fleets;
each lead hires as its work needs.

**By hand.** On the chart the person adds agents under a lead or under any agent, moves
an agent to another supervisor by dragging it, and sets each agent's model. A lead may
still hire below what the person placed.

Order: the described way, then adding an agent and its model, then moving.

## Alternatives considered

- **The described way makes one fleet only**, to avoid waste. A draft costs one request;
  the waste is a fleet that runs without being needed, which the person's go-ahead
  prevents. A limit of one is a rule for a case: right for "be my marketing team", wrong
  for a company.
- **The drafting agent stays as a lead over the fleets.** The one-fleet company the
  projects note rejected: it stands between the person and every department.

## Acceptance criteria

- A description yields a draft of named fleets on the chart, and no agent runs before the
  person starts it.
- An agent added by hand appears under the supervisor chosen, and that supervisor knows
  it has it.
- A moved agent reports to its new supervisor; both supervisors are told.
- An agent's model is set on the chart and holds after a resume.

## Risks

- **Moving an agent in a running fleet** changes whom it reports to and its rank, and the
  ranks of everyone under it; a move that would push an agent below K1 is refused. Moving
  one to another fleet means another manager and limit.
- **A draft nobody reads.** The described way guards against waste only if the person
  looks at the draft before starting it.
- A lead that hires and a person who places agents can build two teams for one job.
