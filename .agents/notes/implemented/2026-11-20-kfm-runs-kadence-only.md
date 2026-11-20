# Agent Note: KFM runs Kadence only

Status: implemented — every agent is a Kadence; the unit contract stays as a boundary inside KFM and is not offered to other agent programs

Narrows [the unit contract](2026-11-12-unit-contract.md). Shapes
[an agent's page is its conversation](2026-11-20-an-agents-page-is-its-conversation.md).

## Problem

KFM said its agents need not be Kadence: a setting named the agent program, a loader
found it, and the documentation told how to write one. No second program was ever run.
The only other one is the scripted unit of the tests.

The promise still had to be kept by every feature. Showing an agent's conversation as
Kadence shows it became a question of what a program that does not exist would show in
its place, how its page would be made to look like the rest of the window, and what
happens when it offers none.

## Decision

The user's (2026-11-20): it is Kadence's fleet manager, and running Kadence is enough.
Whoever wants another model, other tools or another memory changes Kadence's modules,
which is where that choice already is.

- Every unit is a Kadence. `harnesses.load` makes it with the settings'
  `harness_options`; nothing names another program, and the documentation offers none.
- `kfm/api.py` stays, as the line inside KFM between what a fleet does and how one
  agent's process is run: what KFM asks of a unit, and what a unit says. The manager
  knows nothing of processes, and the tests put a scripted unit in Kadence's place.
- A feature is designed for Kadence, and reaches past that line where that is the
  simplest way, as the window does when it shows Kadence's own conversation view.
- The settings' `harness` says nothing. It is in fleets' records of format 9 and leaves
  with that format.

## Alternatives considered

- **Keep the promise.** Each feature is designed twice, once for a program nobody has.
- **Take the contract out as well**, with its base classes and the words a harness has
  for its tools. The manager, the briefs, the tools and most tests would change for no
  change in what KFM does, and the tests would lose the scripted unit that makes them
  fast, or keep it by another name.
- **Let the agent program provide its page** in the window, with KFM's feed where it
  offers none, and KFM's look handed to it. It is the promise again, for the window.

## Consequences

- **Less to design.** What Kadence can show or do, KFM can use directly.
- **Another agent program is one file away and no nearer.** The line is still there. Each
  place where KFM reaches past it to Kadence is something such a program would not
  have, and would have to be looked at when one is real.
- **KFM's window is Kadence's.** Its frame, text, look and now its conversation view.
