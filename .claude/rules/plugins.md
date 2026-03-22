---
paths:
  - "plugins/**/*.py"
  - "plugins/**/*.md"
  - "src/plugins/**/*.py"
  - "src/repository/adapters/plugin_storage.py"
  - "tests/test_plugin*.py"
  - "tests/test_*plugin*.py"
---

# Plugin Rules

- New plugins must fit the existing plugin runtime. Do not hardcode plugin names, labels, or routing into core files.
- Use `build_storage(repository)` and `self.storage` for persistence. Plugin source should not call `self.repository` directly.
- Plugin tables manage their own DDL through `get_schema()`.
- `CALLBACK_PREFIX` and `FORCE_REPLY_MARKER` must be unique.
- For AI-aware plugins, keep static guidance in `ai_context.md` and expose live operations through MCP tools only when needed.
- Prefer built-in plugins as reference implementations:
  - `memo`: minimal CRUD
  - `todo`: scheduled actions
  - `calendar`: external API + MCP
  - `hourly_ping`: system jobs
- Keep plugin-core isolation intact. Adding a plugin should not require editing unrelated core runtime paths.
