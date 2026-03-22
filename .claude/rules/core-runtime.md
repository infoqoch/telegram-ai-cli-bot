---
paths:
  - "src/main.py"
  - "src/bootstrap.py"
  - "src/supervisor.py"
  - "src/bot/**/*.py"
  - "src/services/**/*.py"
  - "src/repository/**/*.py"
  - "src/claude/**/*.py"
  - "src/codex/**/*.py"
---

# Core Runtime Rules

- Keep `main.py` and `bootstrap.py` as wiring/composition points, not business-logic dumps.
- Handlers choose flows; detached job lifecycle, queue drain, retry behavior, and session-lock cleanup belong in services or runtime collaborators.
- Preserve session-level isolation. Avoid introducing same-session concurrent execution paths.
- Detached workers are a core stability feature. Do not move long-running AI work back into the main polling flow.
- SQLite writes should stay explicit and short. Add wider transactions only when the consistency requirement is clear.
- If a change affects user-visible Telegram behavior, update `docs/SPEC.md`.
- If a change affects runtime ownership or implementation boundaries, update `docs/DEVELOPMENT.md`.
