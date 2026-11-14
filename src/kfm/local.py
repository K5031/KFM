"""The service in this process: it owns the projects' fleets and their managers. What
`kfm serve` runs, and what tests use."""

from __future__ import annotations

import asyncio
import contextlib
import re
from collections.abc import Callable, Coroutine, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from kfm import draft, harnesses, log, project
from kfm import fleet as records
from kfm.api import Harness
from kfm.config import CONFIG_FILE, HOME, FleetConfig, load, save
from kfm.events import FleetEvent
from kfm.fleet import Fleet, FleetError, Unit
from kfm.manager import Manager, forget, lay_out, place
from kfm.service import FleetState, Happened, Listed, ProjectInfo, Service, Update, Updated

_ID = re.compile(r"[\w.-]+")


@dataclass
class _Held:
    """A fleet this service has read: its record, and its manager while it runs."""

    folder: Path
    fleet: Fleet
    manager: Manager | None = None
    stopping: bool = False


class LocalService(Service):
    """`home`: where projects and the settings are kept (~/.kfm). `harness` and `config`
    replace what the settings name, for tests."""

    def __init__(self, home: Path = HOME, *, harness: Harness | None = None,
                 config: FleetConfig | None = None) -> None:
        self.home, self._harness, self._config = home, harness, config
        self._held: dict[str, _Held] = {}
        self._listeners: list[Callable[[Update], None]] = []
        self._drafts: dict[str, asyncio.Task[draft.Draft]] = {}
        self._tasks: set[asyncio.Task[Any]] = set()

    # ── Projects ─────────────────────────────────────────────────────────

    async def projects(self) -> list[ProjectInfo]:
        found = await asyncio.to_thread(project.every, self.home)
        counts = await asyncio.to_thread(lambda: [len(each.fleets()) for each in found])
        return [self._info(each, fleets) for each, fleets in zip(found, counts, strict=True)]

    async def create_project(self, name: str, work: str) -> ProjectInfo:
        made = await asyncio.to_thread(project.create, self.home, name, Path(work))
        self._tell(Listed())
        return self._info(made, 0)

    async def rename_project(self, project_id: str, name: str) -> None:
        if not name.strip():
            raise ValueError("A project needs a name.")
        folder = self._project(project_id)
        await asyncio.to_thread(lambda: project.rename(project.load(folder), name))
        self._tell(Listed())

    async def delete_project(self, project_id: str) -> None:
        folder = self._project(project_id)
        await self.cancel_draft(project_id)
        for fleet_id in [each for each in self._held if each.startswith(f"{project_id}/")]:
            await self.stop(fleet_id)
            del self._held[fleet_id]
        await asyncio.to_thread(project.delete, folder)
        self._tell(Listed())

    async def draft(self, project_id: str, description: str) -> draft.Draft:
        opened = await asyncio.to_thread(project.load, self._project(project_id))
        config = await self.settings()
        harness = self._harness or await asyncio.to_thread(
            harnesses.load, config.harness, config.harness_options)
        await self.cancel_draft(project_id)
        work = self._drafts[project_id] = asyncio.ensure_future(draft.draft(
            description, config=config, harness=harness, folder=opened.folder / "planner",
            work=opened.work))
        try:
            return await asyncio.shield(work)
        except asyncio.CancelledError:
            if work.cancelled():  # dropped, by cancel_draft
                raise draft.DraftError("The draft was dropped.") from None
            work.cancel()  # whoever asked for it gave up
            raise
        finally:
            if self._drafts.get(project_id) is work:
                del self._drafts[project_id]

    async def cancel_draft(self, project_id: str) -> None:
        if (work := self._drafts.pop(project_id, None)) is not None:
            work.cancel()
            await asyncio.gather(work, return_exceptions=True)

    # ── Fleets ───────────────────────────────────────────────────────────

    async def fleets(self, project_id: str) -> list[str]:
        opened = await asyncio.to_thread(project.load, self._project(project_id))
        found = []
        for folder in await asyncio.to_thread(opened.fleets):
            fleet_id = f"{project_id}/{folder.name}"
            with contextlib.suppress(ValueError, OSError, KeyError):  # of another version
                await self.watch(fleet_id)
                found.append(fleet_id)
        return found

    async def add_fleet(self, project_id: str, name: str, task: str, *, ongoing: bool = True,
                        work: str | None = None, budget_usd: float | None = None) -> str:
        if not name.strip():
            raise ValueError("A fleet needs a name.")
        opened = await asyncio.to_thread(project.load, self._project(project_id))
        config = await self.settings()
        if budget_usd is not None:
            config = replace(config, budget_usd=budget_usd)
        where = Path(work) if work else opened.work
        folder = await asyncio.to_thread(opened.new_fleet)
        fleet_id = f"{project_id}/{folder.name}"
        if not task.strip():
            made = await asyncio.to_thread(lay_out, config, folder, where, name.strip())
            self._held[fleet_id] = _Held(folder, made)
        else:
            manager = Manager(config, folder, where, harness=self._harness,
                              on_event=self._listener(fleet_id), name=name.strip())
            self._held[fleet_id] = _Held(folder, manager.fleet, manager)
            await manager.start()
            self._run(manager.lead(task, ongoing=ongoing))
        self._tell(Listed())
        self._tell(Updated(fleet_id))
        return fleet_id

    async def watch(self, fleet_id: str) -> None:
        if fleet_id not in self._held:
            folder = self._folder(fleet_id)
            self._held[fleet_id] = _Held(folder, await asyncio.to_thread(Fleet.load, folder))

    def fleet(self, fleet_id: str) -> Fleet | None:
        held = self._held.get(fleet_id)
        return held.fleet if held is not None else None

    def state(self, fleet_id: str) -> FleetState:
        held = self._held.get(fleet_id)
        if held is None:
            return FleetState()
        manager = held.manager
        return FleetState(
            manager is not None, held.stopping,
            manager is not None and manager.finished.is_set(),
            manager.config.budget_usd if manager is not None
            else held.fleet.settings.get("budget_usd", 0.0))

    async def history(self, fleet_id: str) -> list[tuple[float, FleetEvent]]:
        held = self._get(fleet_id)
        if held.manager is not None:
            await held.manager.flush()
        return await asyncio.to_thread(log.read, held.folder, held.fleet)

    async def start(self, fleet_id: str, *, budget_usd: float | None = None) -> None:
        held = self._get(fleet_id)
        if held.manager is not None or held.stopping:
            return
        manager = await asyncio.to_thread(lambda: Manager.resumed(
            held.folder, harness=self._harness, on_event=self._listener(fleet_id),
            budget_usd=budget_usd))
        held.manager, held.fleet = manager, manager.fleet
        self._tell(Updated(fleet_id))
        await manager.start()
        self._tell(Updated(fleet_id))

    async def stop(self, fleet_id: str) -> None:
        held = self._held.get(fleet_id)
        if held is None or (manager := held.manager) is None:
            return
        held.manager, held.stopping = None, True
        self._tell(Updated(fleet_id))
        try:
            await manager.aclose()
        finally:
            held.stopping = False
            self._tell(Updated(fleet_id))

    async def rename_fleet(self, fleet_id: str, name: str) -> None:
        if not name.strip():
            raise ValueError("A fleet needs a name.")
        held = self._get(fleet_id)
        if held.manager is not None:
            await held.manager.rename(name.strip())
        else:
            held.fleet.name = name.strip()
            await asyncio.to_thread(held.fleet.save, held.folder)
        self._tell(Listed())
        self._tell(Updated(fleet_id))

    async def delete_fleet(self, fleet_id: str) -> None:
        folder = self._folder(fleet_id)
        await self.stop(fleet_id)
        self._held.pop(fleet_id, None)
        await asyncio.to_thread(records.delete, folder)
        self._tell(Listed())

    # ── Agents ───────────────────────────────────────────────────────────

    async def add_agent(self, fleet_id: str, supervisor: str | None, name: str, task: str, *,
                        ongoing: bool = False, model: str | None = None) -> str:
        held = self._get(fleet_id)
        above = self._unit(held, supervisor) if supervisor else None
        if held.manager is None:
            unit = await asyncio.to_thread(place, held.fleet, held.folder, above, name, task,
                                           ongoing=ongoing, model=model)
        elif above is not None:
            unit = await held.manager.hire(above, name, task, ongoing=ongoing, model=model)
        else:
            raise FleetError("Stop the fleet to give it a lead.")
        self._tell(Updated(fleet_id))
        return unit.id

    async def remove_agent(self, fleet_id: str, unit_id: str) -> None:
        held = self._get(fleet_id)
        unit = self._unit(held, unit_id)
        if held.manager is not None:
            await held.manager.remove(unit)
        else:
            gone = held.fleet.remove(unit)
            await asyncio.to_thread(held.fleet.save, held.folder)
            await asyncio.to_thread(forget, held.folder, gone)
        self._tell(Updated(fleet_id))

    async def rename_agent(self, fleet_id: str, unit_id: str, name: str) -> None:
        held = self._get(fleet_id)
        unit = self._unit(held, unit_id)
        if held.manager is not None:
            await held.manager.rename(name.strip(), unit)
        else:
            unit.name = name.strip()
            await asyncio.to_thread(held.fleet.save, held.folder)
        self._tell(Updated(fleet_id))

    async def set_task(self, fleet_id: str, unit_id: str, task: str) -> None:
        held = self._get(fleet_id)
        unit = self._unit(held, unit_id)
        if held.manager is not None:
            await held.manager.retask(unit, task)
        elif not task.strip():
            raise FleetError("A task cannot be empty.")
        elif unit.session is not None:
            raise FleetError(f"{unit.called} has run: start the fleet to give it another "
                             "task, so that it can be told.")
        else:
            unit.task = task.strip()
            unit.state = "planned"  # one that waited for a task starts with the fleet now
            await asyncio.to_thread(held.fleet.save, held.folder)
        self._tell(Updated(fleet_id))

    async def set_model(self, fleet_id: str, unit_id: str, model: str) -> None:
        held = self._get(fleet_id)
        unit = self._unit(held, unit_id)
        if held.manager is not None:
            await held.manager.set_model(unit, model.strip())
        else:
            unit.model = model.strip() or None
            await asyncio.to_thread(held.fleet.save, held.folder)
        self._tell(Updated(fleet_id))

    async def say(self, fleet_id: str, text: str, unit_id: str | None = None, *,
                  files: Sequence[str] = (), spoken: bool = False) -> None:
        held = self._get(fleet_id)
        if held.manager is None:
            await self.start(fleet_id)
        if held.manager is None:
            raise FleetError("The fleet is stopping: say it again in a moment.")
        to = self._unit(held, unit_id) if unit_id else None
        await held.manager.say(text, to, files=files, spoken=spoken)

    async def approve(self, fleet_id: str, unit_id: str, request_id: str, allow: bool) -> None:
        held = self._get(fleet_id)
        if held.manager is not None:
            await held.manager.approve(self._unit(held, unit_id), request_id, allow)

    # ── Settings, and what happens ───────────────────────────────────────

    async def settings(self) -> FleetConfig:
        return self._config or await asyncio.to_thread(load, self.home / CONFIG_FILE)

    async def save_settings(self, **changes: Any) -> FleetConfig:
        return await asyncio.to_thread(lambda: save(self.home / CONFIG_FILE, **changes))

    def subscribe(self, listener: Callable[[Update], None]) -> Callable[[], None]:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener) if listener in self._listeners else None

    async def aclose(self) -> None:
        for work in list(self._drafts.values()):
            work.cancel()
        await asyncio.gather(*(self.stop(fleet_id) for fleet_id in list(self._held)),
                             return_exceptions=True)
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    def watched(self) -> list[str]:
        """The fleets this service holds."""
        return list(self._held)

    def running(self) -> bool:
        """Whether any fleet runs or is stopping."""
        return any(held.manager is not None or held.stopping for held in self._held.values())

    # ── Internals ────────────────────────────────────────────────────────

    def _info(self, opened: project.Project, fleets: int) -> ProjectInfo:
        mine = f"{opened.folder.name}/"
        running = sum(1 for fleet_id, held in self._held.items()
                      if held.manager is not None and fleet_id.startswith(mine))
        return ProjectInfo(opened.folder.name, opened.name, str(opened.work), fleets, running)

    def _project(self, project_id: str) -> Path:
        if not _ID.fullmatch(project_id) or project_id in (".", ".."):
            raise ValueError(f"{project_id!r} is not a project.")
        return self.home / "projects" / project_id

    def _folder(self, fleet_id: str) -> Path:
        owner, _, name = fleet_id.partition("/")
        if not _ID.fullmatch(name) or name in (".", ".."):
            raise ValueError(f"{fleet_id!r} is not a fleet.")
        return self._project(owner) / "fleets" / name

    def _get(self, fleet_id: str) -> _Held:
        if (held := self._held.get(fleet_id)) is None:
            raise ValueError(f"The fleet {fleet_id} is not being watched.")
        return held

    @staticmethod
    def _unit(held: _Held, unit_id: str) -> Unit:
        if (unit := held.fleet.units.get(unit_id)) is None:
            raise FleetError(f"The fleet has no agent {unit_id}.")
        return unit

    def _listener(self, fleet_id: str) -> Callable[[FleetEvent], None]:
        def happened(event: FleetEvent) -> None:
            self._tell(Happened(fleet_id, event))
            # After the manager has finished with the event: it may end the fleet's task.
            asyncio.get_running_loop().call_soon(self._tell, Updated(fleet_id))

        return happened

    def _tell(self, update: Update) -> None:
        for listener in list(self._listeners):
            listener(update)

    def _run(self, work: Coroutine[Any, Any, Any]) -> None:
        task = asyncio.ensure_future(work)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
