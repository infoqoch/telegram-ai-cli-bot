# Architecture

## Goals

- Keep runtime behavior explicit and state-based.
- Prefer SRP over clever abstractions.
- Delete dead code instead of preserving alternate paths.
- Keep persistence durable and simple enough to test with SQLite.
- Push UX-visible behavior into [`SPEC.md`](./SPEC.md) and keep code-structure rules here.

## Layer Map

### Composition Root

- [`src/main.py`](/Users/bae/AiSandbox/telegram-claude-bot/src/main.py): Telegram `Application` wiring and process lifecycle.
- [`src/bootstrap.py`](/Users/bae/AiSandbox/telegram-claude-bot/src/bootstrap.py): builds the runtime dependency graph.

`main.py` should not own business logic. It should wire services, handlers, schedulers, and transport.

### Handler Layer

- [`src/bot/handlers/`](/Users/bae/AiSandbox/telegram-claude-bot/src/bot/handlers): Telegram command/callback/message entrypoints.
- [`src/bot/handlers/base.py`](/Users/bae/AiSandbox/telegram-claude-bot/src/bot/handlers/base.py): shared transport-facing helpers only.
- [`src/bot/runtime/`](/Users/bae/AiSandbox/telegram-claude-bot/src/bot/runtime): handler collaborators for runtime-only concerns.

Rules:

- Handlers decide `which flow` to run.
- Handlers should not own detached worker spawn, lock cleanup, or temp pending persistence logic inline.
- Shared handler state must stay small and operational, not business-heavy.

### Service Layer

- [`src/services/session_service.py`](/Users/bae/AiSandbox/telegram-claude-bot/src/services/session_service.py): session business rules.
- [`src/services/job_service.py`](/Users/bae/AiSandbox/telegram-claude-bot/src/services/job_service.py): detached AI job lifecycle.
- [`src/services/schedule_execution_service.py`](/Users/bae/AiSandbox/telegram-claude-bot/src/services/schedule_execution_service.py): scheduled execution delivery.

Rules:

- One service should own one runtime story.
- Services may orchestrate multiple repositories/clients.
- Services should be unit-testable with mocks/fakes.

### Infrastructure Layer

- [`src/repository/`](/Users/bae/AiSandbox/telegram-claude-bot/src/repository): SQLite persistence and adapters.
- [`src/ai/`](/Users/bae/AiSandbox/telegram-claude-bot/src/ai): provider registry and clients.
- [`src/plugins/`](/Users/bae/AiSandbox/telegram-claude-bot/src/plugins): plugin discovery and plugin runtime.

Rules:

- Repository methods should stay short and explicit.
- Runtime SQLite writes should prefer autocommit-sized operations.
- Provider clients should encapsulate subprocess behavior, timeout/cancellation cleanup, and provider-specific parsing.
- Session creation APIs use keyword-only options after `session_id` to avoid ambiguous positional calls.

## Core Runtime Flows

### AI Message Flow

1. Handler resolves current session and provider.
2. If the session is busy, show Session Queue UI.
3. If idle, create `message_log` row and reserve `session_locks`.
4. Spawn detached worker.
5. Detached worker runs [`JobService`](/Users/bae/AiSandbox/telegram-claude-bot/src/services/job_service.py), sends the final Telegram response, drains persistent queue, then releases the lock.

UX details for this flow belong in [`SPEC.md`](./SPEC.md).

### Temp Pending Flow

- Session conflict UI uses short-lived pending state.
- Runtime owner: [`PendingRequestStore`](/Users/bae/AiSandbox/telegram-claude-bot/src/bot/runtime/pending_request_store.py)
- Lifetime: 5 minutes
- Purpose: preserve callback context, not durable user intent

### Persistent Queue Flow

- `Wait in this session` writes to `queued_messages`.
- Queue does not auto-expire.
- Queue is drained by the same detached worker lifecycle, not by a separate in-memory worker.

### Schedule Flow

- Scheduler decides when to run.
- [`ScheduleExecutionService`](/Users/bae/AiSandbox/telegram-claude-bot/src/services/schedule_execution_service.py) decides how to execute and deliver.
- Schedule registration/storage belongs to repository adapters, not `main.py`.

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
