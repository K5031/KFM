# Agent Note: Short-lived workers

Status: proposed — a worker does one task with a fresh conversation and is then dismissed; only a supervisor lives long, and what a team knows is kept outside conversations

Part of [standing teams](2026-11-12-standing-teams.md). Depends on
[shared team memory](2026-11-12-shared-team-memory.md) for the second half.

## Problem

In the run of 2026-11-14 (duty: keep improving KFM, features allowed; Sonnet lead, Haiku
workers) the lead hired one K4 for each feature and named it after the feature
("Approvals", "Reporter"). Each knew exactly what its task was. Two things followed.

- **The roster fills.** A finished agent stays in the fleet: its process is closed, but
  it is on the chart and counts toward the fleet's limit of agents. A lead cannot remove
  an agent; only the person can. Over a long duty a lead either reuses an agent whose
  name no longer fits its work, or hires until the limit and is then forced to reuse.
- **A new worker starts cold.** Each K4 was told to read about eight files before it
  began, and a feature cost about $0.55 to $0.65, a fair part of it reading the project
  again.

Reusing a finished agent looks like the answer to the second point, and is not. An
agent sends its whole conversation with every request. A cache hit makes that about a
tenth of the price, not free, and a working agent makes dozens of requests a task: the
tenth feature pays for the nine before it at every step. The provider's cache lasts
about five minutes, and KFM's longest check-in gap is five minutes, so a standing agent
that wakes after a quiet spell pays in full to load everything it has ever done.
Kadence compacts a long conversation, which bounds the cost at the price of detail and
of a model call.

## Proposal

- **A supervisor can dismiss an agent it will not use again** (a fleet tool beside
  `delegate` and `message`): the agent and those under it leave the fleet, as when the
  person removes one, and what they spent stays in the fleet's total. One task, a small
  fresh conversation, then gone.
- **The lead is the long-lived agent**, and stays cheap because the reading is done
  below it: in that run it had spent $0.41 when its two workers had spent $1.20.
- **What a team knows is kept outside conversations**: in the brief a supervisor writes
  (the lead already listed the files to read, the project's rules and how to run its
  tests) and in team notes a new worker starts with
  ([shared team memory](2026-11-12-shared-team-memory.md)). That, and not a long
  history, is what keeps a fresh worker from learning the project again.
- **Nothing tells a lead to reuse or not to.** It is given the means and decides.

**Who lives long is who holds the direction.** A long conversation is worth its cost
where the value is in what has been decided along the way: what the person wants, what
was tried and dropped, why a thing is as it is. A fresh agent can read the code again
and not that, and the person pays for the gap in corrections. That is the lead, as it
is a person's own long session with one agent. A worker building one named thing needs
a good brief, not the history.

**Watching is not a task.** Work that is itself ongoing ("watch the logs", "keep an eye
on the inbox") needs an agent that stays, and such an agent has the cost described
above. There should be as few as possible, ideally the lead alone, each kept cheap: a
check that does almost nothing unless something changed (about 1.5 cents a check-in in
the duty run of 2026-11-12, see [standing teams](2026-11-12-standing-teams.md)). A
standing team is the role, its lead and what it knows; its workers come and go.

## Alternatives considered

- **Reuse finished agents for the next task.** Possible today (a message to a finished
  agent is a new task). It keeps what the agent learned and makes every later request
  carry it; see the problem above.
- **Tell leads to name agents by lasting role** ("Developer", not "Approvals"). A rule
  for one observed habit, and it leads straight to the long-lived workers this note
  argues against.
- **Finished agents do not count toward the limit.** The chart still fills with agents
  that will never work again, and the limit stops meaning how big a fleet is.

## Acceptance criteria

- A lead on a duty of several rounds ends each round with only the agents it still
  uses.
- A dismissed agent's spending is in the fleet's total, and its supervisor's own
  conversation is not longer for having had it.
- With team notes, a second-round worker reads less of the project than a first-round
  one, measured in what it spends before its first change.

## Risks

- A lead that dismisses too soon loses an agent it then needs, and pays the cold start
  it was meant to avoid.
- Without team notes this is only half: dismissing is cheap to build, and by itself
  makes every worker a cold start.
- One run, on one duty, with one pair of models.
