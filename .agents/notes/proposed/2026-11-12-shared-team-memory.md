# Agent Note: Shared team memory

Status: proposed — a supervisor publishes notes its agents can read but not change, as a Kadence Memory module; not to be built before a run shows a team needs it

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

**Point, do not paste**, with notes that flow one way.

- **Private by default.** An agent's own memory stays its own, as now: a supervisor's
  notes hold things its agents should not read (what it thinks of their work, what the
  person told it in confidence, the budget) and most of them are noise to an agent with
  one task.
- **Team notes are published, not shared.** A supervisor chooses what its agents should
  know and writes that as a team note; its agents read team notes and cannot change
  them. What an agent learns goes up in its report, as now, and the supervisor decides
  whether it becomes a team note.
- **It is a Kadence Memory module, not part of the unit contract.** Memory is one of
  Kadence's swappable modules: a module built on `kadence.memory.sqlite.SqliteArchive`, as
  `kadence.memory.files` is, keeps the agent's private notes and shows the team's beside
  them. KFM's Kadence harness names it in each unit's configuration; another harness
  solves team memory its own way.
- **Briefs and reports refer to it** instead of repeating content.

## Alternatives considered

- **Everything through the supervisor's messages**, as now. Nothing is shared that the
  supervisor did not think to pass on, and each pass is written once and read once.
- **One memory folder for the whole fleet**, every agent reading and writing it: a
  setting in KFM's Kadence harness that points `kadence.memory.files` at one folder
  (`projects`). Almost nothing to build, but agents see their supervisor's notes, and
  an agent that was misled (by a web page it read, by its own mistake) writes into what
  every agent above it reads: a wrong or planted note travels up the ranks.

## Acceptance criteria

- A team note a supervisor writes is in what its agents start with; its private notes
  are not.
- An agent cannot change a team note.
- The same job costs less with the shared memory than without, measured.

## Risks

- **Reading is paid for too.** A team notebook that grows unpruned becomes the new waste;
  the index has to stay as short as Kadence already keeps it.
- **An open conversation does not see new notes.** Kadence reads the index when a
  conversation is first assembled; an agent that runs for hours sees a teammate's note
  only if it looks.
- A wrong team note misleads every agent below, not one.
- Not shown to be needed: no run has had a team pay to learn the same thing twice.
