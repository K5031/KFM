"""The fleet's record: events written as JSON lines and read back as they were."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from kfm import events, log


def test_a_line_names_the_kind_and_time_and_refers_to_units_by_id():
    text = log.line(events.Delivered("u2", "u1", "report", "done"), 12.3456789)
    data = json.loads(text)
    assert data == {"t": 12.346, "type": "Delivered", "unit": "u2", "to": "u1",
                    "kind": "report", "text": "done"}


def test_a_line_keeps_non_ascii_text_readable():
    assert "café" in log.line(events.Said("u1", "café"), 0.0)


def test_a_fleet_with_no_record_has_no_events(tmp_path: Path):
    assert log.read(tmp_path) == [] and log.count(tmp_path) == 0


def test_events_round_trip_through_the_record_in_order(tmp_path: Path):
    sent = [
        events.Changed("u2", "working"),
        events.Said("u2", "reading files"),
        events.Delivered("u2", "u1", "report", "done"),
        events.Instructed("u2", "u1", "message", "more"),
        events.Approval("u2", "r1", "shell", "rm -rf x"),
        events.Answered("u2", "r1", False),
        events.Told("start", to="u1", files=("a.txt", "b.txt")),
        events.Told("hello"),
        events.Notice("saved"),
    ]
    with (tmp_path / log.FILE).open("w") as record:
        for at, event in enumerate(sent):
            record.write(log.line(event, float(at)) + "\n")

    read = log.read(tmp_path)
    assert [at for at, _ in read] == [float(i) for i in range(len(sent))]
    assert [event for _, event in read] == sent
    assert read[6][1].files == ("a.txt", "b.txt")  # a tuple again, not a list
    assert [events.units(event) for event in sent[2:4] + sent[7:]] == [
        {"u2", "u1"}, {"u2", "u1"}, set(), set()]


def test_an_events_number_is_its_line_in_the_record(tmp_path: Path):
    sent = [events.Notice(f"line {index}") for index in range(1, 6)]
    (tmp_path / log.FILE).write_text("".join(log.line(event, 0.0) + "\n" for event in sent))
    assert log.count(tmp_path) == 5
    assert [event for _, event in log.read(tmp_path, 3)] == sent[:3]  # as far as number 3


def test_lines_cut_off_by_a_crash_are_left_out(tmp_path: Path):
    good = log.line(events.Notice("kept"), 1.0)
    (tmp_path / log.FILE).write_text(
        good + "\n" + '{"t": 2.0, "type": "Said", "unit": "u1"' + "\n"  # cut off
        + '{"t": 3.0, "type": "NoSuchEvent"}' + "\n"  # unknown kind
        + log.line(events.Said("u1", "after"), 4.0) + "\n")
    assert [event for _, event in log.read(tmp_path)] == [
        events.Notice("kept"), events.Said("u1", "after")]
    assert log.count(tmp_path) == 4  # they keep their numbers


async def test_the_log_appends_in_order_and_is_flushed_on_close(tmp_path: Path):
    record = log.Log(tmp_path)
    for index in range(5):
        record.add(events.Said("u1", f"line {index}"))
    await asyncio.wait_for(record.aclose(), 5)
    texts = [json.loads(text)["text"] for text in (tmp_path / log.FILE).read_text().splitlines()]
    assert texts == [f"line {index}" for index in range(5)]


async def test_a_record_that_cannot_be_written_is_said_once_and_holds_nobody_up(tmp_path: Path):
    (tmp_path / log.FILE).mkdir()  # where the record should be, something that is no file
    failures: list[BaseException] = []
    record = log.Log(tmp_path, failures.append)
    record.add(events.Notice("one"))
    await asyncio.wait_for(record.flush(), 5)
    await asyncio.sleep(0.01)
    assert [type(failure) for failure in failures] == [IsADirectoryError]
    record.add(events.Notice("two"))  # not tried again, so not said again
    await asyncio.wait_for(record.flush(), 5)
    await asyncio.wait_for(record.aclose(), 5)
    assert len(failures) == 1

    (tmp_path / log.FILE).rmdir()  # once it is closed, it writes again
    record.add(events.Notice("three"))
    await asyncio.wait_for(record.aclose(), 5)
    assert [event for _, event in log.read(tmp_path)] == [events.Notice("three")]


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
    assert [event for _, event in log.read(tmp_path)] == [
        events.Notice("one"), events.Notice("two")]
