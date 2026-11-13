# Agent Note: Drafting a project

Status: implemented — a project described in words is drafted by one agent as named fleets, which the person changes and starts

Follows [Projects](2026-11-13-projects.md). The other way to shape a project:
[editing the tree](../proposed/2026-11-13-editing-the-tree.md).

## Problem

A project is made empty and its fleets are added one at a time, each with its name and
what it does written by the person. Someone who knows what they want ("run my game
studio") has to work out the departments themselves first.

## Decision

A new project takes an optional description of what it should do (the user,
2026-11-13). With one, an agent drafts the organisation (`draft.py`): which fleets, each
one's name, what it is there to do, and whether that is a duty or a task.

- **The drafting agent is a unit like any other**, opened through the fleet's harness
  with a brief of its own (`draft.brief`) and one tool, `propose`, served to it where a
  fleet's unit finds the fleet tools. The proposal arrives as the tool's arguments, so
  its shape is checked and nothing is read out of prose.
- **It ends with its proposal.** It is no lead over the fleets, since the person is the
  top. An approval it asks for is refused; ending, failing or asking a question without
  a proposal is reported as no draft.
- **It does not size the fleets**; each lead hires as its work needs. It is told to
  propose as few teams as do the job.
- **The draft is a page of the window**: a row for each fleet with its name, what it
  does, Task or Duty and a way to leave it out, a button to add one, the limit each fleet
  gets, Start and Discard. Nothing runs until Start; it then starts every fleet and
  shows the chart. Discard, or no draft, leaves the project to be filled by hand.

## Alternatives considered

- **The described way makes one fleet only**, to avoid waste. A draft costs one agent's
  turn; the waste is a fleet that runs without being needed, which the person's go-ahead
  prevents. A limit of one is a rule for a case: right for "be my marketing team", wrong
  for a company.
- **The drafting agent stays as a lead over the fleets.** The one-fleet company the
  projects note rejected: it stands between the person and every department.
- **Read the proposal from the agent's report**, as JSON or a list. No tool to serve, but
  a report is prose: its shape is whatever the model wrote that time.

## Consequences

- The harness contract is unchanged: a drafting agent needs only what a unit has.
- What the drafting agent spent is shown on the draft and counted nowhere else: it
  belongs to no fleet's limit. It may spend up to one agent's limit (`unit_budget_usd`).
- A draft lives in the window: closing the window, or the agent's process, loses it, and
  the project is then empty.
- The draft guards against waste only if the person reads it before starting it.
- Checked with a real Kayden unit on a scripted model, and in the window on the second
  harness. Not checked: with a real model, so what a model proposes for a description is
  not known.
