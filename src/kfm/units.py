"""The words KFM writes to a unit: its brief, and what it is told when the fleet starts
again or when a message reaches it after its goal ended. The tools they name are the
harness's (api.Vocabulary)."""

from __future__ import annotations

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


def hired_note(unit: Unit, words: Vocabulary) -> str:
    """What a supervisor is told when the person puts an agent under it."""
    return (f"[The person added {unit.label} under you. Its task: {unit.task}\nIt has "
            "started on that. Its reports and questions come to you; answer with "
            f"{words.tool('message')}(agent=\"{unit.id}\", text=...).]")


def answer_note(supervisor: Unit | None, words: Vocabulary) -> str:
    """What a unit is told with a message that reaches it after its goal ended."""
    reader = supervisor.name if supervisor is not None else "the person"
    return ("(Your task had ended, so there is no goal to finish now. Deal with this, then "
            f"end your turn: what you write last goes to {reader} as your report, so make it "
            f"complete, and do not also send it with {words.tool('report')}.)")


def brief(unit: Unit, supervisor: Unit | None, placed: list[Unit], words: Vocabulary) -> str:
    """The unit's standing instruction: who it is in the fleet, its task, and how the fleet
    works from where it stands. `placed`: the agents the person put under it, which start
    when it does."""
    lines = [(f"You are {unit.name}, a K{unit.rank} agent in a fleet of AI agents that works "
              "without human supervision. A higher rank supervises the one below it.")]
    if unit.team:
        lines.append(f"Your team: {unit.team}.")
    lines += ["", f"Your task: {unit.task}", "", "How the fleet works:"]
    if supervisor is None:
        lines.append("- A person gave you this task and you are their point of contact. "
                     "They are not watching: decide what you can, and ask them only what "
                     "only they can decide.")
    else:
        lines.append(f"- Your supervisor is {supervisor.name} (K{supervisor.rank}). It gave "
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
            f"- You can hand work to K{unit.rank - 1} agents with {words.tool('delegate')}. Give each "
            "a complete, self-contained task: it sees nothing of your conversation. Their "
            f"reports and questions arrive as messages; answer with {words.tool('message')}, and see "
            f"them all with {words.tool('status')}. Delegate what is big enough to be worth another "
            "agent and do small things yourself. Your job is then to coordinate: plan, "
            "split the work, check what comes back, and keep everyone on the goal. If an "
            "agent fails or stalls, give it the task again, hand it to another, or ask your "
            "own supervisor.")
    else:
        lines.append("- You are the lowest rank: do the work yourself.")
    if placed:
        lines.append("- The person already put these agents under you, and each has started "
                     "on its task:")
        lines += [f"  - {each.label}: {each.task}" for each in placed]
    lines.append("- Every agent works in the same folder. Say in each task which files it "
                 "owns, and do not change files that belong to another agent's task.")
    return "\n".join(lines)
