# Documentation Map

This directory contains the deeper reference documents that sit behind the main [README](../README.md).

## Recommended Reading Order

1. [README.md](../README.md)
   Product overview, value proposition, and the shortest path to understanding what the project is for.

2. [SETUP.md](./SETUP.md)
   User-facing setup, runtime commands, security controls, and environment variables.

3. [SPEC.md](./SPEC.md)
   User-visible bot behavior, Telegram UI flows, session rules, scheduler behavior, restart behavior, and error handling.

4. [SPEC_PLUGINS_BUILTIN.md](./SPEC_PLUGINS_BUILTIN.md)
   Detailed UX and behavior for the built-in plugins.

5. [ARCHITECTURE.md](./ARCHITECTURE.md)
   Runtime boundaries, ownership rules, and implementation structure.

6. [UI_EMOJI_SYSTEM.md](./UI_EMOJI_SYSTEM.md)
   Canonical emoji mapping for the Telegram UI.

## Which Document To Edit

- Change product setup or runtime instructions: [SETUP.md](./SETUP.md)
- Change user-visible Telegram behavior: [SPEC.md](./SPEC.md)
- Change built-in plugin UX or plugin-specific user behavior: [SPEC_PLUGINS_BUILTIN.md](./SPEC_PLUGINS_BUILTIN.md)
- Change code ownership or implementation boundaries: [ARCHITECTURE.md](./ARCHITECTURE.md)
- Change emoji semantics or canonical labels: [UI_EMOJI_SYSTEM.md](./UI_EMOJI_SYSTEM.md)
- Change developer rules or extension contracts for contributors/agents: [CLAUDE.md](../CLAUDE.md)
