# Agent Note: One API, many frontends

Status: implemented — a KFM service owns the projects and runs the fleets; the window and the command line are frontends of one contract

Follows [the fleet window](2026-11-12-fleet-window.md),
[projects](2026-11-13-projects.md) and
[editing the tree](2026-11-13-editing-the-tree.md). Part of
[standing teams](../proposed/2026-11-12-standing-teams.md).

## Problem

A fleet that lives inside whatever started it, the window with its managers and
`kfm run` with its own, can be reached by nothing else. Then:

- Closing the window stops every fleet. A standing team, which is what KFM is for, needs
  a window left open on someone's desk.
- The window cannot show a fleet the command line started, and a second terminal cannot
  message one.
- Every feature is built in the window alone. From the command line a person can start,
  resume and look at a fleet; projects, building a fleet by hand, adding, removing and
  renaming agents, another task or model, a message to one agent and files exist only in
  the window. What a frontend can do is decided by where the code happened to be written.

Kadence has the answer for one agent: a runtime behind `RuntimeAPI`, and the desktop
window, the terminal UI and the headless channel as frontends of it.

## Decision

**One contract.** `kfm/service.py` is `Service`, with everything a person can do and the
plain records it answers with. It is normative for frontends as `kfm/api.py` is for
agent programs.

- Projects: list, create, rename, delete, draft the fleets of a described one.
- Fleets: list, add (on a task, or with no agents), start (with more money if wanted),
  stop, rename, delete.
- Agents: add, remove, rename, another task, another model, a message with files, the
  answer to an approval.
- State: a watched fleet's record and state, its history, and one stream of what
  happens (`Happened`, `Updated`, `Listed`), for every fleet.
- Settings: read and save.

**One owner.** `kfm/local.py` implements it in a process: it holds the managers. Tests
use it directly, with the second harness.

**One service.** `kfm serve` runs it and answers the contract on a Unix socket in KFM's
home (`kfm/server.py`), one JSON object a line, as Kadence's headless channel speaks.
Any number of frontends attach. One that finds no service starts it, detached. It ends
when no fleet runs and no frontend is attached. The first line of a connection names
the contract's version, and a frontend of another version is told so.

**Frontends are clients.** `kfm/remote.py` is `Service` over that socket. The window
(`desktop.py`) and the command line (`__main__.py`) take a `Service` and nothing else:
neither imports the manager or the service in a process, and both know a project and a
fleet by the id the service gives it, never by where its record is kept.

- A client keeps the record of each fleet it watches (`fleet.Fleet`, which is plain
  data) as the service last sent it. The manager says when the record or the fleet's
  state changed, and the service sends the record then, ahead of any event that tells
  of the change. Events are plain data too: they name units by id, and a frontend reads
  who that is in the record. The window's chart, agents list and timeline read a
  `Fleet`.
- Each event has a number, its place in the fleet's log. `history` says which number it
  reaches, so a frontend that opens a fleet shows the history and then what has a
  higher number.
- The command line has a command for each part of the contract. `kfm run` puts its
  fleet in a project, so the window shows it.

**Letting go of a frontend leaves fleets running.** Stopping is something a person does.

## Alternatives considered

- **Add command-line commands one at a time.** Each would be a second copy of code that
  is in the window, and a terminal still could not reach a fleet the window runs.
- **One process per fleet**, as a Kadence instance is one process. Projects, the list of
  fleets and settings then belong to no process, and a frontend holds a connection per
  fleet and finds them by scanning folders.
- **Kadence's stdio channel as it is.** It speaks to a child process, which ends with its
  parent: the opposite of what is wanted. Its line format is taken; its transport is
  not.
- **HTTP or WebSocket on localhost** with uvicorn, which KFM already has. A port on
  localhost is reachable by every local program and by web pages, so it needs a token
  and its own care; a socket file is guarded by the file system.
- **Events with whole units inside.** Every event would carry each unit's task and
  state, and a frontend would still need the record for agents that no event has
  mentioned.
- **Events that refer to the record's unit objects**, with the record sent before every
  event so that they can be found (built first). Decoding failed for a unit the copy
  lacked, spending needed a check every second, and a page had to compare the end of
  its history with the events that came meanwhile to guess which were the same.

## Consequences

- A fleet started in the window keeps running when the window closes and is there, with
  what happened meanwhile, when it opens again; one started with `kfm run --detach`
  appears in the window, and a message from either reaches its lead.
- A feature is added to the contract once and every frontend can have it. The command
  line does everything the window does.
- A fleet that nobody watches keeps spending. Its limit holds and `kfm status` shows what
  runs, but closing the window no longer stops the money.
- If the service dies, every fleet's processes go with it; the records stay and the
  fleets can be started again.
- An approval asked while no frontend is attached waits until one is.
- Unix sockets: no Windows.
- `kfm run` lost `--fleet`, `--config`, `--unit-budget` and `--top-rank`: where a fleet
  is kept and what it gets are the service's.
- Checked on the second harness, in process and over the socket with two frontends, in
  the window's tests, and with the real command and real Kadence units on a scripted
  model. Not checked: with a real model, on a real display, or a service left running
  across an upgrade.
