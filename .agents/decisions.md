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
| New fleet screen | The logo, a heading that names the project ("New fleet in Acme"), the fleet's name ("Fleet name", where the project's screen says "Project name": the two screens look alike and a project's name was taken for the fleet's), one box for what it does (Kayden's `MessageBox`: Enter starts, Shift+Enter a new line) and along its lower edge Task or Duty (Duty by default: a fleet in a project holds a role), the folder, the limit ("Up to $", 10 unless the settings say otherwise) and the start button; a few words under it say what the chosen kind means. It is reached with Add fleet on the chart, also in a project with no fleet: a new project opens on its chart, so the person sees the project before being asked for a fleet. No text explains the product |
| Work folder | Chosen by the person for each project: none is suggested or remembered, and KFM makes none (a default `~/kfm-work` was built first and was a folder nobody asked for). A project without one, with one that does not exist, or with the home folder is refused with a line under the box. A new fleet has its project's folder unless another is chosen |
| New fleet | In `<project>/fleets/<date-time>/` (with `-2`, `-3` when that exists), with the settings of `<home>/fleet.toml` and the limit from its screen |
| Projects | `<home>/projects/<date-time>/project.json` (`format` 1: name, work folder). Listed in the sidebar, newest first, drawn by Kayden's `RowDelegate` with the number of fleets and how many run; the window opens on the newest. Fleets of a project left open keep running |
| Draft | A new project's box takes what it should do; left empty, the project is filled by hand. The drafting agent is unit `u1` "Planner" at the top rank, in `<project>/planner/`, with the settings' model for that rank, their permissions and one agent's limit; its tool server has the one tool `propose(teams)`, each team a name, what it does and `ongoing` (true unless said), and refuses an empty list and a repeated name. The draft page's limit is for each fleet. Start is refused while a fleet lacks a name or what it does, or two share a name |
| Chart | A fleet's tree by `chart.slots`: an agent with nobody under it takes a column, a supervisor stands over the middle of its agents, the row is the rank's distance from the top. Agents are 168 x 56 px boxes joined by elbow lines; fleets stand 56 px apart, in the order they were made; "Add fleet" follows the last. Redrawn every second and on every event, keeping the boxes |
| Agent menu | Right-click on a chart box: "Add agent…" (not for a K1) and "Model…", both only while the fleet runs. Add agent is a dialog with name, task, Task or Duty (Task unless chosen), and model; what the fleet refuses is shown in it and it stays open. An agent's own model is in its box's tooltip. `fleet.json` is format 5 with each unit's `model` |
| Bar | 48 px. On the chart: the project's name, how many fleets run and what the project has spent. In a fleet: an arrow back to the chart, the fleet's name, its status, Stop or Resume |
| Column | One column of at most 860 px in the middle of the window; the message box below it is as wide |
| App menu | `app_menu.py`, as Kayden's: `io.github.k5031.kfm.desktop` and the icon (the user's logo, 512 x 512 with clear corners and the same margin around its tile as Kayden's icon, in `hicolor/512x512/apps`) under the user's XDG data directory; the entry runs this install's Python with `-m kfm.desktop`; the icon's file name carries a hash of its content |

## What agents are told

The user's rule (2026-11-12): nothing is hardcoded for a case; it is brittle and does not
generalize.

| Choice | Decision |
|---|---|
| Order | What an agent should do is reached for in this order: the model's own judgment; structure, so the right behaviour falls out of how the fleet works; one general principle, stated once; never a rule for a specific case or a patch for one observed mistake |
| The brief | Situation only, the same for any kind of fleet: who the agent is and its rank, its task, who gave it and that nobody watches, how to finish and how to ask, whether it can hand work down, that everyone shares one folder. What a unit is told when the fleet resumes, and when a message reaches it after its goal ended, are facts it cannot know otherwise |
| Not added | "Report where the results are, do not paste them" and "pass on what you know": the lead did the second unasked, and the first cost cents in runs of two minutes. Nothing is added on a hunch |
| Known soft spot | "Say in each task which files it owns" stands in for a mechanism that is missing: nothing stops two agents editing one file |

