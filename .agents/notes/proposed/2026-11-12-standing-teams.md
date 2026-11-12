# Agent Note: Standing teams

Status: proposed — what KFM lacks to hold a role over a long horizon, and the order to build it in

## Problem

The owner's measure for KFM (2026-11-12): it is not another task doer, which a single
coding agent does more cheaply. It is meant to take on roles and duties over long
horizons, in effect to do the work of a human team ("be the marketing team").

The first runs with real models tested one-off tasks: a lead hands out a small job, gets
it back, checks it. They show that the plumbing works and nothing about whether a fleet
can hold a role.

## What the runs showed

All on 2026-11-12, lead on Claude Opus 5.5, agents below it on Claude Sonnet 5.5.

| Run | Agents | Time | Cost |
|---|---|---|---|
| A haiku written by one agent, checked by the lead | 2 | 30 s | $0.36 |
| Tests and a usage page for a function that broke its spec, unannounced | 3 | 2 min | $0.98 |
| A finished agent sent back once | 2 | 2 min | $0.47 |
| One agent alone, to check a prompt change | 1 | 20 s | $0.12 |

- **Coordination is about half the bill.** The lead spent $0.27 of $0.36 and $0.46 of
  $0.98. While everyone is busy, three agents cost about $0.50 a minute.
- **Information is paid for more than once.** The lead read the files, then each agent
  read them again from nothing. Agents pasted whole files into reports; the lead read the
  report and then opened the file to check it.
- **What an agent learns ends with it.** Each has its own memory; the next agent on the
  same project starts cold.
- **The judgment came from the model.** The lead held back a fix while another agent was
  still reading the file, ran every claim in a report itself, and left the one question
  only the person could answer to the person. Nothing in KFM enforces any of that.
- **A fleet's cost against one agent's**: roughly the same work, plus what a fleet adds
  (each agent starts cold, briefs and reports are written and read, each agent loads its
  own instructions) and minus what it saves (each agent carries only its own part of the
  conversation, and the hands-on work can run on a cheaper model). Small jobs favour one
  agent; the saving grows with the size of the job. Not yet measured side by side.

### A duty, for 15 minutes

A K2 lead on Opus 5.5 was told to be the maintenance team of a small Python project,
with nobody saying what to do next. The project had a function with a fault and no
tests, a readme that named half the code, and an inbox folder for requests. A request
was put into the inbox after 4 minutes and a working function was broken, unannounced,
after 9. The fleet spent $0.50.

- **It found the work.** In the first minute it fixed the fault, added tests and
  completed the readme ($0.27).
- **It built its own watch.** The folder has no version control, so it saved checksums of
  the files and started a shell loop that ends when a file changes or a request arrives.
  The loop runs in the background and its end wakes the agent: it answered the request
  in 38 seconds, the function built and tested ($0.09), and reported the broken function
  to its supervisor 11 seconds after the edit, then restored it ($0.04) and said it would
  undo that if the edit was meant.
- **It made no work for its own sake.** Each check-in on the timer was one look at the
  watch and one line, about 1.5 cents.
- **It changed behaviour nobody asked about**: accented letters in a slug now keep their
  base letter. Tested and documented, and arguably better, but a standing team that
  changes what users rely on without asking is a risk of its own.
- **It never delegated.** The work fitted one agent, so the run shows an agent holding a
  duty, not a team.

## Proposal

What a standing team needs, against what KFM has:

| A standing team needs | KFM today |
|---|---|
| To keep going on a duty | `--duty` exists; first run with a real model on 2026-11-12 |
| To find its own work | Seen once, in that run |
| To survive the manager stopping | `kfm resume` ([note](../implemented/2026-11-12-resume.md)) |
| To remember together | Each agent has its own memory |
| To stay affordable while idle | Check-ins on a timer; each costs a request |
| A record of who told whom what | Only on the terminal; `fleet.json` keeps each agent's last report |
| Approval for actions with real-world effect | Only "outside the work folder" asks |
| A way to reach the person that is not a terminal | Missing |
| Teams with a supervisor and a model of their own | A team has a model; nothing else |

Order of work:

1. **Resume**, which is built: a fleet that vanishes when its terminal closes cannot hold
   a role for a week, and everything long-running sits on this.
2. **Shared team memory** ([note](2026-11-12-shared-team-memory.md)).
3. **A fleet log**: every delivery between agents, with its time, in the fleet's folder.
4. Then, by what duty runs show: waking on events instead of a timer, approvals for
   real-world actions, a model for hands-on work whatever the agent's rank.

Left as they are: the top rank is always K5 and a fleet grows downward as far as the work
needs, and five levels stay the limit. With three agents under each, five levels are 121
agents; width and budget run out first, and every level a report passes through costs a
request and loses detail.

## Alternatives considered

- **Keep proving KFM on one-off tasks.** What the first runs did. A fleet is overhead on
  a task one agent can do, so such runs can only show KFM losing.
- **Use one agent instead of a fleet.** Right for a job that fits in one agent's
  conversation. Not for work that is larger than that, runs for weeks or needs checking
  by someone who did not do it.

## Acceptance criteria

- A fleet holds a role in a test project for a day inside a budget set beforehand, and
  its record shows what it did and why without anyone having watched.

## Risks

- **Quality has no guarantee.** If a person must check everything a fleet does, the cost
  is that person's time. This, more than the price of tokens, decides whether a fleet is
  worth running.
- **Idle cost.** A top-model agent checking in every two minutes costs about $11 a day
  doing nothing (1.5 cents a check-in in the duty run).
- **Scale.** Ten top-model agents busy all day cost more than $100 an hour.
- An agent's good judgment over minutes says little about its judgment over weeks.
