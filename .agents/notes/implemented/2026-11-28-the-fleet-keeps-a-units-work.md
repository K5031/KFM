# Agent Note: The fleet keeps a unit's work

Status: implemented — Kaden dropped duty mode, so goals, questions to a supervisor and check-ins are the fleet's own: units only take turns

## Problem

A unit's task was a Kaden duty: a goal that Kaden kept going with check-ins until the
agent called `finish_goal`, or standing work that checked in forever, with
`ask_for_help` for a question to the supervisor and `HelpNeeded` for the fleet to carry
it up. Kaden removed duty mode on 2026-11-28 (its note `2026-11-28-no-duty-mode.md`):
no goals or duties but Continuous's life, no `finish_goal`, `ask_for_help`,
`HelpNeeded` or headless `--duty`/`--goal`, and `set_duty` refuses any kind but
"life". Its sub-sessions became plain tasks, as Claude Code's subagents: the task is the
first message and the last reply is the result. The user wants KFM to keep its own
behaviour (goals that end with a report, questions that go up, standing work that keeps
going) and to own what Kaden used to do for it.

## Decision

- **A unit only takes turns.** The unit contract (`kfm/api.py`) asks an agent program
  for turns on `begin` and on each `send`, and for `Rested` when a turn ends. Whether the
  unit's work stands, ended or waits is the manager's.
- **Finishing and asking are fleet tools**: `finish(report)` and `ask(question)` in the
  fleet's MCP server (`kfm/tools.py`), beside `report`, `delegate` and the rest. They
  set the unit's report or question (`Manager.finish`, `Manager.question`); when the
  turn ends the manager makes the unit done and delivers the report, or makes it waiting
  and delivers the question. `finish` is refused for standing work. A message that
  reaches a done unit gives it its task again, to finish again.
- **The manager wakes a resting unit whose work stands.** When a turn ends with no
  report and no question (standing work, or a goal not yet finished), the manager sends
  the unit `units.CHECK_IN` after the longest rest (`FleetConfig.check_in_s[1]`, 5
  minutes), unless a message or the unit's own plan wakes it first. A waiting unit is
  not woken.
- **The brief is the first message.** The Kaden harness starts a plain conversation and
  sends the brief as its first message, as Kaden's sub-sessions do with their task. A
  new instruction (a new task, the agents under it changed) goes as a Kaden notice
  (`notify`) just before the next message; the harness keeps the instruction the agent
  last read in the unit's folder (`instruction.md`) and does not tell the same one again,
  so a resumed unit is not told its brief twice.
- **A question an agent waits on in a call** (Kaden's `AskUserQuestion`) stays a unit
  event, `Asked`: the agent takes the next message as its answer, so the manager holds
  every other message until the answer has gone.
- **`Vocabulary` names only the fleet tools' prefix** (`mcp__fleet__` for Kaden): every
  tool KFM names to an agent is now a fleet tool.
- There is no "idle" state: a goal that rests without a report is still at work and is
  woken.

## Alternatives considered

- **A goal is done when its turn ends, its last reply the report**, as Kaden's
  sub-sessions and Claude Code's subagents work. No tool needed, but a lead ends its turn
  while its agents work, and would be done before their reports arrive; a question in a
  reply would look like a report.
- **The Kaden harness keeps the old contract**: it schedules check-ins itself and reads
  `mcp__fleet__finish` and `mcp__fleet__ask` calls out of the agent's messages to emit
  `Reported` and `Asked`. The contract would not move, but the tools would live in the
  manager and their meaning in a harness, every harness would rebuild the timer, and the
  harness would depend on tool names.
- **The brief in Kaden's system prompt** (`update_settings(system_prompt=…)`), where a
  duty's instruction used to be. It would not grow the conversation, but it replaces the
  person's base prompt for the unit, the harness would have to compose the two, and a
  change would rewrite the unit's settings file under Kaden.
- **Kaden's `reminder_min_s` from `check_in_s[0]`**, so that a unit's own plan does not
  wake it sooner than the fleet's shortest rest. Kaden does not read that key from its
  configuration, and KFM adds nothing to Kaden for itself.

## Consequences

- A fleet runs on an unchanged Kaden without duty mode; the second harness in the tests
  calls the same `finish` and `ask` tools over MCP, so goals and questions work the same
  on any agent program that has the fleet tools.
- Each check-in is a message in the unit's conversation, kept and paid for like any;
  Kaden's check-ins were notices of its own.
- `check_in_s[0]`, the shortest rest, is no longer used: Kaden's own minimum for a due
  plan step (60 seconds) applies.
- A unit's brief is in its conversation and is summarized with it; a new one is a
  notice that follows the old, not a replacement of it.
- A unit begun on a duty has no `instruction.md`: the first time it is opened, its brief
  is told again.
