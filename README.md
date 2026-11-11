# KFM

Kayden Fleet Manager runs a team of [Kayden](https://github.com/K5031/Kayden) agents on one
task. You tell the lead agent what you want; it plans, hands parts of the work to agents
below it, checks what comes back and reports to you. The fleet works without supervision,
inside the spending limit and permissions you set.

It is an early version: one fleet at a time, followed from a terminal.

```bash
kfm run "Audit this repository's docs and fix what is out of date" --budget 5
```

See [the documentation](docs/README.md) to install and use it, and
[PHILOSOPHY.md](PHILOSOPHY.md) for where it is going.
