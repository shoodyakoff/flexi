# Project Agentic Mode

This repository uses a local, project-scoped problem-first workflow for work where "done" depends on the user's original intent, not only a checklist or passing tests. The workflow is intentionally lighter than a global Codex rule so quick edits stay quick.

## When To Use It

Use the full workflow for:

- product, design, editorial, or technical decisions with ambiguous behavior;
- multi-step implementation, pipeline, UI, content, or automation changes;
- data, artifact, API, rendering, or user-facing contract changes;
- tasks where the request names "agentic mode", "problem-first", "исходная боль", or asks to preserve the original intent;
- anything where the user may reject a technically correct implementation because it missed the real problem.

Use normal direct execution for:

- one-line fixes;
- typo edits;
- mechanical renames;
- pure information questions;
- commands the user explicitly asks to run;
- narrow test or lint fixes where the intended outcome is already concrete.

## Session Folder

Each non-trivial task gets a local audit trail:

```text
.codex/session/<taskId>/
|-- 00_ORIGINAL_PROBLEM.md
|-- 00_ORIGINAL_PROBLEM.sha256
|-- 01_CONTEXT.md
|-- 02_HYPOTHESES.md
|-- 03_SPEC.md
|-- 04_IMPLEMENTATION.md
|-- 05_SPEC_VERIFICATION.md
|-- 06_INTENT_VERIFICATION.md
|-- attempts.log
`-- decision.md
```

The `.sha256` sidecar is used instead of putting the hash inside `00_ORIGINAL_PROBLEM.md`, because self-hashing a file that contains its own hash is not stable.

## Phase Rules

1. **Problem Freeze**: write `00_ORIGINAL_PROBLEM.md` from the user's raw request, extracted pain, desired outcome, success signals, and anti-goals. Then compute `00_ORIGINAL_PROBLEM.sha256`. Do not edit the problem file after hashing.
2. **Context / Research Gate**: collect only context that can change the chosen approach, but do not skip the three research lanes below. This phase is a gate: do not write hypotheses, a spec, or code until `01_CONTEXT.md` records the findings or explicitly states why a lane was impossible or irrelevant for this task.
   - **Docs and Past Problems**: read the current repository docs that govern the affected area, `AGENTS.md`, relevant plans, project memory, previous `.codex/session/*` records, `attempts.log`, and similar failures or fixes. Capture what was already tried, what passed or failed intent verification, and what constraints still apply.
   - **Current Code Reality**: inspect the current implementation, contracts, tests, source data, generated artifacts, CLI/API paths, UI surfaces, scripts, renders, or content outputs that can explain the problem. Map the actual flow before proposing a solution.
   - **External Prior Art**: search current external sources when the task is not purely mechanical or private-only. Prefer official documentation for dependencies and APIs, then GitHub issues/discussions, Reddit, and professional community posts for how similar problems are solved in practice. Record source links, search terms when useful, what applies to this repo, and what does not. If network access is blocked or the task is too sensitive for web search, state that explicitly and continue with local evidence.
3. **Hypotheses**: propose at least three mechanism-distinct approaches. Include failure modes and cost, then choose one.
4. **Spec**: map every spec item back to an original-problem success signal. Name affected domains, contracts, files, commands, UI surfaces, artifacts, docs, and tests.
5. **Implementation**: build the approved spec in small vertical slices that respect the repository's existing boundaries, local instructions, and established patterns.
6. **Spec Verification**: prove the spec was implemented with commands, tests, screenshots, rendered output checks, artifact checks, or file checks. For UI, report, content, render, or owner-facing behavior, inspect the visible output and compare it with the request.
7. **Intent Verification**: verify against `00_ORIGINAL_PROBLEM.md`, not just `03_SPEC.md`. For important tasks, use an isolated reviewer/subagent that receives only the original problem, implementation summary, and result artifacts.
8. **Decision Gate**: `PASS` finishes. `FAIL` appends a dated note to `attempts.log` and loops back to context or hypotheses.

## Research Gate Detail

The research gate should be scaled to the size and risk of the task, but its structure is mandatory for problem-first work. A small label bug may need three concise bullets. A multi-step product, pipeline, UI, or rendering task may need several pages of notes, source links, and reproduction evidence.

Use this shape inside `01_CONTEXT.md`:

```md
# Context

## Docs and Past Problems
- Current docs read:
- Previous sessions or attempts:
- Constraints that still apply:

## Current Code Reality
- Relevant modules and contracts:
- Data, UI, content, render, or command flow:
- Tests, artifacts, or commands inspected:
- Observed failure or reproduction:

## External Prior Art
- Sources searched:
- Useful patterns:
- Rejected patterns:
- Applicability to this repo:

## Clarifications Needed
- Only questions that can change the approach:
```

External prior art is not a license to import complexity. It is there to avoid missing current API behavior, established library patterns, known failure modes, and common UX, content, or operational solutions. Repository boundaries and the original user pain still win.

After the research gate, ask the user only for clarifications that can change the approach. If the next step is obvious, proceed to hypotheses.

## Project Calibration

The first setup session can be:

```text
.codex/session/000-agentic-mode-integration/
```

Do not reuse that session for product work. Future tasks should create focused names such as:

```text
.codex/session/001-render-contract-fix/
.codex/session/002-public-page-flow/
.codex/session/003-content-pipeline-guardrail/
```

The workflow is a tool, not a cage. If a phase produces no new information, state that in the relevant session file and keep moving. If tests pass but intent verification fails, the task is not done: update context and hypotheses, then choose the next attempt.

When a task fails intent verification, the next attempt must begin by updating the research gate, not by patching from memory. Check whether the failure came from a missed past problem, a wrong code map, or missing external prior art, then revise hypotheses.

## Completion Rule

For problem-first tasks, completion requires all of the following:

- the original problem is frozen and hashed;
- `01_CONTEXT.md` includes Docs and Past Problems, Current Code Reality, and External Prior Art findings, or an explicit reason a lane could not be completed;
- implementation evidence is recorded in `05_SPEC_VERIFICATION.md`;
- `06_INTENT_VERIFICATION.md` explicitly checks the original success signals and anti-goals;
- `decision.md` records `PASS` or `FAIL`;
- the final response states whether the original user pain is solved, not only which files changed.
