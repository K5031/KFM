# Agent Note: A project's secretary

Status: proposed — the agent that maps out a project stays as the person's one contact for it, asleep between conversations; team leads can message each other

Changes [drafting a project](../implemented/2026-11-13-drafting-a-project.md) and builds
on [projects](../implemented/2026-11-13-projects.md). Related:
[Build](2026-11-18-build.md), [short-lived workers](2026-11-14-short-lived-workers.md),
[shared team memory](2026-11-12-shared-team-memory.md).

## Problem

A project is several fleets with the person as the only thing above them.

- **The agent that drafts the project is thrown away.** It reads what the person wants,
  proposes the teams and ends. What it understood about the project goes with it, and
  it exists for that one step, with a brief, a tool and a page of its own.
- **Teams cannot reach each other.** Each lead reports to the person, and two teams
  meet only through files. Whatever crosses a boundary lands on the person.
- **The person has no one to ask.** To know how the project stands they read every
  fleet's feed; to change course they work out which leads it concerns and write to
  each.

## Proposal

The person's idea (2026-11-19), which joins two others: one lead above all teams (the
person gets one contact, at the price of a manager every report passes through) and no
drafting at all with leads that talk to each other (simple, and the person does all the
setting up).

### The secretary

Every project has one agent beside its teams, the secretary.

- **It maps out the project.** Given what the project should do, it proposes the teams,
  as the drafting agent does today, on the page the person changes and starts from.
- **Then it sleeps.** Its process is closed when its turn ends, as any finished agent's
  is, and it costs nothing until someone writes to it.
- **It is who the person asks.** A message to the project goes to the secretary, which
  wakes on its own conversation: it still knows what the person wanted and why the
  teams are what they are. Who lives long is who holds the direction
  ([short-lived workers](2026-11-14-short-lived-workers.md)).
- **It is not a manager.** No team reports to it and no report wakes it. Leads answer to
  the person, as now, and the person can still write to any lead or agent directly.

What it is able to do, and nothing about when:

| Ability | What it is |
|---|---|
| `teams` | how each team stands: its lead, what it is doing, what it spent, and from the record KFM keeps of every team, what was reported, asked and said to it lately, the person's own messages to a lead included |
| `message` | write to a team's lead, as the person does |
| `propose` | put a change to the person: teams added, a team's work restated, a team ended, a team given a folder of its own. Shown where a draft is shown; applies when the person approves |

**The project's state is the record, not a file someone keeps.** KFM already writes
down every report, question, message and change of each team. The secretary reads that,
so what she knows after sleeping is what happened, including what the person told a
lead without her.

So "how is it going?" is answered from `teams`; a small adjustment is a `message` to
the lead it concerns; a change of structure or a plan that involves several teams is a
`propose` the person approves. Which of the three a request needs is its judgment.

### Leads reach each other

- A lead's facts name the project's other teams, what each is there to do, and its
  lead.
- `message` reaches another team's lead as it reaches the lead's own agents, and the
  answer comes back as a message. Agents below a lead go through their lead.
- A message to a team that is stopped or has finished is refused with that fact: one
  team does not start another, or spend its money.
- A lead can write to the secretary as to another lead, which wakes her. Two leads that
  cannot agree have her, and through her or directly the person, to take it to.

### What teams share

The project's folder, which every team already works in, and nothing keeps two teams
from changing the same file, which every agent is told. A team can be given a folder of
its own, by the person or in a proposal. Nothing else is added here;
[shared team memory](2026-11-12-shared-team-memory.md) is the piece for what teams know
as opposed to what they made.

## Alternatives considered

- **One lead above all teams.** Coordination and the person's one contact in one agent.
  Every report then wakes the most expensive conversation in the project, each team's
  own limit and Stop become one, and the projects built on 2026-11-13 are rebuilt.
- **No drafting; the person sets up the teams, and leads talk to each other.** Removes
  the special case and keeps everything else. The person has to know the split before
  they start, and still has nobody to ask or to hand a change to.
- **A state file the leads keep up to date** (status, decisions, blockers), for the
  secretary to read on waking. It is a duty laid on every lead, kept as well as each
  lead keeps it, and a second account beside the record KFM writes anyway.
- **A rule for escalation** (an unresolved conflict wakes the secretary or the person).
  Whether something is unresolved is a judgment; the leads are given whom they can
  reach, and reach them when they judge so.
- **A fixed layout for the shared folder** (an area per team, an append-only log, an
  owner per file), held by KFM. It decides for every project how its files are
  arranged. A team's own folder is there for the projects that need it.
- **As it is, with a "status" command that summarises the fleets.** A summary by fixed
  code says what is in the records and cannot be asked a question or told a change.

## Acceptance criteria

- A project described in words gets its teams from the secretary, and the secretary is
  still there afterwards with that conversation.
- With every team at work and nobody writing to it, the secretary spends nothing.
- Asked how the project stands, it answers from the teams' state without the person
  opening a fleet.
- Told a change that concerns one team, it reaches that lead; told one that changes the
  teams, the person gets a proposal and nothing changes before it is approved.
- A lead that needs something from another team gets it by message, without the person.

## Risks

- **What it knows goes stale while it sleeps.** It wakes knowing the plan, not the
  week. Each question costs it a look at the record, and it can answer from memory when
  it should have looked.
- **Two teams can drift apart without either noticing.** No rule or referee catches
  what neither lead sees as a conflict.
- **Two voices.** A lead can hear one thing from the person and another from the
  secretary. Its message is the person's word passed on, and a lead cannot tell how
  faithfully.
- **Talk between leads costs money.** Each message wakes a lead on its whole
  conversation. Each team's limit is what bounds it.
- **Nobody settles a disagreement between leads** but the person. The secretary can
  carry it to them; it has no say of its own.
- **A person who stops reading the feeds** knows only what the secretary tells them.
