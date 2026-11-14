# Agent Note: One API, many frontends

Status: proposed — a KFM service owns the projects and runs the fleets; the app, the command line and any later frontend are clients of one API

Follows [the fleet window](../implemented/2026-11-12-fleet-window.md),
[projects](../implemented/2026-11-13-projects.md) and
[editing the tree](../implemented/2026-11-13-editing-the-tree.md). Part of
[standing teams](2026-11-12-standing-teams.md).

## Problem

A fleet lives inside whatever started it. The window makes its own managers and
`kfm run` makes its own, and nothing else can reach a fleet that runs. So:

- Closing the window stops every fleet. A standing team, which is what KFM is for, needs
  a window left open on someone's desk.
- The window cannot show a fleet the command line started, and a second terminal cannot
  message one.
- Every feature is built in the window alone. From the command line a person can start,
  resume and look at a fleet; projects, building a fleet by hand, adding, removing and
  renaming agents, another task or model, a message to one agent and files exist only in
  the window. What a frontend can do is decided by where the code happened to be written.

Kayden has the answer for one agent: a runtime behind `RuntimeAPI`, and the desktop
window, the terminal UI and the headless channel as frontends of it.

## Proposal

**One API.** `kfm/service.py` is a protocol, `Service`, with everything a person can do,
and the plain records it answers with. It is normative for frontends as `kfm/api.py` is
for agent programs.

- Projects: list, create, rename, delete, draft the fleets of a described one.
- Fleets: list, add (on a task, or with no agents), start, stop, rename, delete.
- Agents: add, remove, rename, another task, another model, a message (with files),
  the answer to an approval.
- State: a fleet's record and its history; one stream of what happens, for every fleet.
- Settings: read and save.

**One owner.** `kfm/local.py` implements it in-process: it holds the managers, as the
window does today. It is what tests use, with the second harness.

**One service.** `kfm serve` runs that implementation and answers the API on a Unix
socket in KFM's home (`service.sock`), one JSON object a line, the way Kayden's headless
channel speaks: a request with an id gets a result or an error, and what happens arrives
as lines with no id. Any number of clients attach. A client that finds no service starts
one, detached. The service ends when no fleet runs and no client is attached.

**Frontends are clients.** `kfm/remote.py` is `Service` over that socket. The window
and the command line take a `Service` and nothing else: neither imports the manager.

- A client keeps a copy of each fleet it looks at (`fleet.Fleet`, which is plain data).
  The service sends a fleet's record when it changes and its events with units as ids;
  the client updates its copy in place and hands frontends the same `FleetEvent`s the
  manager emits. The window's chart, agents list and timeline keep reading a `Fleet`.
- The command line gets a command for each part of the API, and `kfm run` puts its fleet
  in a project (the one whose folder it is run in, made if there is none) so that the
  window shows it.

**Closing a frontend leaves fleets running.** Stopping is something a person does, per
fleet or for all of them.

## Alternatives considered

- **Add command-line commands one at a time.** Each would be a second copy of code that
  is in the window, and a terminal still could not reach a fleet the window runs.
- **One process per fleet**, as a Kayden instance is one process. Projects, the list of
  fleets and settings then belong to no process, and a frontend holds a connection per
  fleet and finds them by scanning folders.
- **Kayden's stdio channel as it is.** It speaks to a child process, which ends with its
  parent: the opposite of what is wanted. Its line format is taken; its transport is
  not.
- **HTTP or WebSocket on localhost** with uvicorn, which KFM already has. A port on
  localhost is reachable by every local program and by web pages, so it needs a token
  and its own care; a socket file is guarded by the file system.
- **Events with whole units inside.** Simpler to decode, but every event would carry
  each unit's task and state, and a frontend would still need the record for agents that
  no event has mentioned.

## Acceptance criteria

- A fleet started in the window keeps running when the window closes, and is there,
  with what happened meanwhile, when it opens again.
- A fleet started with `kfm run` appears in the window while it runs, and a message
  typed in either reaches the lead.
- Everything the window does is a call on `Service`; `desktop.py` and `__main__.py` do
  not import `manager`.
- The window's tests run on the in-process implementation; the socket is tested once,
  end to end, with the second harness.
- Two clients on one fleet both see every event once.

## Risks

- A fleet that nobody watches keeps spending. The limit holds, and `kfm status` shows
  what runs, but closing the window no longer stops the money.
- A service that dies takes every fleet's processes with it; the records stay and the
  fleets can be started again, as after a crash today.
- A service left over from an older version answers a newer client: the first line of a
  connection has to name the API's version, and a mismatch has to be said plainly.
- Unix sockets: no Windows.
- An approval asked while no frontend is attached waits until one is.
