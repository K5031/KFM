# Agent Note: An agent's live activity

Status: proposed — an agent's page shows what it is doing as it does it: its text as it is written, its reasoning, each tool it uses

Follows [one API, many frontends](../implemented/2026-11-14-one-api-many-frontends.md)
and [the fleet window](../implemented/2026-11-12-fleet-window.md).

## Problem

A frontend sees an agent's messages when each is complete, its reports and questions,
what it is told, and its state and spending. It does not see the agent write, think, or
act. During a long stretch of work an agent looks silent: its state says "working" and
its spending rises, and nothing says whether it is reading files, running the tests or
stuck waiting on a command. In the first run of 2026-11-14 a lead spent four minutes
polling one hung command, and that was found only by reading the agent's own event log
on disk.

Kadence already sends all of it over the channel KFM drives it through: text as it
streams, reasoning, and every tool call with its start, end and result. KFM's Kadence
harness passes on a finished assistant message as `Said` and drops the rest.

## Proposal

- **The unit contract gains activity events** (`kfm/api.py`): text being written,
  reasoning, a tool started and finished (its name, a short form of its arguments, how
  it ended). They are optional: a harness that cannot give them gives none, and
  everything else works as now.
- **The manager passes them on as fleet events of their own kind**, and the service
  sends them to frontends like any other.
- **They are not written to the fleet's log.** They are for watching, and a duty that
  runs for weeks would otherwise record every keystroke of every agent. What an agent
  wrote, reported and was told stays the record.
- **A frontend shows them on the agent's own page**: the message being written where
  the finished one will stand, and a line for the tool in use ("running: uv run pytest,
  40 s"). The whole fleet's feed stays as it is.

## Alternatives considered

- **Read the agent's own event log from disk**, as was done by hand. It is Kadence's
  file, in Kadence's format, which a frontend of KFM has no business knowing; another
  harness has no such file.
- **Send everything always.** Text deltas from a dozen agents is a great deal over the
  socket to frontends that are showing the chart. Activity should be sent for the
  agents a frontend has asked to watch.
- **Only the tool in use, no streaming text or reasoning.** Much less to carry, and it
  answers "is it stuck" on its own. A sound first step if the whole proves heavy.

## Acceptance criteria

- With an agent's page open, a command it runs appears while it runs, and a message
  appears as it is written.
- Nothing of it is in `log.jsonl`.
- A harness that sends no activity changes nothing: the second harness's tests pass
  untouched.
- A frontend that watches no agent receives none of it.

## Risks

- Reasoning is the model's private working: showing it invites reading it as what the
  agent decided.
- Volume, and a frontend that redraws on every delta.
- A tool's arguments can hold what should not be shown to whoever looks at the screen.
