# Decisions

Small choices the design notes leave open.

## Fleet

| Choice | Decision |
|---|---|
| Ids | `u1`, `u2`, ... in the order units are added; the lead is `u1` |
| States | starting, working (between check-ins included), waiting (asked its supervisor), done (reported), idle (stopped without a report), failed, stopped |
| Defaults | top rank 5, 12 units, $10 for the fleet, $2 per unit, check-ins every 5 to 300 seconds |
| Lead's name | "Lead" |
| Team | Inherited from the supervisor unless `delegate` names one |
| A finished unit | Its process is closed; a message to it starts it again on its conversation. Its goal has ended, so there is none to finish: the message says so (`units.answer_note`), and what the unit writes last in that turn is its report, delivered once when the turn ends. A `finish_goal` call from a unit without a goal is not a report (Kayden refuses it) |
| A supervisor that has finished | Still gets its agents' reports, which start it again |
| Fleet tool permission | Granted by the manager for the conversation (`allow_conversation`), once per tool |
| Tool caller | A token per unit in the server's URL (`?unit=`), compared in constant time |
| Fleet folder | `~/.kfm/fleets/<date-time>/`; settings in `~/.kfm/fleet.toml` |
| Fleet record | `fleet.json`, format 3: the units (each with `session`, what its harness opens it on again), `work`, `settings` and `manager_pid`; written on every change of a unit's state or spending, and when the manager starts and stops |
| Resume | Units in starting, working or waiting are started again, in the order they were added; a unit that never got its task is started from the beginning; each of the others is sent `units.RESUMED` |
| Harness | `harness = "kayden"` by default; its options: `config`, the Kayden configuration every unit starts from (default `~/.kayden/config.toml`, taken without its `[frontend]`; the unit's model, permissions and budget are set under `[runtime]`), and `command`, how Kayden is started (default: KFM's own Python with `-m kayden`) |

## Fleet window

Design: [Agent Note](notes/implemented/2026-11-12-fleet-window.md).

| Choice | Decision |
|---|---|
| Record | `log.jsonl` in the fleet's folder: one JSON object per event, `t` (seconds), `type` (the event's class) and its fields, units by id; written in batches off the event loop, in order; a line that cannot be read is left out |
| Timeline | Lines only for states nothing else shows (failed, stopped, idle); starting, working, waiting and done are in the agents list |
| Agents | A tree by supervisor: name and rank, state (accent while at work, warning while waiting, green when done, red when failed), spending; refreshed every second while the fleet runs |
| Status | running, stopping (until its processes have ended), stopped, or finished (the lead has reported and nothing is at work) with what was spent of the limit |
| New fleet | In `<home>/fleets/<date-time>/`, with the settings of `<home>/fleet.toml` and the limit from the form |
| Opening a fleet | The button lists every fleet under `<home>/fleets`, newest first, by start and task; Resume is offered when something was at work |

