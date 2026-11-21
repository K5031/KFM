# Agent Note: An agent's page is its conversation

Status: implemented — in a fleet that runs, an agent's page in the window is that agent's own Kadence conversation, drawn by Kadence's conversation view

Follows [an agent's live activity](2026-11-14-live-agent-activity.md) and
[the fleet window](2026-11-12-fleet-window.md). Rests on
[KFM runs Kadence only](2026-11-20-kfm-runs-kadence-only.md).

## Problem

Kadence's window shows a conversation with a good deal of care: a thinking line
("Thinking…", then "Thought for 4s", which opens on the reasoning), a card for each tool
call with its state and result, several calls folded into "3 steps", a checklist for the
agent's plan, copy buttons, code blocks with their language.

KFM's window had none of it (the user, 2026-11-20). It drew an agent's page from its own
pieces: a name with text under it, boxes for reports, and three plain lines for what the
agent was doing. Two looks for the same thing drift apart, what an agent thought was not
kept, and what KFM did not draw was lost: an agent that ended its turn with Kadence's
card (a recap, a question and buttons that answer it) in place of text showed nothing.

## Decision

An agent's page is that agent's conversation as Kadence shows it.

- **Kadence's conversation view is a widget of its own**
  (`kadence.frontends.desktop.window.ConversationView`): what draws a
  `ConversationModel`. Kadence's window builds one; so does KFM's.
- **The service passes an agent's conversation through.** `Service.follow(fleet, unit)`
  has what happens in the agent's Kadence sent as `service.Within`: the events of
  Kadence's headless channel, in its JSON. `Service.ask` sends that Kadence a request of
  the same channel, which is how its history is read. It is Kadence's channel that is
  carried, not something the unit contract describes (`Manager.follow`, `Manager.ask`,
  and the Kadence session's `listeners` and `ask`).
- **A followed agent's process is kept up**, and started on its conversation when it is
  closed, which asks its model nothing. `unfollow` closes one that had ended. A frontend
  that goes away lets go of what it followed.
- **The window** (`kfm.conversation`) gives Kadence's `RemoteRuntime` a channel that is
  the service (`UnitChannel`), and Kadence's model, controller and view do the rest
  (`AgentPage`). `FleetView` shows it in place of its feed when an agent is chosen,
  and for a secretary always.
- **KFM's own ways stay where they matter** (`AgentController`): what the person writes
  on the page, and a click on a card's answer, go through `Service.say`, so a supervisor
  is told and the fleet's record has it; an answer to a permission request goes through
  `Service.approve`. The strip of what waits for the person leaves out what the open
  page asks in the agent's own words.
- **Where there is nothing to follow, the page is the feed as before**: a fleet that
  does not run, an agent that has not begun, a scripted unit of the tests. It is looked
  at again whenever the fleet changes, and after the agent's process ended.

The fleet's own feed, every agent interleaved with what passes between them, has no like
in Kadence and stays, with its live blocks. A card an agent ends its turn with is said
there in words (`harnesses.kadence._card`).

## Alternatives considered

- **Reuse Kadence's thinking line and tool card in the live block only.** Small, and
  seen at once. KFM keeps a look of its own that falls behind each time Kadence's
  improves, and what an agent thought is still not kept.
- **Let the agent program provide its page**, with KFM's feed where it offers none. See
  [KFM runs Kadence only](2026-11-20-kfm-runs-kadence-only.md).
- **Read a stopped fleet's agents too**, from a Kadence started on the conversation for
  that alone, or from Kadence's store. Built the first way on 2026-11-21 and taken out
  again (the user): the feed already says what a stopped agent did, "stopped" would no
  longer mean that nothing of the fleet runs, and keeping such a process from acting
  rests on knowing every way a Kadence wakes by itself. Reading the store would tie KFM
  to Kadence's files.
- **Leave it.** The feed says what happened; it does not look like the app the agents
  are, and loses what it does not know of.

## Consequences

- **What Kadence shows, KFM shows**, and what Kadence learns to show comes with it.
- **A stopped fleet's agents show the feed**, not their conversations: nothing is
  started to read from while the fleet does not run.
- **A followed agent's process stays up** for as long as its page is open, where it
  would have been closed when its turn ended.
- **Several windows may show the same agent.** What happens in its conversation goes
  to every frontend, and the server lets go of it when the last one that follows it
  leaves or closes (`Server._followers`).
- **KFM leans harder on Kadence's insides**: its window code, its remote runtime and
  the wire format of its channel.
- **Tried with a scripted model only** (a real Kadence process, `tests/fakecore.py`).
