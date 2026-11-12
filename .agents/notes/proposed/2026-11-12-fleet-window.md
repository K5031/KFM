# Agent Note: The fleet window

Status: proposed — a desktop window for KFM: the fleet's agents, what passes between them, and the person's part in it, with a record of it on disk

Part of [Standing teams](2026-11-12-standing-teams.md).

## Problem

A fleet is followed from a terminal: lines scroll past, a message for the lead is typed
blind between them, and an approval is a `y`. Who supervises whom, what each agent is
doing and what it has spent are in `kfm status`, a second command. Nothing shows a
report in full, and when the terminal closes the lines are gone: of what passed between
the agents, the fleet's record keeps each agent's last report.

The owner wants a proper frontend, as Kayden has one.

## Proposal

**A record first.** The manager appends every fleet event to `log.jsonl` in the fleet's
folder as it happens: each change of an agent's state, what the lead said, every report,
question, update and failure with who sent it to whom, every approval asked for, and
what the person said. A frontend that opens a fleet later shows its history from this
file; so does anyone who reads it.

**A window on the manager.** `kfm-desktop` is a second frontend beside the terminal's
`Console`, on the same `Manager`: its events in, its methods out. Nothing about units or
harnesses is in it. It is built with PySide6 on Kayden's desktop code: its event loop,
its theme, its Markdown view.

- **Agents** on the left, as the tree of who supervises whom: name, rank, state and
  spending. Choosing one shows only what concerns it.
- **The timeline** in the middle: what the lead said and each delivery as a card with its
  whole text; state changes as single lines; an approval as a card with Allow and Deny.
- **The message box** at the bottom goes to the lead.
- **The fleet's bar** on top: its task, whether it runs, what it has spent of its limit,
  and Stop or Resume.
- **Starting a fleet** is the window's empty state: the task, goal or duty, the folder to
  work in, the limit.
- The window runs the fleet: closing it stops the fleet, which is resumed from the window
  or with `kfm resume`.

## Alternatives considered

- **A terminal interface** (Textual, as Kayden's). No new dependency for people on a
  server, but a tree of agents beside a timeline of long reports is what a terminal shows
  worst, and the owner uses Kayden's window.
- **A web page served by the manager.** Reachable from anywhere, and a fleet could run
  without a window. A second stack (HTTP, a page, its own look) where Kayden's desktop
  code already gives the loop, the theme and the widgets.
- **A fleet view inside Kayden's own window.** Kayden would then know fleets, which it
  must not.

## Acceptance criteria

- A fleet is started, followed, answered, stopped and resumed without a terminal.
- A fleet opened after it stopped shows what happened in it, from its record.
- The window's tests run on the second harness: nothing of Kayden's units is needed to
  show a fleet.

## Risks

- The window is the fleet's process: a fleet cannot yet run with the window closed.
- KFM's window depends on Kayden's desktop code, which Kayden may change for its own
  needs; the manager stays free of it.
- One fleet per window.
- The record grows for as long as a duty runs; nothing trims it.
