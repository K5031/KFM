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
from kfm.api import Harness
from kfm.config import CONFIG_FILE, HOME, FleetConfig, load, save
from kfm.events import FleetEvent, units
from kfm.fleet import Fleet, FleetError, Unit
from kfm.manager import Manager
from kfm.service import FleetState, Happened, Listed, ProjectInfo, Service, Update, Updated

_ID = re.compile(r"[\w.-]+")


class LocalService(Service):
    """`home`: where projects and the settings are kept (~/.kfm). `harness` and `config`
    replace what the settings name, for tests."""

    def __init__(self, home: Path = HOME, *, harness: Harness | None = None,
                 config: FleetConfig | None = None) -> None:
        self.home, self._harness, self._config = home, harness, config
        self._managers: dict[str, Manager] = {}  # of every fleet read, running or not
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
        for fleet_id in [each for each in self._managers if each.startswith(f"{project_id}/")]:
            await self._managers.pop(fleet_id).aclose()
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
        folder = await asyncio.to_thread(opened.new_fleet)
        fleet_id = f"{project_id}/{folder.name}"
        manager = self._managers[fleet_id] = await asyncio.to_thread(
            lambda: Manager.new(config, folder, Path(work) if work else opened.work,
                                name.strip(), **self._how(fleet_id)))
        if task.strip():
            await manager.start()
            self._run(manager.add(None, "", task, ongoing=ongoing))
        self._tell(Listed())
        return fleet_id

    async def watch(self, fleet_id: str) -> None:
        if fleet_id not in self._managers:
            folder = self._folder(fleet_id)
            read = await asyncio.to_thread(
                lambda: Manager.load(folder, **self._how(fleet_id)))
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
                          status, can_start, has_run)

    async def history(self, fleet_id: str) -> tuple[list[FleetEvent], int]:
        manager = self._get(fleet_id)
        upto = manager.count
        await manager.flush()
        kept = await asyncio.to_thread(log.read, manager.folder, upto)
        # What was about an agent since removed went with it.
        return [event for _, event in kept if units(event) <= manager.fleet.units.keys()], upto

    async def start(self, fleet_id: str, *, budget_usd: float | None = None) -> None:
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
        manager = self._get(fleet_id)
        to = self._unit(manager, unit_id) if unit_id else None
        await manager.say(text, to, files=files, spoken=spoken)

    async def approve(self, fleet_id: str, unit_id: str, request_id: str, allow: bool) -> None:
        manager = self._get(fleet_id)
        await manager.approve(self._unit(manager, unit_id), request_id, allow)

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
        await asyncio.gather(*(manager.aclose() for manager in self._managers.values()),
                             return_exceptions=True)
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    def watched(self) -> list[str]:
        """The fleets this service holds."""
        return list(self._managers)

    def running(self) -> bool:
        """Whether any fleet runs or is stopping."""
        return any(manager.running for manager in self._managers.values())

    # ── Internals ────────────────────────────────────────────────────────

    def _info(self, opened: project.Project, fleets: int) -> ProjectInfo:
        mine = f"{opened.folder.name}/"
        running = sum(1 for fleet_id, manager in self._managers.items()
                      if manager.up and fleet_id.startswith(mine))
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

    def _get(self, fleet_id: str) -> Manager:
        if (manager := self._managers.get(fleet_id)) is None:
            raise ValueError(f"The fleet {fleet_id} is not being watched.")
        return manager

    @staticmethod
    def _unit(manager: Manager, unit_id: str) -> Unit:
        if (unit := manager.fleet.units.get(unit_id)) is None:
            raise FleetError(f"The fleet has no agent {unit_id}.")
        return unit

    def _how(self, fleet_id: str) -> dict[str, Any]:
        """What a fleet's manager is made with: its harness, and what it says to whom."""
        def happened(event: FleetEvent) -> None:
            self._tell(Happened(fleet_id, event, self._managers[fleet_id].count))

        return {"harness": self._harness, "on_event": happened,
                "on_change": lambda: self._tell(Updated(fleet_id))}

    def _tell(self, update: Update) -> None:
        for listener in list(self._listeners):
            listener(update)

    def _run(self, work: Coroutine[Any, Any, Any]) -> None:
        task = asyncio.ensure_future(work)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
