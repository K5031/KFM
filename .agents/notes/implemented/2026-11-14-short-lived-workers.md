# Agent Note: Short-lived workers

Status: implemented — a supervisor can dismiss an agent it will not use again; notes a new worker starts with are [shared team memory](../proposed/2026-11-12-shared-team-memory.md), not built

Part of [standing teams](../proposed/2026-11-12-standing-teams.md).

## Problem

In the run of 2026-11-14 (duty: keep improving KFM, features allowed; Sonnet lead, Haiku
workers) the lead hired one K4 for each feature and named it after the feature
("Approvals", "Reporter"). Each knew exactly what its task was. Two things followed.

- **The roster fills.** A finished agent stays in the fleet: its process is closed, but
  it is on the chart and counts toward the fleet's limit of agents. A lead could not
  remove an agent; only the person could. Over a long duty a lead either reuses an agent
  whose name no longer fits its work, or hires until the limit and is then forced to
  reuse.
- **A new worker starts cold.** Each K4 was told to read about eight files before it
  began, and a feature cost about $0.55 to $0.65, a fair part of it reading the project
  again.

Reusing a finished agent looks like the answer to the second point, and is not. An
agent sends its whole conversation with every request. A cache hit makes that about a
tenth of the price, not free, and a working agent makes dozens of requests a task: the
tenth feature pays for the nine before it at every step. The provider's cache lasts
about five minutes, and KFM's longest check-in gap is five minutes, so a standing agent
that wakes after a quiet spell pays in full to load everything it has ever done.
Kaden compacts a long conversation, which bounds the cost at the price of detail and
of a model call.

## Decision

- **A supervisor has a fleet tool, `dismiss`**, beside `delegate` and `message`
  (`tools.py`, `Manager.dismiss`). The agent it names, one of its own, and everyone
  under it leave the fleet: their processes end, what they kept is deleted, they no
  longer count toward the fleet's limit of agents, and what they spent stays in the
  fleet's total (`Fleet.removed_usd`).
- **The tool says the fact that makes it worth using**: an agent sends its whole
  conversation with each request, so one given a second task carries the first into
  every step of it, and a new agent starts with its task alone. The brief of an agent
  that can hire names the tool. Nothing tells a supervisor when to dismiss, or to.
- **The record keeps who they were.** A dismissal is an event of the fleet
  (`events.Dismissed`: the agent, who dismissed it, and everyone who left, by id and
  name), said before the record changes. The feed shows it as a line and keeps what the
  dismissed agents said and reported; the chart and the agents list no longer have
  them. An agent the person removes still takes its entries with it.
- **The lead is the long-lived agent**, and stays cheap because the reading is done
  below it: in that run it had spent $0.41 when its two workers had spent $1.20.

**Who lives long is who holds the direction.** A long conversation is worth its cost
where the value is in what has been decided along the way: what the person wants, what
was tried and dropped, why a thing is as it is. A fresh agent can read the code again
and not that. That is the lead. A worker building one named thing needs a good brief,
not the history.

**Watching is not a task.** Work that is itself ongoing ("watch the logs") needs an
agent that stays, and such an agent has the cost described above. A standing team is
the role, its lead and what it knows; its workers come and go.

**None of this is a rule for agents.** What an agent gets is the ability (to dismiss)
and the facts (what an agent that stays costs); what it does with them is its judgment.

## Alternatives considered

- **Reuse finished agents for the next task.** Possible (a message to a finished agent
  is a new task). It keeps what the agent learned and makes every later request carry
  it; see the problem above.
- **Tell leads to name agents by lasting role** ("Developer", not "Approvals"). A rule
  for one observed habit, and it leads straight to the long-lived workers this note
  argues against.
- **Finished agents do not count toward the limit.** The chart still fills with agents
  that will never work again, and the limit stops meaning how big a fleet is.
- **A dismissed agent's entries leave the feed**, as a removed agent's do. A lead that
  dismisses each worker would empty the fleet's record of everything they reported.

## Consequences

- A lead on a long duty can end each round with only the agents it still uses.
- **Every worker is a cold start** until a new worker has notes to start with
  ([shared team memory](../proposed/2026-11-12-shared-team-memory.md)): dismissing by
  itself makes that worse, not better.
- A lead that dismisses too soon loses an agent it then needs.
- Tried with scripted units only; whether a real lead uses the tool, and when, is not
  known.
