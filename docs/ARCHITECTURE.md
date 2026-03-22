# Architecture

## Goals

- Keep runtime behavior explicit and state-based.
- Prefer SRP over clever abstractions.
- Delete dead code instead of preserving alternate paths.
- Keep persistence durable and simple enough to test with SQLite.
- Push UX-visible behavior into [`SPEC.md`](./SPEC.md) and keep code-structure rules here.

## Layer Map

### Composition Root

- [`src/main.py`](../src/main.py): Telegram `Application` wiring and process lifecycle.
- [`src/bootstrap.py`](../src/bootstrap.py): builds the runtime dependency graph.

`main.py` should not own business logic. It should wire services, handlers, schedulers, and transport.

### Process Supervision

- [`src/supervisor.py`](../src/supervisor.py): thin process manager for [`src/main.py`](../src/main.py)

Rules:

- Supervisor owns process lifecycle only: startup preflight, child restart/backoff, crash-loop cut-off, and operator notifications.
- Supervisor must not become a durable state owner for sessions, queues, or detached jobs.
- Unrecoverable startup exits should be expressed through shared exit codes, not inferred from log text.
- Job-level watchdogs stay in services that know the job/session context, not in supervisor.

### Handler Layer

- [`src/bot/handlers/`](../src/bot/handlers): Telegram command/callback/message entrypoints.
- [`src/bot/handlers/base.py`](../src/bot/handlers/base.py): shared transport-facing helpers only.
- [`src/bot/runtime/`](../src/bot/runtime): handler collaborators for runtime-only concerns.

Rules:

- Handlers decide `which flow` to run.
- Handlers should not own detached worker spawn, lock cleanup, or temp pending persistence logic inline.
- Shared handler state must stay small and operational, not business-heavy.
- Protected command entrypoints should use `authorized_only` / `authenticated_only` unless the command is intentionally public.

Callback interaction rule:

- Stateful menu/navigation callbacks may edit the same message in place.
- AI-response shortcut callbacks must use a dedicated prefix and send a follow-up message instead of editing the original AI response.
- Do not reuse edit-based screen callbacks directly on detached AI result messages.

### Service Layer

- [`src/services/session_service.py`](../src/services/session_service.py): session business rules.
- [`src/services/job_service.py`](../src/services/job_service.py): detached AI job lifecycle.
- [`src/services/schedule_execution_service.py`](../src/services/schedule_execution_service.py): scheduled execution delivery.

Rules:

- One service should own one runtime story.
- Services may orchestrate multiple repositories/clients.
- Services should be unit-testable with mocks/fakes.

### Infrastructure Layer

- [`src/repository/`](../src/repository): SQLite persistence and adapters.
- [`src/ai/`](../src/ai): provider registry and clients.
- [`src/plugins/`](../src/plugins): plugin discovery and plugin runtime.

Rules:

- Repository methods should stay short and explicit.
- Runtime SQLite writes should prefer autocommit-sized operations.
- Provider clients should encapsulate subprocess behavior, timeout/cancellation cleanup, and provider-specific parsing.
- Session creation APIs use keyword-only options after `session_id` to avoid ambiguous positional calls.
- Internal AI-assisted features such as workspace recommendation should use injected clients, not ad-hoc subprocess calls from adapters.

## Core Runtime Flows

### AI Message Flow

1. Handler resolves current session and provider.
2. If the session is busy, show Session Queue UI.
3. If idle, create `message_log` row and reserve `session_locks`.
4. Spawn detached worker.
5. Detached worker runs [`JobService`](../src/services/job_service.py), sends the final Telegram response, drains persistent queue, then releases the lock.

UX details for this flow belong in [`SPEC.md`](./SPEC.md).

### Temp Pending Flow

- Session conflict UI uses short-lived pending state.
- Runtime owner: [`PendingRequestStore`](../src/bot/runtime/pending_request_store.py)
- Lifetime: 5 minutes
- Purpose: preserve callback context, not durable user intent

### Persistent Queue Flow

- `Wait in this session` writes to `queued_messages`.
- Queue does not auto-expire.
- Queue is drained by the same detached worker lifecycle, not by a separate in-memory worker.

### Schedule Flow

- Scheduler decides when to run.
- [`ScheduleExecutionService`](../src/services/schedule_execution_service.py) decides how to execute and deliver.
- Schedule registration/storage belongs to repository adapters, not `main.py`.
- Canonical trigger model is `cron | once`.
- User-facing `Daily` / `One-time` buttons are UI sugar over that storage model.
- Global scheduler time is owned by [`src/time_utils.py`](../src/time_utils.py) and configured through `APP_TIMEZONE`.
- Schedule list ordering belongs to adapter/view logic and should be based on `next run`, not legacy `hour/minute` sorting.

## SQLite Rules

- SQLite WAL allows many readers but only one writer commit path at a time.
- App-level `session_locks` prevent same-session concurrency.
- DB writes must stay short; avoid hidden write side effects inside read methods.
- If a new feature needs multi-step consistency, prove why a wider transaction is necessary before adding one.

## Test Strategy

### Unit Tests

- First choice for:
  - repository methods
  - services
  - provider client parsing/timeouts
  - runtime collaborators

### Integration Tests

- Keep a small number of golden flows:
  - idle session -> detached success
  - busy session -> wait -> queue drain
  - provider hang -> still-running notice -> watchdog timeout
  - restart with detached worker locks present

### Dead Code Checks

- `vulture` is a review aid, not a source of truth.
- Treat its output as “candidate dead code”, then confirm call paths before deletion.

## Change Rules

- If a change affects user-visible AI/chat UX, update [`SPEC.md`](./SPEC.md).
- If a change affects code ownership/boundaries, update this file.
- Prefer deleting alternate flows over preserving legacy compatibility branches.
- New shared logic should enter as a small collaborator or service, not by growing `BaseHandler`.
