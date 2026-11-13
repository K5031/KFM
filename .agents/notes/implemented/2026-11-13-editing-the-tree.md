# Agent Note: Editing the tree

Status: implemented — the person builds a fleet agent by agent on the chart and starts it, or adds agents and sets models in a fleet a lead built

Follows [Projects](2026-11-13-projects.md) and
[drafting a project](2026-11-13-drafting-a-project.md).

## Problem

Inside a fleet the person decided nothing: a fleet was made from a task, and its lead
hired whom it wanted, on the model its rank is given in a settings file. A person who
knows the team they want could not build it, or give one agent a better model. Every
way of making a fleet began with an agent reading a plan and doing the building.

## Decision

**A fleet can be made with no task.** It then has no agents and runs nothing
(`manager.lay_out`): the chart shows its name and a place for its lead. The person adds
the lead and then agents under any agent, each with a name, a task (or a duty) and, if
it should not run on its rank's, a model. **Start** starts them all.

**Changes to a fleet that is not running are changes to its record**
(`manager.place`). An agent placed that way has the state `planned` and starts when the
fleet does, through the same path that resumes a stopped fleet (`Manager.resumed`). So
there is one rule, with no kind of fleet that is "manual": a fleet a lead built can be
added to while it is stopped, and a fleet built by hand can have a lead that hires.

When the fleet starts, every planned agent's process is brought up first, and then each
is given its task from the top down. A supervisor that starts with its agents finds them
listed in its brief; a supervisor that was there before is told with a message.

**A task is optional.** An agent with none has no process and spends nothing (state
`idle`); the first message it gets is its task, and it starts on it (`Manager.tell`).
Its supervisor is told it has no task yet and how to give it one; for a lead, the
message is the person's. This lets the person fix who is on a team and leave what each
does to the supervisor, which knows the work better than a plan written beforehand.
An agent that delegates must still give a task.

**In a running fleet** an agent is added as if its supervisor had delegated it and
starts at once (`Manager.hire`), and the supervisor is told.

**Model**: the model an agent runs on (`Unit.model`, kept in the fleet's record) wins
over the team's and the rank's, and empty returns the agent to those. An agent at work
is started again on it at once, in its own conversation, and told so
(`Manager.set_model`). The model is typed as its id: what models exist is the harness's
knowledge, and the unit contract has no way to ask.

## Alternatives considered

- **Moving an agent to another supervisor by dragging it** (asked for first, then
  dropped by the user, 2026-11-13). It changes the agent's rank and that of everyone
  under it, has to be refused where that would go below K1, both supervisors and the
  agent have to be told in the middle of their work, the agent's standing instruction
  still names its old supervisor, and across fleets it means another manager and limit.
  Stopping the agent and adding one in the other place does the same.
- **Adding and changing agents only while the fleet runs** (built first). The person
  could shape a team only after a lead had started on a task and begun hiring, which is
  not building it by hand: the user asked for two ways and found one.
- **A manager for a fleet that is being built**, so that one code path adds agents. It
  would load the agent program and hold a log and a tool server for a fleet in which
  nothing runs, and it would have to be made again each time the window opens.
- **Start an agent with no task and have it wait.** It would spend on starting up, end
  its turn with nothing done, and its supervisor would get "stopped without a report".
- **The model takes effect the next time the agent's process starts.** Nothing is
  interrupted, but an agent on a duty may never start again, and the person who set a
  model would see nothing change.

## Consequences

- Setting the model of an agent at work interrupts its turn; what it ran in the
  background ends, as on a resume.
- A lead that hires and a person who places agents can build two teams for one job.
- A supervisor may never give a waiting agent a task: it then stays idle, at no cost.
- A fleet built by hand has no task of its own: what it does is what its lead's task
  says. Nothing checks that the agents' tasks fit together.
- An agent cannot be taken out again, and its task cannot be changed. Its name can; the
  agent is not told, so its own conversation still has the first one.
- A model id that the harness does not know fails the agent when it starts; the chart
  shows it failed.
- The menu is reached by right-click only; nothing on an agent's box shows it is there.
- Checked on the second harness. Not checked: with real Kayden units, so that a
  conversation carries on under another model is taken from Kayden, not seen here.
