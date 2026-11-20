# Agent Note: An agent's live activity

Status: implemented — a fleet's page shows what each agent is doing as it does it: the tool it uses, its reasoning, the message it is writing

Follows [one API, many frontends](2026-11-14-one-api-many-frontends.md) and
[the fleet window](2026-11-12-fleet-window.md). Used by
[a project's secretary](2026-11-19-a-projects-secretary.md) while she drafts.

## Problem

A frontend saw an agent's messages when each was complete, its reports and questions,
what it was told, and its state and spending. It did not see the agent write, think, or
act. During a long stretch of work an agent looked silent: its state said "working" and
its spending rose, and nothing said whether it was reading files, running the tests or
stuck waiting on a command. In the first run of 2026-11-14 a lead spent four minutes
polling one hung command, and that was found only by reading the agent's own event log
on disk. A secretary drafting a project was a heading and a Cancel button (the user,
2026-11-20: "a black box").

Kadence sends all of it over the channel KFM drives it through. KFM's Kadence harness
passed on a finished assistant message as `Said` and dropped the rest.

## Decision

- **The unit contract has activity events** (`kfm/api.py`): `Thought` and `Wrote`, each
  more of what the agent is thinking or writing; `Doing`, the tool it is using, in
  words; `Discarded`, when a request to the model is made again. They are optional: an
  agent program that cannot give them gives none, and everything else works as it did.
- **The Kadence harness gives them** from the reasoning and text as they stream and
  from each tool's start and end. A tool is put in words as what the agent said it is
  for, then its name and arguments, cut at 160 characters (`harnesses.kadence._in_words`).
- **The manager keeps, for each unit, what it is doing now** (`Manager._live`): the end
  of its thinking and of its writing in the request it is on (4,000 characters), and its
  tool. Thinking and writing end when the message is whole (`Said`); all three end with
  the unit's turn. A tool that is over stays until the unit thinks or writes on, since
  many are over in no time.
- **Frontends are told a few times a second at most** (`service.Live`, every 0.2 s for
  the units that changed; a tool starting is told at once), however fast the model
  writes.
- **None of it is written to the fleet's log.** What an agent wrote, reported and was
  told stays the record.
- **The window shows it under the feed** (`desktop.LiveEntry`), a block for each agent
  at work: its tool, the last 600 characters of its thinking in italics, and what it is
  writing. The message, once whole, takes its place in the feed above. On an agent's
  own page only that agent's shows. While a secretary is waited for, the same is shown
  under "Drafting…".

## Alternatives considered

- **Read the agent's own event log from disk**, as was done by hand. It is Kadence's
  file, in Kadence's format, which a frontend of KFM has no business knowing; another
  harness has no such file.
- **Send activity only for the agents a frontend asked to watch.** The proposal's way
  of bounding what goes over the socket. Telling at most five times a second what each
  changed unit is doing bounds it without a subscription to keep.
- **Only the tool in use, no streaming text or reasoning.** Much less to carry, and it
  answers "is it stuck" on its own. Drafting is where it falls short: a secretary
  mostly thinks.
- **Record it.** A duty that runs for weeks would keep every thought of every agent.

## Consequences

- **A working agent is no longer silent**: a hung command is a tool line that does not
  change.
- **Reasoning is the model's working out**, and reads like a decision. The wiki says so.
- **A tool's arguments are on the screen** of whoever looks, cut but not hidden.
- **Nothing of it can be read later**: it is gone with the turn.
- **The terminal does not show it**: `kfm watch` prints the record.
- **With Kadence, reasoning is what the model's provider streams**: a summary for
  Claude models, nothing where a provider gives none.
- **Tried with scripted agents only**, and a real Kadence agent's tool use on a scripted
  model.
