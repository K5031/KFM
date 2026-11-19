"""The words KFM writes to a unit: its brief, and what it is told when something about it
or its fleet changes. They state what is so (who it is, what happened, what each tool
does) and leave what to do about it to the agent: no instruction on how to work is given
here. The tools they name are the harness's (api.Vocabulary)."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from kfm.api import Vocabulary
from kfm.fleet import Unit

RESUMED = (
    "[The fleet was stopped and has now been started again. Anything you had running in "
    "the background (a watcher, a long command) ended with it, and a question you had "
    "asked that was not answered was not kept.]")


MODEL_CHANGED = (
    "[Your model was changed, which started you again. Anything you had running in the "
    "background ended.]")


def from_person(text: str) -> str:
    """The person's message, for a unit that has a supervisor."""
    return ("[Message from the person the fleet works for. Your supervisor is told of "
            f"it.] {text}")


def overheard(unit: Unit, text: str, files: Sequence[str]) -> str:
    """What a supervisor is told when the person speaks to one of its agents."""
    if files:
        text = f"{text}\n(With files: {', '.join(Path(file).name for file in files)}.)"
    return f"[The person told {unit.label}: {text}]"


RETASKED = ("[Your task was changed by the person the fleet works for. The new one is in "
            "your instruction.]")


def retasked_note(unit: Unit) -> str:
    """What a supervisor is told when the person gives one of its agents another task."""
    return f"[The person changed the task of {unit.label}. It is now: {unit.task}]"


def removed_note(unit: Unit, others: int) -> str:
    """What a supervisor is told when the person takes one of its agents out."""
    also = f" and the {others} under it" if others else ""
    return (f"[The person removed {unit.label}{also} from the fleet. What it was doing is "
            "not being done.]")


def hired_note(unit: Unit, words: Vocabulary) -> str:
    """What a supervisor is told when the person puts an agent under it."""
    message = f"{words.tool('message')}(agent=\"{unit.id}\", text=...)"
    if not unit.task:
        return (f"[The person added {unit.label} under you. It has no task yet and has not "
                f"started: the first message it gets is its task, and {message} reaches "
                "it. Its reports and questions then come to you.]")
    return (f"[The person added {unit.label} under you. Its task: {unit.task}\nIt has "
            f"started on that. Its reports and questions come to you; {message} reaches it.]")


SECRETARY = "Secretary"  # what a project's secretary, and her fleet of one, are called


def from_team(team: str, text: str) -> str:
    """A message from another team's lead, or from the project's secretary."""
    who = "the project's secretary" if team == SECRETARY else f"the lead of the team {team}"
    return f"[Message from {who}] {text}"


def secretary_brief(unit: Unit, project: str, work: Path, words: Vocabulary) -> str:
    """The standing instruction of a project's secretary."""
    began = (f"The project began with the person saying:\n\n{unit.task}\n\n"
             if unit.task else "")
    return (
        f"You are the secretary of {project}, a project of teams of AI agents that work "
        "without human supervision.\n\n"
        f"{began}"
        "How the project works:\n"
        "- The project belongs to a person, who talks to you about it. Their messages "
        "arrive here, and they read this conversation: what you write is shown to them "
        f"as you write it. {words.finish} ends your turn on a message, and its report is "
        "shown to them too, in a box right under what you wrote, so what is in both is "
        f"read twice. {words.ask} puts a question to them.\n"
        "- A team has a lead agent that takes on what the team is there to do and hires "
        "agents under it as the work needs, so a team's size is its lead's to decide. A "
        "team's lead runs, and is paid for, for as long as the team does.\n"
        "- You are not above the teams. Each lead answers to the person, who can also "
        "write to any of them without you. No report of theirs comes to you.\n"
        "- Between messages your process is closed, and you keep this conversation.\n"
        f"- {words.tool('teams')} shows how each team stands, from the record that is "
        "kept of it: what its lead is doing, what it has spent, and what was reported, "
        "asked and said to it lately, the person's own messages included.\n"
        f"- {words.tool('message')} writes to a team's lead, who reads it as coming from "
        "you. A lead can write to you, which starts you as a message from the person "
        "does.\n"
        f"- {words.tool('propose')} puts teams to the person, who reads the proposal and "
        "may change it. A team exists once they start it; nothing you propose changes "
        "anything before.\n"
        f"- The teams work in the folder {work}, unless one is given its own, and nothing "
        "keeps two from changing the same file. You can read it; you are not able to "
        "change it or to run commands.")


def brief(unit: Unit, supervisor: Unit | None, under: list[Unit], words: Vocabulary, *,
          in_project: bool = False) -> str:
    """The unit's standing instruction: who it is in the fleet, its task, and how the fleet
    works from where it stands. `under`: the agents it has when this is written.
    `in_project`: its fleet is a team of a project, which has other teams and a secretary."""
    who = f"{unit.name}, a" if unit.name else "a"
    lines = [(f"You are {who} K{unit.rank} agent in a fleet of AI agents that works "
              "without human supervision. A higher rank supervises the one below it.")]
    if unit.team:
        lines.append(f"Your team: {unit.team}.")
    lines += ["", f"Your task: {unit.task}", "", "How the fleet works:"]
    if supervisor is None:
        reader = "the person"
        lines.append("- A person gave you this task and you are their point of contact. "
                     f"They are not watching. {words.ask} puts a question to them; the "
                     "answer arrives as a message.")
    else:
        reader = "your supervisor"
        above = (f"{supervisor.name} (K{supervisor.rank})" if supervisor.name
                 else f"a K{supervisor.rank} agent")
        lines.append(f"- Your supervisor is {above}. It gave you this task, sees nothing of "
                     f"your conversation, and gets what you report. {words.ask} puts a "
                     "question to it; the answer arrives as a message.")
    if unit.ongoing:
        lines.append(f"- This is standing work, with no end. {words.tool('report')} sends "
                     f"{reader} something without ending it.")
    else:
        lines.append(f"- {words.finish} with a report ends the task. The report is all "
                     f"{reader} gets of your work.")
    if unit.rank > 1:
        lines.append(
            f"- {words.tool('delegate')} starts a K{unit.rank - 1} agent on a task you give "
            "it. It works in the same folder and sees nothing of your conversation, only "
            "that task. Its reports and questions arrive as messages; "
            f"{words.tool('message')} reaches it, and {words.tool('status')} shows all of "
            "yours.")
    else:
        lines.append("- You are the lowest rank: there is no rank below you to hand work to.")
    if in_project and supervisor is None:
        lines.append(
            "- Your fleet is one team of a project. Its other teams each have a lead that "
            "answers to the person, as you do, and the person talks about the project to "
            f"its secretary. {words.tool('message')} with a team's name, or \"secretary\", "
            f"in place of an agent's id reaches that team's lead, or the secretary, and "
            f"{words.tool('status')} lists the teams.")
    if under:
        lines.append("- These agents are under you. One with a task works on it by itself; "
                     "one with none starts on the first message it gets:")
        lines += [f"  - {each.label}: {each.task or 'no task yet'}" for each in under]
    lines.append("- Every agent works in the same folder, and nothing keeps two from "
                 "changing the same file.")
    return "\n".join(lines)
