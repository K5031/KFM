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
| Fleet record | `fleet.json`, format 2: the units, `work`, `settings` and `manager_pid`; written on every change of a unit's state or spending, and when the manager starts and stops |
| Resume | Units in starting, working or waiting are started again, in the order they were added; a unit that never got its task is started from the beginning; each of the others is sent `units.RESUMED` |
| Base Kayden configuration | `~/.kayden/config.toml`, without its `[frontend]`; the unit's model, permissions and budget are set under `[runtime]` |
