"""The fleet's record: events written as JSON lines and read back as they were."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from kfm import events, log
from kfm.fleet import Fleet


def make_fleet() -> tuple[Fleet, object, object]:
    fleet = Fleet()
    lead = fleet.add("Lead", "Do it", None)
    scout = fleet.add("Scout", "Look", lead)
    return fleet, lead, scout


def test_a_line_names_the_kind_and_time_and_refers_to_units_by_id():
    _, lead, scout = make_fleet()
    text = log.line(events.Delivered(scout, lead, "report", "done"), 12.3456789)
    data = json.loads(text)
    assert data == {"t": 12.346, "type": "Delivered", "unit": "u2", "to": "u1",
                    "kind": "report", "text": "done"}


def test_a_line_keeps_non_ascii_text_readable():
    _, lead, _ = make_fleet()
    assert "café" in log.line(events.Said(lead, "café"), 0.0)


def test_a_fleet_with_no_record_has_no_events(tmp_path: Path):
    fleet, _, _ = make_fleet()
    assert log.read(tmp_path, fleet) == []


def test_events_round_trip_through_the_record_in_order(tmp_path: Path):
    fleet, lead, scout = make_fleet()
    sent = [
        events.Changed(scout, "working"),
        events.Said(scout, "reading files"),
        events.Delivered(scout, lead, "report", "done"),
        events.Instructed(scout, lead, "message", "more"),
        events.Approval(scout, "r1", "shell", "rm -rf x"),
        events.Answered(scout, "r1", False),
        events.Told("start", to=lead, files=("a.txt", "b.txt")),
        events.Told("hello"),
        events.Notice("saved"),
    ]
    with (tmp_path / log.FILE).open("w") as record:
        for at, event in enumerate(sent):
            record.write(log.line(event, float(at)) + "\n")

    read = log.read(tmp_path, fleet)
    assert [at for at, _ in read] == [float(i) for i in range(len(sent))]
    assert [event for _, event in read] == sent
    assert read[6][1].files == ("a.txt", "b.txt")  # a tuple again, not a list


def test_lines_cut_off_by_a_crash_are_left_out(tmp_path: Path):
    fleet, lead, _ = make_fleet()
    good = log.line(events.Notice("kept"), 1.0)
    (tmp_path / log.FILE).write_text(
        good + "\n" + '{"t": 2.0, "type": "Said", "unit": "u1"' + "\n"  # cut off
        + '{"t": 3.0, "type": "NoSuchEvent"}' + "\n"  # unknown kind
        + log.line(events.Said(lead, "after"), 4.0) + "\n")
    assert [event for _, event in log.read(tmp_path, fleet)] == [
        events.Notice("kept"), events.Said(lead, "after")]


def test_an_event_about_a_removed_unit_is_left_out(tmp_path: Path):
    fleet, _, _ = make_fleet()
    (tmp_path / log.FILE).write_text(
        '{"t": 1.0, "type": "Said", "unit": "u9", "text": "gone"}\n')
    assert log.read(tmp_path, fleet) == []


async def test_the_log_appends_in_order_and_is_flushed_on_close(tmp_path: Path):
    _, lead, _ = make_fleet()
    record = log.Log(tmp_path)
    for index in range(5):
        record.add(events.Said(lead, f"line {index}"))
    await asyncio.wait_for(record.aclose(), 5)
    texts = [json.loads(text)["text"] for text in (tmp_path / log.FILE).read_text().splitlines()]
    assert texts == [f"line {index}" for index in range(5)]


async def test_closing_a_log_that_wrote_nothing_creates_no_file(tmp_path: Path):
    await asyncio.wait_for(log.Log(tmp_path).aclose(), 5)
    assert not (tmp_path / log.FILE).exists()


async def test_a_second_log_continues_the_same_record(tmp_path: Path):
    first = log.Log(tmp_path)
    first.add(events.Notice("one"))
    await asyncio.wait_for(first.aclose(), 5)
    second = log.Log(tmp_path)
    second.add(events.Notice("two"))
    await asyncio.wait_for(second.aclose(), 5)
    fleet, _, _ = make_fleet()
    assert [event for _, event in log.read(tmp_path, fleet)] == [
        events.Notice("one"), events.Notice("two")]
