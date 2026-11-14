# Agent Note: Approval for real-world actions

Status: implemented — an action with real-world effect waits for the person through a fleet tool, unless the settings grant them

Follows [one API, many frontends](2026-11-14-one-api-many-frontends.md). PHILOSOPHY.md: publishing, payments and outbound messages require human approval unless explicitly granted.

## Problem

Only an action outside the work folder asked the person. Publishing, paying or messaging someone from inside the work folder, through a tool or an outside call, went through with nobody asked, though PHILOSOPHY.md says these need approval.

## Decision

Every unit has the fleet tool `request_approval(action, details)`. The manager records it as an `Approval` event (`tool` "request_approval", the action and details as its arguments), which the window shows as a card and `kfm watch` as a question, and answers with the existing `approve` path. The tool's call waits on a future, not on the event loop, and returns `approved` or `denied: <reason>`. A fleet that stops first denies what waits. The setting `real_world_approval` (in `fleet.toml`, default true) grants every real-world action when false, with no ask. Every unit's brief says to ask before such an action; the brief has no list of cases.

## Alternatives considered

- **A list of granted kinds** (for example `real_world_approval = ["email"]`). Lost: it needs a list of cases, which the user rejected, and a unit cannot reliably name the kind of its action.
- **Gating in the harness** (block the outside call by its tool name). Lost: a harness knows tools, not what they do outside; the contract would need to know about publishing.
- **A new event type.** Lost: `Approval` already carries what the person must see; a separate event would need its own card and command.

## Consequences

- The agent's work is slower for each real-world action: it waits for the person. A
  missed answer leaves the unit waiting.
- It rests on the agent asking. Nothing stops an agent that does not call the tool from
  acting: this is an instruction and a way to ask, not a gate.
- A wait longer than Kadence lets a tool take in a turn carries on in the background:
  the agent is told the call is still running and gets the answer when it comes, so
  whether it waits is then the model's to get right.
- The approval is not remembered: each request is asked again. A request does not
  outlive the fleet stopping, or the agent that asked being stopped or removed: it is
  refused, and its card then answers nothing.
- Built by a fleet working on a copy of this repository (2026-11-14) and reviewed
  after: checked on the second harness, and a real Kadence unit on a scripted model
  waits for the answer. Not checked: a real model.
