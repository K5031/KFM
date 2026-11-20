"""The service in this process: it owns the projects and a manager for each fleet it has read. What
`kfm serve` runs, and what tests use."""

from __future__ import annotations

import asyncio
import contextlib
import re
from collections.abc import Callable, Coroutine, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from kfm import draft, harnesses, log, project
from kfm import fleet as records
from kfm.api import Harness, UnitError
from kfm.config import CONFIG_FILE, HOME, FleetConfig, load, save
from kfm.events import Across, Delivered, FleetEvent, Notice, Told, units
from kfm.fleet import Fleet, FleetError, Unit
from kfm.manager import Manager
from kfm.service import (
    FleetState,
    Happened,
    Listed,
    Live,
    ProjectInfo,
    Proposed,
    Service,
    Update,
    Updated,
)
from kfm.units import SECRETARY

_ID = re.compile(r"[\w.-]+")
SECRETARY_MODEL = "secretary"  # in FleetConfig.models: a secretary's, else her rank's
READ_ONLY = "allow_read_only"  # the secretary looks, writes and proposes; she does no work
LATELY, MORE = 4, 30  # lines of a team's record the secretary is shown; of one she asks for
CLIP = 300  # characters of one such line


class LocalService(Service):
    """`home`: where projects and the settings are kept (~/.kfm). `harness` and `config`
    replace what the settings name, for tests."""

    def __init__(self, home: Path = HOME, *, harness: Harness | None = None,
                 config: FleetConfig | None = None) -> None:
        self.home, self._harness, self._config = home, harness, config
        self._managers: dict[str, Manager] = {}  # of every fleet read, running or not
        self._listeners: list[Callable[[Update], None]] = []
        self._proposals: dict[str, draft.Draft] = {}  # by project, until settled
        self._models: list[tuple[str, str, str]] | None = None  # what the program offers
        self._tasks: set[asyncio.Task[Any]] = set()

    # ── Projects ─────────────────────────────────────────────────────────

    async def projects(self) -> list[ProjectInfo]:
        found = await asyncio.to_thread(project.every, self.home)
        counts = await asyncio.to_thread(lambda: [len(each.fleets()) for each in found])
        return [self._info(each, fleets) for each, fleets in zip(found, counts, strict=True)]

    async def create_project(self, name: str, work: str,
                             description: str = "") -> ProjectInfo:
        made = await asyncio.to_thread(project.create, self.home, name, Path(work))
        config = replace(await self.settings(), permissions=READ_ONLY)
        secretary = f"{made.folder.name}/{project.SECRETARY}"
        manager = self._managers[secretary] = await asyncio.to_thread(
            lambda: Manager.new(config, made.secretary, made.work, SECRETARY,
                                **self._how(secretary, made.name)))
        await manager.add(None, SECRETARY, "", model=config.models.get(SECRETARY_MODEL))
        self._tell(Listed())
        if description.strip():
            await manager.say(description.strip())
        return self._info(made, 0)

    async def rename_project(self, project_id: str, name: str) -> None:
        if not name.strip():
            raise ValueError("A project needs a name.")
        folder = self._project(project_id)
        await asyncio.to_thread(lambda: project.rename(project.load(folder), name))
        self._tell(Listed())

    async def delete_project(self, project_id: str) -> None:
        folder = self._project(project_id)
        self._proposals.pop(project_id, None)
        for fleet_id in [each for each in self._managers if each.startswith(f"{project_id}/")]:
            await self._managers.pop(fleet_id).aclose()
        await asyncio.to_thread(project.delete, folder)
        self._tell(Listed())

    async def proposal(self, project_id: str) -> draft.Draft | None:
        return self._proposals.get(project_id)

    async def settle_proposal(self, project_id: str) -> None:
        self._proposals.pop(project_id, None)

    # ── Fleets ───────────────────────────────────────────────────────────

    async def fleets(self, project_id: str) -> list[str]:
        opened = await asyncio.to_thread(project.load, self._project(project_id))
        found = []
        for folder in await asyncio.to_thread(opened.fleets):
            fleet_id = f"{project_id}/{folder.name}"
            with contextlib.suppress(ValueError, OSError, KeyError):  # of another version
                await self.watch(fleet_id)
                found.append(fleet_id)
        await self.watch(f"{project_id}/{project.SECRETARY}")
        return found

    async def add_fleet(self, project_id: str, name: str, task: str, *, ongoing: bool = False,
                        work: str | None = None, budget_usd: float | None = None,
                        model: str | None = None) -> str:
        if not name.strip():
            raise ValueError("A fleet needs a name.")
        if name.strip().lower() == SECRETARY.lower():
            raise ValueError("That name is the secretary's.")
        opened = await asyncio.to_thread(project.load, self._project(project_id))
        await self.fleets(project_id)  # a team is told of the others: they are all read
        config = await self.settings()
        if budget_usd is not None:
            config = replace(config, budget_usd=budget_usd)
        lead_model = (config.chosen(model) or None) if task.strip() else None
        if work is not None:
            await asyncio.to_thread(_own_folder, Path(work), opened.work)
        await self._learn_models(config)
        folder = await asyncio.to_thread(opened.new_fleet)
        fleet_id = f"{project_id}/{folder.name}"
        manager = self._managers[fleet_id] = await asyncio.to_thread(
            lambda: Manager.new(config, folder, Path(work) if work else opened.work,
                                name.strip(), **self._how(fleet_id, opened.name)))
        if task.strip():
            await manager.start()
            self._run(manager.add(None, "", task, ongoing=ongoing, model=lead_model))
        self._tell(Listed())
        return fleet_id

    async def watch(self, fleet_id: str) -> None:
        if fleet_id not in self._managers:
            folder = self._folder(fleet_id)
            owner = await asyncio.to_thread(project.load, folder.parent.parent)
            read = await asyncio.to_thread(
                lambda: Manager.load(folder, **self._how(fleet_id, owner.name)))
            self._managers.setdefault(fleet_id, read)  # it may have been read meanwhile

    def fleet(self, fleet_id: str) -> Fleet | None:
        manager = self._managers.get(fleet_id)
        return manager.fleet if manager is not None else None

    def state(self, fleet_id: str) -> FleetState:
        manager = self._managers.get(fleet_id)
        if manager is None:
            return FleetState()
        units = manager.fleet.units.values()
        at_work = any(unit.active for unit in units)
        has_run = any(unit.session is not None for unit in units)
        finished = manager.finished.is_set()
        status = ("stopping" if manager.stopping
                  else "finished" if manager.running and finished and not at_work
                  else "running" if manager.running
                  else "stopped" if has_run else "not started")
        can_start = not manager.running and (
            at_work or any(unit.state == "planned" for unit in units))
        return FleetState(manager.up, manager.stopping, finished, manager.config.budget_usd,
                          status, can_start, has_run, tuple(manager.asking.values()))

    async def history(self, fleet_id: str) -> tuple[list[FleetEvent], int]:
        manager = self._get(fleet_id)
        upto = manager.count
        await manager.flush()
        kept = await asyncio.to_thread(log.read, manager.folder, upto)
        # What was about an agent since removed went with it.
        return [event for _, event in kept if units(event) <= manager.fleet.units.keys()], upto

    async def start(self, fleet_id: str, *, budget_usd: float | None = None) -> None:
        await self.fleets(fleet_id.partition("/")[0])
        await self._learn_models(self._get(fleet_id).config)
        await self._get(fleet_id).start(budget_usd=budget_usd)

    async def stop(self, fleet_id: str) -> None:
        if (manager := self._managers.get(fleet_id)) is not None:
            await manager.stop()

    async def rename_fleet(self, fleet_id: str, name: str) -> None:
        if not name.strip():
            raise ValueError("A fleet needs a name.")
        await self._get(fleet_id).rename(name.strip())
        self._tell(Listed())

    async def delete_fleet(self, fleet_id: str) -> None:
        folder = self._folder(fleet_id)
        if (manager := self._managers.pop(fleet_id, None)) is not None:
            await manager.aclose()
        await asyncio.to_thread(records.delete, folder)
        self._tell(Listed())

    # ── Agents ───────────────────────────────────────────────────────────

    async def add_agent(self, fleet_id: str, supervisor: str | None, name: str, task: str, *,
                        ongoing: bool = False, model: str | None = None) -> str:
        manager = self._get(fleet_id)
        above = self._unit(manager, supervisor) if supervisor else None
        if manager.config.auto:  # the person starts it: they name its model
            model = manager.config.chosen(model)
        return (await manager.add(above, name, task, ongoing=ongoing, model=model)).id

    async def remove_agent(self, fleet_id: str, unit_id: str) -> None:
        manager = self._get(fleet_id)
        await manager.remove(self._unit(manager, unit_id))

    async def rename_agent(self, fleet_id: str, unit_id: str, name: str) -> None:
        manager = self._get(fleet_id)
        await manager.rename(name.strip(), self._unit(manager, unit_id))

    async def set_task(self, fleet_id: str, unit_id: str, task: str) -> None:
        manager = self._get(fleet_id)
        await manager.retask(self._unit(manager, unit_id), task)

    async def set_model(self, fleet_id: str, unit_id: str, model: str) -> None:
        manager = self._get(fleet_id)
        await manager.set_model(self._unit(manager, unit_id), model.strip())

    async def say(self, fleet_id: str, text: str, unit_id: str | None = None, *,
                  files: Sequence[str] = (), spoken: bool = False) -> None:
        await self.fleets(fleet_id.partition("/")[0])
        manager = self._get(fleet_id)
        to = self._unit(manager, unit_id) if unit_id else None
        await manager.say(text, to, files=files, spoken=spoken)

    async def approve(self, fleet_id: str, unit_id: str, request_id: str, allow: bool, *,
                      always: bool = False) -> None:
        manager = self._get(fleet_id)
        await manager.approve(self._unit(manager, unit_id), request_id, allow, always=always)

    # ── Settings, and what happens ───────────────────────────────────────

    async def models(self) -> list[tuple[str, str, str]]:
        if self._models is None:  # asked once: starting the program to ask takes a moment
            config = await self.settings()
            harness = self._harness or await asyncio.to_thread(
                harnesses.load, config.harness, config.harness_options)
            try:
                self._models = [tuple(each) for each in await harness.model_choices(
                    self.home / "models")]
            except UnitError:
                return []  # it could not say now: asked again the next time
        return list(self._models)

    async def settings(self) -> FleetConfig:
        return self._config or await asyncio.to_thread(load, self.home / CONFIG_FILE)

    async def save_settings(self, **changes: Any) -> FleetConfig:
        config = await asyncio.to_thread(lambda: save(self.home / CONFIG_FILE, **changes))
        for each in await self.projects():  # every fleet there is follows them
            await self.fleets(each.id)
        for fleet_id, manager in list(self._managers.items()):
            if not fleet_id.endswith(f"/{project.SECRETARY}"):
                await manager.adopt(config)
                continue
            await manager.adopt(config, permissions=READ_ONLY)  # she only ever reads
            lead, model = manager.fleet.lead, config.models.get("secretary")
            if lead is not None and lead.model != model:
                await manager.set_model(lead, model)
        return config

    def subscribe(self, listener: Callable[[Update], None]) -> Callable[[], None]:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener) if listener in self._listeners else None

    async def end(self) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await asyncio.gather(*(manager.aclose() for manager in self._managers.values()),
                             return_exceptions=True)
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    def watched(self) -> list[str]:
        """The fleets this service holds."""
        return list(self._managers)

    def running(self) -> bool:
        """Whether any fleet runs or is stopping. A secretary with nothing to do is
        asleep, and holds nothing up."""
        return any(manager.running and not (
                       fleet_id.endswith(f"/{project.SECRETARY}")
                       and not any(unit.active for unit in manager.fleet.units.values()))
                   for fleet_id, manager in self._managers.items())

    # ── Internals ────────────────────────────────────────────────────────

    def _info(self, opened: project.Project, fleets: int) -> ProjectInfo:
        mine, secretary = f"{opened.folder.name}/", f"{opened.folder.name}/{project.SECRETARY}"
        running = sum(1 for fleet_id, manager in self._managers.items()
                      if manager.up and fleet_id.startswith(mine) and fleet_id != secretary)
        waiting = sum(len(manager.asking) + sum(
                          1 for unit in manager.fleet.units.values()
                          if unit.supervisor is None and unit.state == "waiting"
                          and manager.up)
                      for fleet_id, manager in self._managers.items()
                      if fleet_id.startswith(mine))
        return ProjectInfo(opened.folder.name, opened.name, str(opened.work), fleets, running,
                           secretary, waiting)

    def _project(self, project_id: str) -> Path:
        if not _ID.fullmatch(project_id) or project_id in (".", ".."):
            raise ValueError(f"{project_id!r} is not a project.")
        return self.home / "projects" / project_id

    def _folder(self, fleet_id: str) -> Path:
        owner, _, name = fleet_id.partition("/")
        if not _ID.fullmatch(name) or name in (".", ".."):
            raise ValueError(f"{fleet_id!r} is not a fleet.")
        return self._project(owner) / "fleets" / name

    def _get(self, fleet_id: str) -> Manager:
        if (manager := self._managers.get(fleet_id)) is None:
            raise ValueError(f"The fleet {fleet_id} is not being watched.")
        return manager

    @staticmethod
    def _unit(manager: Manager, unit_id: str) -> Unit:
        if (unit := manager.fleet.units.get(unit_id)) is None:
            raise FleetError(f"The fleet has no agent {unit_id}.")
        return unit

    def _how(self, fleet_id: str, project_name: str) -> dict[str, Any]:
        """What a fleet's manager is made with: its harness, what it says to whom, and
        the project it is a team of."""
        def happened(event: FleetEvent) -> None:
            self._tell(Happened(fleet_id, event, self._managers[fleet_id].count))

        return {"harness": self._harness, "on_event": happened,
                "on_change": lambda: self._tell(Updated(fleet_id)),
                "on_live": lambda unit, *live: self._tell(Live(fleet_id, unit, *live)),
                "on_asking": lambda: self._tell(Listed()),  # a project's count changed
                "models": self._known,
                "project": _Side(self, fleet_id, project_name)}

    async def _learn_models(self, config: FleetConfig) -> None:
        """Have the models' names and prices there before a fleet starts whose
        supervisors choose among them: they are in what the hiring tool says."""
        if config.auto_models:
            await self.models()

    def _known(self) -> dict[str, str]:
        """What is known of each model the agent program offers, by its id."""
        return {model_id: ", ".join(part for part in (name, about) if part)
                for model_id, name, about in self._models or ()}

    # ── What a team is given of its project (manager.Project) ────────────

    def _teams(self, fleet_id: str) -> list[tuple[str, str, Manager]]:
        """The other fleets of the fleet's project: what each is called, its id, and its
        manager. The secretary is called that."""
        owner = fleet_id.partition("/")[0]
        return [(SECRETARY if each == f"{owner}/{project.SECRETARY}" else manager.fleet.name,
                 each, manager)
                for each, manager in self._managers.items()
                if each.startswith(f"{owner}/") and each != fleet_id]

    def _listing(self, fleet_id: str) -> str:
        lines = []
        for name, each, manager in self._teams(fleet_id):
            if name == SECRETARY:
                continue
            lead = manager.fleet.lead
            lines.append(f"- {name} ({self.state(each).status}): "
                         f"{_clip(lead.task) if lead is not None and lead.task else 'no task yet'}")
        return "\n".join(lines) or "It has no other teams."

    async def _standing(self, fleet_id: str, team: str | None) -> str:
        teams = [(name, each, manager) for name, each, manager in self._teams(fleet_id)
                 if name != SECRETARY]
        if team is not None:
            teams = [found for found in teams if found[0].lower() == team.strip().lower()]
            if not teams:
                raise FleetError(f"The project has no team {team!r}.")
        parts = []
        for name, each, manager in teams:
            lead, how = manager.fleet.lead, self.state(each)
            head = (f"{name}: {how.status}, spent ${manager.fleet.spent_usd:.2f} of "
                    f"${how.budget_usd:.2f}, {len(manager.fleet.units)} agents.")
            if lead is not None:
                head += f"\n  Lead {lead.label}: {lead.state}. Task: {_clip(lead.task)}"
            await manager.flush()
            kept = await asyncio.to_thread(log.read, manager.folder)
            lines = [line for _, event in kept if (line := _record_line(manager, event))]
            lately = lines[-(MORE if team is not None else LATELY):]
            parts.append("\n".join([head, *(f"  - {line}" for line in lately)]))
        return "\n\n".join(parts) or "The project has no teams."

    async def _send(self, fleet_id: str, sender: Unit, team: str, text: str) -> str:
        mine = self._managers[fleet_id]
        wanted = team.strip().lower()
        found = next(((name, manager) for name, _, manager in self._teams(fleet_id)
                      if name.lower() == wanted), None)
        if found is None:
            names = ", ".join(name for name, _, _ in self._teams(fleet_id)) or "none"
            raise FleetError(f"You have no agent, and the project no team, called {team!r}. "
                             f"The project's other teams: {names}.")
        name, manager = found
        await manager.start()  # a message starts a team, as the person's does
        me = SECRETARY if fleet_id.endswith(f"/{project.SECRETARY}") else mine.fleet.name
        await manager.hear(me, text)
        mine.sent(sender, name, text)
        return name

    def _propose(self, fleet_id: str, teams: Sequence[draft.Team]) -> None:
        owner = fleet_id.partition("/")[0]
        self._proposals[owner] = draft.Draft(tuple(teams),
                                             self._managers[fleet_id].fleet.spent_usd)
        self._tell(Proposed(owner))

    def _tell(self, update: Update) -> None:
        for listener in list(self._listeners):
            listener(update)

    def _run(self, work: Coroutine[Any, Any, Any]) -> None:
        task = asyncio.ensure_future(work)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)


class _Side:
    """`manager.Project` for one fleet of a project held by a LocalService."""

    def __init__(self, service: LocalService, fleet_id: str, name: str) -> None:
        self._service, self._fleet, self.name = service, fleet_id, name
        self.secretary = fleet_id.endswith(f"/{project.SECRETARY}")

    def listing(self) -> str:
        return self._service._listing(self._fleet)

    async def standing(self, team: str | None) -> str:
        return await self._service._standing(self._fleet, team)

    async def send(self, sender: Unit, team: str, text: str) -> str:
        return await self._service._send(self._fleet, sender, team, text)

    def propose(self, teams: Sequence[draft.Team]) -> None:
        self._service._propose(self._fleet, teams)


def _own_folder(work: Path, project_work: Path) -> None:
    """A fleet's own folder is there before its agents start in it: one inside the
    project's folder is made, as the secretary may propose one that is not there yet;
    any other has to exist. ValueError when it does not. Blocking."""
    if work.resolve().is_relative_to(project_work.resolve()):
        work.mkdir(parents=True, exist_ok=True)
    elif not work.is_dir():
        raise ValueError(f"There is no folder {work}.")


def _clip(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= CLIP else text[:CLIP] + "…"


def _record_line(manager: Manager, event: FleetEvent) -> str | None:
    """An event of a team's record as the secretary reads it; None for what says nothing
    of how the team stands."""
    def called(unit_id: str) -> str:
        unit = manager.fleet.units.get(unit_id)
        return unit.label if unit is not None else "an agent since removed"

    match event:
        case Delivered(unit=unit, to=None, kind=kind, text=text):
            return f"{called(unit)} to the person, {kind}: {_clip(text)}"
        case Told(text=text, to=to):
            return f"The person to {called(to) if to else 'the lead'}: {_clip(text)}"
        case Across(unit=unit, team=team, text=text, sent=sent):
            way = f"{called(unit)} to {team}" if sent else f"{team} to {called(unit)}"
            return f"{way}: {_clip(text)}"
        case Notice(text=text):
            return _clip(text)
    return None
