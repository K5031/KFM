# Agent Note: Units other than Kayden

Status: proposed — what a fleet needs from a unit is written down as a contract, so that an agent program built for KFM can stand where Kayden does; nothing is built until a second one exists

## Problem

A unit is a Kayden. The owner wants KFM to be able to run its fleet on another agent
program instead: not an existing one bent to fit, but one built with KFM in mind.

What KFM uses of Kayden is small and already in few places: four requests on the headless
channel (`start`, `set_duty`, `send`, `resolve_permission`), about ten of its events, and
the files `units.prepare` writes into the unit's data folder. What it relies on beneath
that is larger: a duty or a goal that the unit carries on by itself, `finish_goal` and
`ask_for_help`, tools from an MCP server, a spending limit the unit enforces, permissions
that ask, and a conversation that survives the process.

## Proposal

- **The contract is the protocol.** A unit is any program that is started headless on a
  data folder and speaks the channel KFM speaks today, with the behaviour named above.
  That is written down as KFM's unit contract, taken from what the manager really uses.
- **No plugin system in KFM.** The manager already starts a unit from a command
  (`Manager(command=...)`); a setting for that command, per fleet and later per team, is
  the whole mechanism. What is Kayden's own (the configuration files `units.prepare`
  writes) stays in `units.py`.
- **Built when a second unit exists.** Until then only the contract is kept current, as
  the manager changes.

## Alternatives considered

- **An adapter interface in KFM now**, with Kayden as its first implementation. An
  interface drawn from one example fits that example; the second unit shows what it
  should have been.
- **Adapters for existing agent programs** (opencode and others). They have no duties,
  goals or reports to a supervisor, so an adapter would have to supply the very things a
  fleet depends on.

## Acceptance criteria

- The contract names every request, event and behaviour the manager depends on, and
  KFM's tests pass against a second program that implements only that.
- A team can run on another unit than the rest of its fleet.

## Risks

- Features that lean on what only Kayden has (a memory the fleet shares, through
  Kayden's memory folders) narrow what another unit can be, or have to become part of
  the contract.
- A contract nobody implements drifts from the code.
- The lowest common denominator: what every unit must do limits what KFM can ask of any.
