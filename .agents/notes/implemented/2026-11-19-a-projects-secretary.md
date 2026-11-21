# Agent Note: A project's secretary

Status: implemented — the agent that maps out a project stays as the person's one contact for it, asleep between conversations; team leads can message each other

Builds on [projects](2026-11-13-projects.md). Related:
[editing the tree](2026-11-13-editing-the-tree.md), the other way to shape a project;
[Build](../proposed/2026-11-18-build.md);
[short-lived workers](2026-11-14-short-lived-workers.md);
[shared team memory](../proposed/2026-11-12-shared-team-memory.md).

## Problem

A project is several fleets with the person as the only thing above them.

- **The agent that drafted a project was thrown away.** It read what the person wanted,
  proposed the teams and ended. What it understood about the project went with it, and
  it existed for that one step, with a brief, a tool and a run of its own.
- **Teams could not reach each other.** Each lead reports to the person, and two teams
  met only through files. Whatever crossed a boundary landed on the person.
- **The person had no one to ask.** To know how the project stood they read every
  fleet's feed; to change course they worked out which leads it concerned and wrote to
  each.

## Decision

The person's idea (2026-11-19), which joins two others: one lead above all teams, and
no drafting at all with leads that talk to each other.

### The secretary

Every project has a secretary beside its teams.

- **She is a fleet of one agent** (`fleets/secretary` in the project's folder, called
  "Secretary"), so her conversation, her record, messages to her and starting her again
  are what any fleet has. She is not one of the project's teams: `Service.fleets` and a
  project's counts leave her out, and `ProjectInfo.secretary` is her id.
- **She maps out the project.** A project made with a description says it to her
  (`Service.create_project(..., description)`), which starts her. Without one she has
  not begun, and nothing runs.
- **Then she sleeps.** Her process is closed when her turn ends, as any finished
  agent's is, and the service does not count her as running while she is at nothing.
- **She is who the person asks.** A message to her wakes her on her own conversation:
  she still knows what the person wanted and why the teams are what they are.
- **She is not a manager.** No team reports to her and no report wakes her. Leads
  answer to the person, who can still write to any lead or agent directly.
- **She is a conversation, not work that ends.** Every other agent has a goal or a
  duty, which it ends or reports on. Her brief is said once, as what her conversation
  opens with, and after that she answers each message and stops
  (`api.Instruction.conversation`; for Kadence, no duty is set). So there is no report:
  what she writes is the one answer the person reads. With a goal she wrote her answer
  and then gave it again as her report, since the person reads her conversation itself.
- **She has the permissions any agent has**, the settings' level (the user,
  2026-11-20). Held to reading, every command she ran to look around asked the person,
  a listing of a folder included. That she does none of the teams' work is hers to
  judge, as it is that she manages nobody.

Her brief (`units.secretary_brief`) says what is so and what her tools do, and nothing
on when to use which. In place of a lead's tools she has three (`tools.secretary_tools`):

| Tool | What it is |
|---|---|
| `teams` | how each team stands: its lead, what it is doing, what it spent, and from the team's record what was lately reported, asked and said to it, the person's own messages to a lead included. With a team's name, more of that record |
| `message` | write to a team's lead, who reads it as from the project's secretary. It starts a team that is stopped, and a lead that finished or failed |
| `propose` | put teams to the person: each with a name, what it does, task (unless she says duty) or duty, and optionally a folder of its own inside the project's |

**The project's state is the record, not a file someone keeps.** KFM already writes
down every report, question, message and change of each team. `teams` reads that
(`LocalService._standing`), so what she knows after sleeping is what happened.

### A proposal

`propose` hands the teams to the service, which holds them for the project and says so
(`service.Proposed`). The window shows them on the page a draft was shown on, where the
person changes them and starts them, each as a new fleet; the terminal has
`kfm proposal PROJECT [--start|--drop]`. Started or dropped, the proposal is settled
(`Service.settle_proposal`). A page that waits for her first proposal stops waiting
when she ends without one.

### Leads reach each other

- A lead's brief says its fleet is one team of a project, and `status` lists the other
  teams with what each is there to do.
- A lead's `message` takes a team's name, or "secretary", where it takes an agent's id.
  The other lead reads `[Message from the lead of the team dev] …`; both fleets' records
  get the message (`events.Across`), and both feeds show it. Agents below a lead have no
  such way.
- A message starts a team that is not running, and wakes a lead that finished or
  failed, whoever sent it: another lead, the secretary, or the person (the user,
  2026-11-20). A team that has finished is otherwise out of every other team's reach,
  and work that needs it waits for a start nobody gives. The started team spends from
  its own limit, which is what bounds it; one that has spent it is not started, and the
  sender is told so.

### What teams share

The project's folder, which every team already works in, and nothing keeps two teams
from changing the same file, which every agent is told. A team can have a folder of its
own, chosen by the person or proposed by the secretary.

## Alternatives considered

- **An agent that drafts and ends.** What was there. See the problem.
- **One lead above all teams.** Coordination and the person's one contact in one agent.
  Every report then wakes the most expensive conversation in the project, each team's
  own limit and Stop become one, and projects are rebuilt.
- **No drafting; the person sets up the teams, and leads talk to each other.** Removes
  the special case and keeps everything else. The person has to know the split before
  they start, and still has nobody to ask or to hand a change to.
- **A state file the leads keep up to date** (status, decisions, blockers), for the
  secretary to read on waking. It is a duty laid on every lead, kept as well as each
  lead keeps it, and a second account beside the record KFM writes anyway.
- **A lead's message does not start a stopped team; the secretary's does.** One team
  then cannot spend another's money. A lead that needs a finished team has to go
  through the secretary, which makes her the coordinator she is not meant to be, and
  one that does not think of it waits for a team that never comes back.
- **Read-only permissions for her**, whatever the settings give other agents, so that
  she could not do the teams' work. A shell command is not a read, so she asked the
  person before each one, on a level meant to spare them that.
- **A rule for escalation** (an unresolved conflict wakes the secretary or the person).
  Whether something is unresolved is a judgment; the leads are given whom they can
  reach, and reach them when they judge so.
- **A fixed layout for the shared folder** (an area per team, an append-only log, an
  owner per file), held by KFM. It decides for every project how its files are
  arranged. A team's own folder is there for the projects that need it.
- **A "status" command that summarises the fleets.** A summary by fixed code says what
  is in the records and cannot be asked a question or told a change.
- **A secretary run by code of her own**, beside the fleets, as the drafting agent was.
  A fleet of one gives her a conversation that is kept, a record, a page in the window
  and a way to be written to, without any of it being built twice.

## Consequences

- **One contact, at no standing cost.** The person asks one agent about the whole
  project, and pays for her only while she answers.
- **What she knows goes stale while she sleeps.** She wakes knowing the plan, not the
  week. Each question costs her a look at the record, and she can answer from memory
  when she should have looked.
- **Two voices.** A lead can hear one thing from the person and another from the
  secretary. Her message is the person's word passed on, and a lead cannot tell how
  faithfully.
- **Talk between leads costs money.** Each message wakes a lead on its whole
  conversation, and starts its team if it had stopped: a team the person stopped can be
  started again by another. Each team's limit is what bounds it.
- **Nobody settles a disagreement between leads** but the person, and two teams can
  drift apart without either noticing.
- **She proposes new teams only.** Ending a team or restating its work is the person's
  to do, or hers to ask of its lead by message.
- **A proposal is held by the running service.** One that is neither started nor
  dropped is gone when the service ends; she can be asked for it again.
- **Projects are of format 3** and those made before it are not read.
- **Her brief is an ordinary message.** In a conversation long enough for Kadence to
  summarise its beginning, the brief is summarised with it.
- **Tried once with a real model** (2026-11-19, as a goal): asked how the project
  stood, she read the team's record and said what she had not checked. How leads use
  the way to each other is known from scripted agents only.
