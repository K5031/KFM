# Agent Note: One holder of a fleet

Status: implemented — a `Manager` holds a fleet whether or not it runs: its record and rules always, its processes only between start and stop

Follows [editing the tree](2026-11-13-editing-the-tree.md),
[resuming a fleet](2026-11-12-resume.md) and
[one API, many frontends](2026-11-14-one-api-many-frontends.md).

## Problem

A manager existed only while a fleet's processes were up. Everything a person can do to
a fleet was therefore written twice: in the manager for a running fleet, and as an edit
of the saved record for a stopped one. Each feature added a pair of branches, the two
drifted apart (a stopped fleet could not be given a lead once it ran, an agent that had
run could not be given a task while stopped), and starting sorted agents into kinds to
bring up what the stopped half had left behind.

"Has no task yet" was no state either: such an agent was known by being idle, having no
session and having no task, all at once.

## Decision

**One object per fleet.** The service makes a `Manager` for every fleet it reads
(`Manager.new`, `Manager.load`) and keeps it. It always holds the record and the rules;
`start` brings the processes up (the agent program, the fleet tools) and `stop` ends
them. Each operation (`add`, `retask`, `remove`, `rename`, `set_model`, `say`) is one
function that changes the record and, if the fleet runs, the processes.

**`planned` is the one state for "in the fleet, not begun".** Every agent starts in it.
`Manager._launch` is the one place an agent begins: when the fleet runs and the agent
has a task. It is called when the fleet starts, when an agent is added and when one is
given a task. An agent with no task stays `planned` until its first message, which is
its task (`Manager.say`, `Manager.message`); `Manager.tell` only delivers messages.

**The record is what an agent is opened on.** When a fleet starts, every agent that was
at work is opened on its conversation and given its standing instruction as the fleet
then is (`Session.instruct`), then told the fleet was down. So what changed while the
fleet was stopped needs no bookkeeping: another task is in the instruction, and so are
the agents the person put under it (the brief lists an agent's agents whenever it is
written).

**A fleet that reaches its spending limit stops.** There is then no running fleet that
may not start anything: the limit is tested where a fleet starts.

## Alternatives considered

- **Start the fleet when an agent that has run is given a task**, as a message does. A
  person who changes three tasks before pressing Start would have the whole fleet
  running, and spending, after the first.
- **Keep what an agent is to be told in the record until its process is next up.** A
  queue of messages per agent, saved and replayed: a second place for what the task and
  the tree already say.
- **Keep `idle` for an agent without a task in a running fleet.** It is what made the
  condition a combination of fields; `idle` means a turn ended without a report.
- **Give a first message's files to the agent with its task.** The unit contract's
  `begin` takes an instruction and no files, so they follow as a message of their own;
  a first message with files and no words is refused, since a task cannot be empty.

## Consequences

- A lead can be added to a running fleet that has none, and an agent that has run can be
  given another task while its fleet is stopped; both were refused.
- An agent given a task while stopped is told the fleet was started again, not that its
  task changed: it reads the new task in its instruction. Its supervisor is not told
  either, having no process; it reads its agents' tasks in its own instruction.
- Every start sends each agent that was at work its instruction again: one more request
  to its program per agent.
- A taskless agent shows as `planned`, in a running fleet too.
- A manager is held for every fleet the service has read, also those that never run.
  It costs a record in memory: the agent program and the tool server are made on start.
- `fleet.json` is format 8: `planned` is every new agent's state, and `idle` no longer
  means "waits for a task".
- Checked on the second harness. With real Kadence units only the paths the earlier
  tests cover (start, resume, another task while running) were run.
