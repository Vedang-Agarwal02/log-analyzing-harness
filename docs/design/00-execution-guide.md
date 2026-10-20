# Execution guide

Read this file at the start of every session, before anything else. `02-development-plan.md` (same folder) holds the code and the step-by-step tasks. This guide holds the routine around them: how to start a session, when to stop, and what to do when you are stuck.

- **Coding model:** follow sections 1–4.
- **Person supervising:** sections 5–6.
- **Stronger model asked to unblock the build:** section 7.

## 1. Start of every session

1. **Blockers first.** If `ERRORS.md` exists in the repository root, read it.
   - If any entry has `Status: OPEN`, the person has not fixed it yet. Reply "Still blocked on E<n>; see ERRORS.md." and do nothing else.
   - For each entry with `Status: RESOLVED`, run its **Command**:
     - **If the output matches its Expected:**
       - Change the status to `CLOSED`.
       - If the Resolution left a file different from the plan, add `PLAN-FIX: <file>: <what changed> (E<n>)` to the Log in `PROGRESS.md`.
       - Finish that entry's task, starting from the step it names.
     - **If the output does not match:** paste the new output under the entry, set the status back to `OPEN`, and stop.
2. **Rules.** Read `docs/design/02-development-plan.md` from the first line down to the line `## Milestone M0: Scaffold`. Follow its "How to execute this plan" rules for every task. The plan is approved, so do not:
   - brainstorm or re-plan;
   - review the whole plan;
   - use planning or subagent skills.
3. **Your place.**
   - If `PROGRESS.md` exists: read it, run `git log --oneline -5`, and continue at the first unticked task.
   - If it does not exist, this is the first session. Read sections 3–7 and 9–10 of `docs/design/01-strategy-and-architecture.md` once for background, then start at Task 0.1.

## 2. Every task

1. **Find the task's lines.** Run `grep -nE "^(### Task|## Milestone)" docs/design/02-development-plan.md`, then read only from the task's heading to the next heading. Never read the whole plan; it is about 15,800 lines.
2. **Do the steps in order:**
   1. Write the test.
   2. Run it and confirm it fails.
   3. Write the code.
   4. Run it and confirm the expected output.
   5. Run `uv run ruff format .` and `uv run ruff check --fix .`.
   6. Tick the task in `PROGRESS.md`.
   7. Commit with the plan's message.
3. **Copy every file exactly.** A file that itself contains triple backticks is shown inside four-backtick fences; copy what is between the outer fences.
4. **Leave tests and dependencies alone.** Never change a test assertion, and never install anything the plan does not list.
5. **Never call the Anthropic API yourself.** Rule 10 of the plan's "How to execute this plan" lists the steps that do. At one of those steps, or at a step marked HUMAN STEP, stop and give the person the exact command to run.

## 3. When you are stuck

You are stuck when one of these happens:
- A test still fails after you compared your files with the plan character by character and made two fix attempts.
- A command fails in a way the plan does not cover, such as a missing tool, an install or network error, or a permission error.
- The plan contradicts itself, or refers to a file, name or command that does not exist.

Then:
1. Do not commit, and do not undo your changes.
2. Append an entry to `ERRORS.md` in the repository root.
   - If the file does not exist, create it with the line `# Errors` first.
   - Number entries E1, E2, … in order.
   - Fill in every field of this template:

````markdown
## E1: <one-line summary of the problem>

- Status: OPEN
- Task: <task number>, Step <step number>
- Command: `<the exact command you ran>`
- Expected: `<the expected output from the plan>`
- Files involved: `<path>`, `<path>`
- What I tried:
  1. <first attempt, and what happened>
  2. <second attempt, and what happened>
- Likely cause: <one or two sentences, or "unknown">

Last output (complete, not shortened):

```text
<paste the full output of the last run>
```

Resolution: <left empty for the person or the stronger model>
````

3. Reply "Blocked on E<n>; see ERRORS.md." and stop.

## 4. When to stop and report

Stop at the end of each milestone and after Task 5.6, and report:
- the tasks completed
- the last pytest summary line
- any `PLAN-FIX` or `TYPE-IGNORE` lines you added to `PROGRESS.md`
- the next task

Also stop at a paid step, at a HUMAN STEP, and whenever you are stuck (section 3).

## 5. For the person: setup and sessions

Setup, once:

```bash
mkdir -p ~/code/incident-investigator/docs/design
```

```bash
cp "/Users/vedang.agarwal/Documents/Important docs/Resume/orange-invest-assignment/"0*.md ~/code/incident-investigator/docs/design/
```

Then:
- **Open the folder.** Open `~/code/incident-investigator` in Claude Code with Haiku 5.5 selected. Choose the permission mode that auto-accepts edits, and click "always allow" for `uv`, `git` and `make` the first time each is asked.
- **Start every session in a new chat** with: **Read docs/design/00-execution-guide.md and follow it.**
- **Run one session at a time.** Tasks build on each other, so parallel sessions would conflict.
- **Keep your API key out of the coding session.** When it stops at a paid step, run the command yourself in your own terminal with `ANTHROPIC_API_KEY` exported, then tell it to continue.
- **Check each stop.** `make check` should pass. Read any `PLAN-FIX` or `TYPE-IGNORE` lines in `PROGRESS.md`; they mark where the build departed from the plan.
- **Keep every session's transcript.** Task 7.4 needs them in `docs/transcripts/`.

## 6. For the person: when the model is blocked

1. Open a session with a stronger model (Sonnet 5.5 or Opus 5.5) in the same folder and send: **Read docs/design/00-execution-guide.md, section 7, and resolve every OPEN entry in ERRORS.md.**
2. When it reports the entry as RESOLVED, start a new Haiku session with the usual message. Haiku re-runs the command, closes the entry and carries on.

You can also resolve an entry yourself the same way: fix the code, fill in the Resolution, and set the status to `RESOLVED`.

## 7. For the stronger model: resolving an ERRORS.md entry

1. **Read in this order:** the entry, then only the task it names in `docs/design/02-development-plan.md` (find it with the grep in section 2), then the files it lists.
2. **Fix the cause.**
   - Change a test only if the test itself is wrong, never to let broken code pass, and say so in the Resolution.
   - If the plan's code is wrong, fix the file and also correct that file in the plan, so the plan stays the source of truth.
3. **Verify.** Run the entry's Command and confirm the Expected output.
4. **Record it.** Fill in `Resolution:` with what you changed and why, naming the files, in one to three sentences. Then set `Status: RESOLVED`.
5. **Stop there.** Do not commit and do not continue with later tasks. The coding model verifies the fix and commits it with its task.
