# Agent Note: An agent's page is its conversation

Status: proposed — an agent's page in the window shows that agent's own Kadence conversation, with Kadence's conversation view, in place of KFM's plainer feed

Follows [an agent's live activity](../implemented/2026-11-14-live-agent-activity.md) and
[the fleet window](../implemented/2026-11-12-fleet-window.md).

## Problem

Kadence's window shows a conversation with a good deal of care: a thinking line
("Thinking…", then "Thought for 4s", which opens on the reasoning), a card for each tool
call with its state and result, several calls folded into "3 steps", a checklist for the
agent's plan, copy buttons, code blocks with their language.

KFM's window has none of it (the user, 2026-11-20). It reuses Kadence's window frame,
message box and text rendering, and draws an agent's page from its own pieces: a name
with text under it, boxes for reports. What an agent is doing now is three plain lines
under the feed. Two looks for the same thing drift apart, and what an agent thought is
not kept and cannot be read back, though the agent's own Kadence keeps all of it.

## Proposal

An agent's page is that agent's conversation as Kadence would show it.

Kadence already has the parts: a conversation model that is not tied to the screen
(`desktop.viewmodel.ConversationModel`: history and live events in, items out), a
`RuntimeAPI` over a channel to another Kadence process (`frontends.remote.RemoteRuntime`,
which its own window uses for sub-sessions), and the visual pieces as classes of their
own. A KFM agent is such a headless Kadence.

In this order:

1. **Kadence: the conversation view becomes a part of its own.** What turns the model's
   items into those pieces lives inside `MainWindow`. It moves into a widget both apps
   use. Kadence looks and behaves as before; its window tests are the check. Done first,
   and confirmed by the user before anything in KFM changes.
2. **KFM's service passes through to an agent's own conversation**: a frontend can ask
   an agent's Kadence for its history and gets its events. Optional in the unit
   contract: an agent program that is not Kadence does not offer it, and its page stays
   the feed.
3. **KFM's window: an agent's page is that view.** An adapter makes the service look
   like the channel `RemoteRuntime` expects; Kadence's model and view do the rest.
4. **KFM's own ways stay where they matter.** What the person writes still goes through
   KFM, so a supervisor is told; approvals still go through KFM's Allow, Always allow
   and Deny.

The fleet's own feed, every agent interleaved with what passes between them, has no
like in Kadence and stays, with its live blocks.

## Alternatives considered

- **Reuse Kadence's thinking line and tool card in the live block only.** Small, and
  seen at once. KFM keeps a look of its own that falls behind each time Kadence's
  improves, and what an agent thought is still not kept. It polishes what this replaces.
- **Leave it.** The feed says what happened; it does not look like the app the agents
  are.

## Acceptance criteria

- After step 1, Kadence's window tests pass unchanged and the user sees no difference.
- An agent's page shows its thinking line, tool cards, folded steps and checklist as
  Kadence does, for what it does now and for what it did before the page was opened.
- An agent whose process is closed shows its conversation all the same.
- With the second harness, an agent's page is the feed as today.

## Risks

- **Step 1 moves a lot of code** in the busiest file of Kadence, with no new behaviour
  to show for it.
- **An agent that is asleep** (a finished one, a secretary) has no process to ask. Its
  process is started to read from, with no request to a model, and closed again.
- **KFM leans harder on Kadence's insides.** It imports from Kadence's window code
  already; this deepens it.
