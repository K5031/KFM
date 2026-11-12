# Agent Note: The unit contract

Status: implemented — what a fleet needs from the agent program its units run is a contract in `kfm/api.py`; Kayden is the first harness behind it and any other can stand in its place

## Problem

A unit was a Kayden, and the manager spoke Kayden: its events, its requests, its
configuration files, the names of its tools. The owner wants KFM able to run its fleet on
another agent program, one built with KFM in mind, and wants that boundary drawn now,
while the manager is small, so that it never has to be cut through a grown one.

## Decision

`kfm/api.py` is the contract, in the manner of Kayden's `api.py`: the types, and in its
head what they cannot express.

- **A harness opens units.** `Harness.open(spec, on_event, session)` starts a unit's
  process from a `UnitSpec` (its data folder, the fleet's work folder, model, permissions,
  spending limit, the fleet tools' address) and returns a `Session`: `begin(instruction)`,
  `send(text)`, `approve(request_id, allow)`, `close(grace_s)`.
- **A unit says what it does** in nine events: `Working`, `Rested(carries_on)`, `Said`,
  `Reported`, `Asked`, `Spent`, `ApprovalNeeded`, `Failed`, `Exited`. The manager keeps a
  unit's state from these and nothing else.
- **What a unit must do** is written at the top of the file: work by itself on its
  instruction and wake itself, end a goal with a report, ask when blocked, have the fleet
  tools (an MCP server) and run them unasked, stay inside its permissions and its
  spending limit, and keep its session, on which it is opened again and then waits for a
  message.
- **The words are the harness's.** `Harness.vocabulary` names the tool that finishes a
  goal, the one that asks, and what stands before a fleet tool's name; the brief and
  every message KFM writes to an agent use them (`units.py`).
- **Harnesses are found by name.** The fleet's settings name one (`harness`, with
  `harness_options`): an entry point in the group `kfm.harnesses`, or
  `package.module:factory`. `kayden` is the default.
- **Everything of Kayden is in `kfm/harnesses/kayden.py`**: the unit's configuration
  files, the headless process, and the translation of Kayden's events. It lets the fleet
  tools run by answering their permission requests itself, and reports a goal only while
  the conversation has one.
- **A second harness proves it**: `tests/fakeharness.py` is written against `kfm/api.py`
  alone, with scripted units inside the test's process that call the fleet tools over
  MCP. A fleet delegates, asks, reports and is resumed on it.

## Alternatives considered

- **Write the contract down and build nothing until a second unit exists.** Proposed
  first: an interface drawn from one example fits that example. The owner chose to build
  early: redrawing the boundary later means an overhaul of whatever has grown across it.
  The second harness in the tests stands in for the second example.
- **The protocol as the contract**: any program that speaks Kayden's headless channel is
  a unit, started from a command in the settings. No interface in KFM at all, but another
  agent program would have to imitate Kayden's events and its data folder.
- **Adapters for existing agent programs** (opencode and others). They have no duties,
  goals or reports to a supervisor, so an adapter would have to supply the very things a
  fleet depends on.

## Consequences

- The manager imports nothing of Kayden. Checked with real models after the change: a
  K2 lead on Claude Opus 5.5 and a K1 agent on Claude Sonnet 5.5 produced and checked a
  file for $0.40.
- A harness is a Python object in the manager's process. One for a program in another
  language starts and speaks to that program itself.
- The contract was drawn with one real harness in sight; what a second real one needs
  may still move it. It has no version.
- One harness per fleet: a team cannot yet run on another than the rest.
- Features that lean on what only Kayden has (a memory the fleet shares, through
  Kayden's memory folders) have to become part of the contract or stay with that harness.
- A unit's record keeps `session`, what its harness opens it on; a fleet folder of
  format 2 is refused.
