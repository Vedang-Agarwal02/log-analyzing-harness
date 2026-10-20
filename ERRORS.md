# Errors

## E1: Task 5.13 Step 5 test needs ui/app.py, which only Task 5.14 creates

- Status: CLOSED
- Task: 5.13, Step 5
- Command: `uv run pytest tests/unit/test_ui_data.py tests/integration/test_cli_ui.py -q`
- Expected: `7 passed`
- Files involved: `tests/integration/test_cli_ui.py`, `src/investigator/ui/app.py` (created by Task 5.14, not yet present), `src/investigator/cli.py`
- What I tried:
  1. Ran Steps 1-4 as written: added Streamlit (`uv add --optional ui` and `uv add --dev`), wrote the two test files, confirmed they failed with `ModuleNotFoundError: No module named 'investigator.ui'`, then created `src/investigator/ui/__init__.py`, `src/investigator/ui/data.py`, the replacement `src/investigator/cli.py` and `Makefile`. Result: 6 passed, 1 failed. The failing test is `test_ui_command_starts_streamlit_on_the_app`, which asserts that `src/investigator/ui/app.py` exists.
  2. Checked the plan. `ui/app.py` is listed under Task 5.14 ("Create: `src/investigator/ui/app.py`"), so Task 5.13's test depends on a file from a later task. I did not create `app.py` early, because that would make the test pass by working around it. I did not change the test.
- Likely cause: plan ordering. Task 5.13's Step 5 expects 7 passed, but the `ui` command test checks for `app.py`, which only exists after Task 5.14. Either Task 5.13's test should not assert the file exists, or the expected count is for after Task 5.14.

Last output (complete, not shortened):

```text
......F                                                                  [100%]
=================================== FAILURES ===================================
_________________ test_ui_command_starts_streamlit_on_the_app __________________

monkeypatch = <_pytest.monkeypatch.MonkeyPatch object at 0x10bb10050>

    def test_ui_command_starts_streamlit_on_the_app(monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[list[str]] = []
    
        def fake_call(command: list[str], **kwargs: Any) -> int:
            calls.append(command)
            return 0
    
        monkeypatch.setattr(subprocess, "call", fake_call)
        result = runner.invoke(app, ["ui", "--port", "8600"])
        assert result.exit_code == 0, result.output
        [command] = calls
        assert command[1:4] == ["-m", "streamlit", "run"] and Path(command[4]).name == "app.py"
>       assert Path(command[4]).exists() and command[-2:] == ["--server.port", "8600"]
E       AssertionError: assert (False)
E        +  where False = exists()
E        +    where exists = PosixPath('/Users/vedang.agarwal/Desktop/personal/projects/orange-invest-assignment/incident-investigator/src/investigator/ui/app.py').exists
E        +      where PosixPath('/Users/vedang.agarwal/Desktop/personal/projects/orange-invest-assignment/incident-investigator/src/investigator/ui/app.py') = Path('/Users/vedang.agarwal/Desktop/personal/projects/orange-invest-assignment/incident-investigator/src/investigator/ui/app.py')

tests/integration/test_cli_ui.py:59: AssertionError
=============================== warnings summary ===============================
.venv/lib/python3.14/site-packages/google/genai/types.py:42
  /Users/vedang.agarwal/Desktop/personal/projects/orange-invest-assignment/incident-investigator/.venv/lib/python3.14/site-packages/google/genai/types.py:42: DeprecationWarning: '_UnionGenericAlias' is deprecated and slated for removal in Python 3.17
    VersionedUnionType = Union[builtin_types.UnionType, _UnionGenericAlias]

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
=========================== short test summary info ============================
FAILED tests/integration/test_cli_ui.py::test_ui_command_starts_streamlit_on_the_app
1 failed, 6 passed, 1 warning in 4.78s
```

Resolution: The test was wrong, not the code: `tests/integration/test_cli_ui.py` asserted `Path(command[4]).exists()`, but `ui/app.py` is created by Task 5.14. I replaced that assertion with a path-shape check (`Path(command[4]).parts[-3:] == ("investigator", "ui", "app.py")`), which still pins where the `ui` command points without needing the file; Task 5.14's `AppTest.from_file(APP)` tests cover that the file exists. The same fix is applied to Task 5.13's test in `docs/design/02-development-plan.md` and the Desktop `02-development-plan.md`, so the plan matches the file and no PLAN-FIX line is needed. `src/investigator/cli.py` was already correct. Verified: the Command gives `7 passed`, and `uv run pytest tests/integration/test_cli.py -q` gives `6 passed`. Continue at Task 5.13 Step 5, then Step 6 (commit).
