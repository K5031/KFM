# Agent Note: Shared team memory

Status: proposed — every agent of a fleet reads and writes one set of notes, and reports point at results instead of repeating them

Part of [Standing teams](2026-11-12-standing-teams.md).

## Problem

A fleet passes everything through messages. In the runs of 2026-11-12:

- The lead read the project and found its fault before delegating; each agent it started
  knew nothing and found the same fault again.
- Agents pasted whole files into their reports. The lead read the report, then opened the
  file to check it: the same content paid for twice.
- Each agent has a private memory. What it worked out about the project is gone when it
  finishes, and the next agent starts cold.

If passing information around costs more than it saves, a fleet is less efficient than
one agent. For a team that holds a role for weeks, what it remembers together is most of
what makes it a team.

## Proposal

**Point, do not paste.**

- **One memory for the fleet.** Kayden's `kayden.memory.files` keeps long-term memory as
  a folder of notes with an index, per project, and changes a note only if the agent has
  read it as it is now. Each unit has such a folder in its own data folder. KFM points
  every unit at one folder in the fleet's folder (the module's `projects` option), so
  what one agent notes, every agent's next conversation sees.
- **Briefs and reports refer to it.** The brief tells an agent to note what the next
  agent would otherwise have to find out again, and to report where the results are and
  what was found, not their content.

## Alternatives considered

- **Everything through the supervisor's messages**, as now. Nothing is shared that the
  supervisor did not think to pass on, and each pass is written once and read once.

## Acceptance criteria

- A note one agent saves is in the index a later agent of the same fleet starts with.
- Two agents changing the same note do not overwrite each other unseen.
- The same job costs less with the shared memory than without, measured.

## Risks

- **Reading is paid for too.** A team notebook that grows unpruned becomes the new waste;
  the index has to stay as short as Kayden already keeps it.
- **An open conversation does not see new notes.** Kayden reads the index when a
  conversation is first assembled; an agent that runs for hours sees a teammate's note
  only if it looks.
- A wrong note misleads every agent, not one.
