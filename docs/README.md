# KFM documentation

KFM (Kayden Fleet Manager) runs a team of Kayden agents on one task. You talk to the lead
agent; it hands work to agents below it and reports back.

## Install

KFM needs [Kayden](https://github.com/K5031/Kayden) checked out next to it (`../Kayden`)
and set up with a provider and an API key, and [uv](https://docs.astral.sh/uv/).

```bash
uv tool install --editable .
```

Without installing, `uv run kfm` works from the checkout.

## Run a fleet

In the folder the agents should work in:

```bash
kfm run "Audit the docs in this repository and fix what is out of date" --budget 5
```

| Option | Does |
|---|---|
| `--budget 5` | The most the whole fleet may spend, in US dollars (default 10) |
| `--unit-budget 1` | The most one agent may spend (default 2) |
| `--top-rank 3` | The lead's rank. 5 (the default) allows four levels below the lead; 1 means the lead works alone |
| `--duty` | Standing work that never ends ("be the support team") instead of a task that finishes |
| `--work PATH` | The folder the agents work in (default: the current one) |
| `--fleet PATH` | Where the fleet keeps its files (default: a new folder in `~/.kfm/fleets/`) |

While it runs, the terminal shows what happens:

```
[Lead K5 u1] working
[Researcher K4 u2] working
[Researcher K4 u2 → Lead] report: Three pages are out of date: ...
[Lead K5 u1 → you] report: Fixed the three pages; details in CHANGES.md.
```

- **Type a message** and press Enter to tell the lead something.
- **Approvals:** agents work freely inside the work folder. When one wants to do something
  outside it, KFM asks you: answer `y` or `n`.
- **Questions:** when the lead needs a decision only you can make, it asks and waits for
  your message. Agents below ask their supervisor, not you.
- **Ctrl+C** stops every agent. A task that finishes ends the run by itself.

```bash
kfm status
```

shows the newest fleet: whether it is running, its agents, what each did and what it
spent.

## Stop a fleet and start it again

A fleet that was stopped (Ctrl+C, a closed terminal, a reboot, or its spending limit)
carries on where it was:

```bash
kfm resume
```

starts the newest fleet again; `--fleet PATH` names another. Every agent that was at work
gets its conversation, plan and role back, is told that the fleet was down, and looks at
what changed meanwhile. Agents that had finished stay finished.

- **More money:** a fleet that stopped at its limit needs a higher one:
  `kfm resume --budget 20`. `--unit-budget` applies to agents started from then on.
- **What is lost:** commands that were running when the fleet stopped, and a question an
  agent had asked without getting an answer; it asks again.

## How a fleet works

- **Ranks.** The lead has the top rank (K5 by default). An agent can hand work only to
  new agents one rank below it, so K1 agents do their work themselves. The fleet uses only
  as many levels as the lead and its agents decide the work needs.
- **Each agent is a separate Kayden** with its own conversation and memory. It sees
  nothing of the others; it knows only its task and what is reported to it.
- **Reports go up.** When an agent finishes, asks a question or fails, its supervisor gets
  a message and decides what to do next: answer, give the task again, or hand it to
  another agent.
- **Spending.** Each agent has its own limit, and the fleet has one for everyone. When the
  fleet's limit is reached, every agent is stopped.

## Settings

`~/.kfm/fleet.toml`, all optional:

```toml
top_rank = 5
max_units = 12            # agents in one fleet
budget_usd = 10.0         # the whole fleet
unit_budget_usd = 2.0     # one agent
permissions = "allow_workspace"

[harness_options]         # for Kayden, the agent program the units run
config = "~/.kayden/config.toml"   # the provider, key and options every agent uses

[models]                  # a model per rank; "default" for the rest
K5 = "claude-opus-5-5"
default = "claude-sonnet-5-5"

[teams]                   # a team's model wins over its rank's
marketing = "claude-sonnet-5-5"
```

Without `[models]`, every agent uses the model in your Kayden configuration.

## Another agent program

Every agent of a fleet is a Kayden unless the settings name another program:

```toml
harness = "my_package.my_harness:factory"   # or the name it is installed under

[harness_options]
# whatever that program needs
```

Such a program has to be written for KFM: it works by itself on an instruction, reports,
asks its supervisor when blocked, and uses the fleet's tools. What exactly it must do is
in `src/kfm/api.py`, and `tests/fakeharness.py` is a small complete example.

## Not there yet

- One fleet per `kfm run`, followed from the terminal only.
- Agents share one folder, and nothing stops two from editing the same file.
