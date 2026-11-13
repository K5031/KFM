# Agent Note: The fleet window

Status: implemented — `kfm-desktop` is a window for KFM: the fleet's agents, what passes between them and the person's part in it, with a record of it on disk

Part of [Standing teams](../proposed/2026-11-12-standing-teams.md).

## Problem

A fleet was followed from a terminal: lines scroll past, a message for the lead is typed
blind between them, and an approval is a `y`. Who supervises whom, what each agent is
doing and what it has spent were in `kfm status`, a second command. Nothing showed a
report in full, and when the terminal closed the lines were gone: of what passed between
the agents, the fleet's record kept each agent's last report.

The owner wants a proper frontend, as Kayden has one.

## Decision

**A record first.** The manager appends every fleet event to `log.jsonl` in the fleet's
folder as it happens (`log.py`): each change of an agent's state, what an agent said,
every report, question, update and failure with who sent it to whom, every approval
asked for and its answer, and what the person said (`events.Told`, `events.Answered`). A
frontend that opens a fleet later shows its history from this file.

**A window on the manager.** `kfm-desktop` (`desktop.py`) is a second frontend beside the
terminal's `Console`, on the same `Manager`: its events in, its methods out. Nothing
about units or harnesses is in it. It is built with PySide6 on Kayden's desktop code: its
event loop, its theme, its Markdown view.

- **Agents** on the left, as the tree of who supervises whom: name, rank, state and
  spending. Choosing one shows only what concerns it.
- **The timeline** in the middle: what an agent said, and each delivery as a card with its
  whole text; an approval as a card with Allow and Deny; a line for an agent that failed
  or was stopped.
- **The message box** at the bottom goes to the lead.
- **The bar** on top: the fleet's task, whether it runs, what it has spent of its limit,
  and Stop or Resume. The window has no title bar, as Kayden's has none: the bar is where
  it is grabbed and holds minimize, maximize and close, and its edges resize it
  (Kayden's `DragArea` and `WindowEdges`).
- **Starting a fleet** is the window's empty state: the task, goal or duty, the folder to
  work in, the limit. **Open a fleet…** lists earlier ones.
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

## Consequences

- The window's tests run on the second harness: nothing of Kayden's units is needed to
  show a fleet. Checked beyond them, without a screen: on the Qt event loop with real
  Kayden units on a scripted model, a fleet started from the form, delegated, reported
  and was closed. Not checked: on a real display, and with a real model.
- The window is the fleet's process: a fleet cannot run with the window closed.
- KFM's window depends on Kayden's desktop code (`theme`, `QtEventLoop`,
  `MarkdownView`, `DragArea`, `WindowEdges`), which Kayden may change for its own needs; the manager stays free of
  it. The `desktop` extra brings all of Kayden's, its speech libraries included.
- One fleet per window. An agent's own conversation cannot be read in it: only what it
  said, reported and asked.
- The record grows for as long as a duty runs; nothing trims it.
