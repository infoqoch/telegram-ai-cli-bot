---
paths:
  - "README.md"
  - "docs/**/*.md"
  - "CLAUDE.md"
  - ".claude/rules/**/*.md"
---

# Documentation Rules

- `README.md` is for product value, workflow, and quick start. Keep it short and practical.
- `docs/SETUP.md` owns installation, runtime commands, security controls, and environment variables.
- `docs/SPEC.md` owns stable user-visible Telegram behavior and may stay detailed when screen-level UX matters.
- `docs/SPEC_PLUGINS_BUILTIN.md` owns built-in plugin UI/UX details and screen transcripts.
- `docs/UI_EMOJI_SYSTEM.md` owns canonical emoji and shared UI labels.
- `docs/DEVELOPMENT.md` owns deeper maintainer reference material and intentionally lives under `docs/`, not `.claude/`, so it is not always-loaded context.
- `CLAUDE.md` is always-loaded guidance for Claude Code. Keep it compact and high signal.
- `.claude/rules/*.md` are for path-scoped auto-loaded guidance only. Use them for focused editing rules, not for dumping general reference material.
- This repo is UX-contract-driven. For meaningful user-visible flow changes, update the relevant spec before coding or in the same change.
- If a UX change is user-visible, update the relevant spec document instead of relying on code/tests alone.
- Prefer deleting stale detail over preserving duplicate documents that drift away from code.
