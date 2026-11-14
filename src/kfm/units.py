"""The words KFM writes to a unit: its brief, and what it is told when the fleet starts
again or when a message reaches it after its goal ended. The tools they name are the
harness's (api.Vocabulary)."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from kfm.api import Vocabulary
from kfm.fleet import Unit

RESUMED = (
    "[The fleet was stopped and has now been started again. Anything you had running in "
    "the background (a watcher, a long command) ended with it: start it again if you still "
    "need it. Look at what changed while the fleet was down, then carry on with your work. "
    "If you were waiting for an answer that has not arrived, ask again.]")


MODEL_CHANGED = (
    "[Your model was changed, which started you again. Anything you had running in the "
    "background ended: start it again if you still need it, then carry on with your work.]")


def from_person(text: str) -> str:
    """The person's message, for a unit that has a supervisor."""
    return ("[Message from the person the fleet works for. Where it differs from what your "
            f"supervisor told you, the person's word stands; your supervisor is told of it.] {text}")


def overheard(unit: Unit, text: str, files: Sequence[str]) -> str:
    """What a supervisor is told when the person speaks to one of its agents."""
    if files:
        text = f"{text}\n(With files: {', '.join(Path(file).name for file in files)}.)"
    return (f"[The person told {unit.label}: {text}\nThe person's word stands where it differs "
            "from yours. You need not do anything about this.]")


RETASKED = ("[Your task was changed by the person the fleet works for. It is in your "
            "instruction now: leave the task you had, and start on the new one.]")


def retasked_note(unit: Unit) -> str:
    """What a supervisor is told when the person gives one of its agents another task."""
    return (f"[The person changed the task of {unit.label}. It is now: {unit.task}\nYou "
            "need not do anything about this.]")


def removed_note(unit: Unit, others: int) -> str:
    """What a supervisor is told when the person takes one of its agents out."""
    also = f" and the {others} under it" if others else ""
    return (f"[The person removed {unit.label}{also} from the fleet. What it was doing is "
            "not being done: take it up yourself or hand it to another agent, if it is "
            "still needed.]")


def hired_note(unit: Unit, words: Vocabulary) -> str:
    """What a supervisor is told when the person puts an agent under it."""
    message = f"{words.tool('message')}(agent=\"{unit.id}\", text=...)"
    if not unit.task:
        return (f"[The person added {unit.label} under you. It has no task yet and has not "
                f"started: give it one with {message}. Its reports and questions then come "
                "to you.]")
    return (f"[The person added {unit.label} under you. Its task: {unit.task}\nIt has "
            f"started on that. Its reports and questions come to you; answer with {message}.]")


def answer_note(supervisor: Unit | None, words: Vocabulary) -> str:
    """What a unit is told with a message that reaches it after its goal ended."""
    reader = "the person" if supervisor is None else supervisor.name or "your supervisor"
    return ("(Your task had ended, so there is no goal to finish now. Deal with this, then "
            f"end your turn: what you write last goes to {reader} as your report, so make it "
            f"complete, and do not also send it with {words.tool('report')}.)")


def brief(unit: Unit, supervisor: Unit | None, placed: list[Unit], words: Vocabulary) -> str:
    """The unit's standing instruction: who it is in the fleet, its task, and how the fleet
    works from where it stands. `placed`: the agents the person put under it, which start
    when it does."""
    who = f"{unit.name}, a" if unit.name else "a"
    lines = [(f"You are {who} K{unit.rank} agent in a fleet of AI agents that works "
              "without human supervision. A higher rank supervises the one below it.")]
    if unit.team:
        lines.append(f"Your team: {unit.team}.")
    lines += ["", f"Your task: {unit.task}", "", "How the fleet works:"]
    if supervisor is None:
        lines.append("- A person gave you this task and you are their point of contact. "
                     "They are not watching: decide what you can, and ask them only what "
                     "only they can decide.")
    else:
        above = (f"{supervisor.name} (K{supervisor.rank})" if supervisor.name
                 else f"a K{supervisor.rank} agent")
        lines.append(f"- Your supervisor is {above}. It gave "
                     "you this task, sees nothing of your conversation, and gets what you "
                     "report.")
    if unit.ongoing:
        lines.append(f"- This is standing work: keep at it. When there is something your "
                     f"supervisor should know, send it with {words.tool('report')}.")
    else:
        lines.append(f"- When the task is done, call {words.finish} with a report: what you did, "
                     "what you found, where the results are, and what is not done. The "
                     "report is all your supervisor gets, so make it complete.")
    lines.append("- When you are blocked on something only your supervisor can decide or "
                 f"provide, call {words.ask} with one clear question; the answer arrives as "
                 "a message.")
    if unit.rank > 1:
        lines.append(
            f"- You supervise: the agents under you do the work, and your own work is to "
            f"judge what the task needs, plan it, hand its parts to K{unit.rank - 1} agents "
            f"with {words.tool('delegate')}, check what comes back and keep everyone on the "
            "goal. Use as many agents as the work needs and no more, and do a thing yourself "
            "only where handing it over would cost more than doing it. Give each agent a "
            "complete, self-contained task: it sees nothing of your conversation. Their "
            f"reports and questions arrive as messages; answer with {words.tool('message')}, and see "
            f"them all with {words.tool('status')}. If an agent fails or stalls, give it the task "
            "again, hand it to another, or ask your own supervisor.")
    else:
        lines.append("- You are the lowest rank: do the work yourself.")
    if placed:
        lines.append("- The person already put these agents under you. One with a task has "
                     f"started on it; one with none waits for you to give it one with "
                     f"{words.tool('message')}:")
        lines += [f"  - {each.label}: {each.task or 'no task yet'}" for each in placed]
    lines.append("- Every agent works in the same folder. Say in each task which files it "
                 "owns, and do not change files that belong to another agent's task.")
    return "\n".join(lines)
