# Agent Note: Close to tray

Status: implemented — closing the window keeps KFM running in the system tray; Quit quits

## Problem

Closing the window quit KFM. Its fleets run on in the background service, but the window
is what sends desktop notifications (an approval waits, a lead asks the user), so with it
closed the user heard nothing. Kaden closes to the tray for the same reason, and the user
asked for KFM to do the same.

## Decision

- Where the desktop has a system tray (`tray.py`), closing the window hides it: KFM keeps
  running, its notifications too. A tray icon (KFM's) brings it back with a click, and
  its menu has **Open KFM** and **Quit**.
- **Quit** (the menu under the user's name, the tray menu) quits, as closing did:
  `FleetWindow.quit()` lets go of the service and closes.
- The first close in a run says so once, as a tray message: KFM is still running.
- Without a system tray, closing quits as before.

## Alternatives considered

- **Leave closing as quitting.** Fleets go on either way, but notifications stop with the
  window, which is when the user is away and needs them.
- **Minimize instead of hide.** The window stays in the taskbar, which is not "closed".
- **A setting.** One more choice for something with a clear default; Quit is always there.

## Consequences

- Notifications go on with the window closed.
- A user who expects closing to quit finds KFM still running: the one-time message and
  the icon say so.
- `FleetWindow.close()` no longer means quitting where there is a tray: what ends KFM
  calls `quit()`.
