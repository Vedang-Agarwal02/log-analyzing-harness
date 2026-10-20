import dataclasses
from pathlib import Path

import pytest

from investigator.harness.llm import LLMRequest, UserMessage, UserText
from investigator.harness.recording import CassetteMiss, RecordingLLM, request_key
from investigator.harness.scripted import ScriptedLLM, sequence, tool_call_response


def _request(text: str = "go") -> LLMRequest:
    return LLMRequest(
        "observability",
        "claude-opus-5-5",
        "sys",
        (UserMessage(parts=(UserText(text),)),),
        (),
        100,
        "medium",
    )


def test_request_key_is_stable_and_sensitive() -> None:
    assert request_key(_request()) == request_key(_request())
    assert request_key(_request()) != request_key(_request("other"))
    assert request_key(_request()) != request_key(dataclasses.replace(_request(), effort="high"))


async def test_record_then_replay(tmp_path: Path) -> None:
    cassette = tmp_path / "cassettes" / "c.json"
    inner = ScriptedLLM({"*": sequence(tool_call_response(("query_logs", {"service": "x"})))})
    recorder = RecordingLLM(cassette, mode="record", inner=inner)
    recorded = await recorder.complete(_request())
    assert cassette.exists()
    replayer = RecordingLLM(cassette, mode="replay")
    replayed = await replayer.complete(_request())
    assert replayed == recorded and replayer.hits == 1
    with pytest.raises(CassetteMiss):
        await replayer.complete(_request("changed prompt"))
    assert replayer.misses == 1


def test_invalid_construction(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        RecordingLLM(tmp_path / "c.json", mode="record")
    with pytest.raises(FileNotFoundError):
        RecordingLLM(tmp_path / "missing.json", mode="replay")
