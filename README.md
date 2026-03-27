# Telegram AI CLI Bot

Turn Claude Code, Codex CLI, and Gemini CLI on your machine into a Telegram-accessible remote coding workspace.

Use Claude Code, Codex CLI, and Gemini CLI from Telegram without changing the workflow you already use on your machine.

Reuse the subscriptions and logins you already have. Start fresh sessions remotely, pick up sessions that already exist locally, switch across projects, schedule recurring work, and keep longer-running jobs stable.

---

## Why Use It

- Use the Claude Code / Codex / Gemini CLI subscriptions and logins you already have; no separate provider API key workflow is required
- Chat with your local coding agents from Telegram wherever you are
- Create new Claude, Codex, or Gemini sessions from Telegram when you want a clean start
- Import local CLI sessions and continue them from Telegram without starting over
- Keep multiple sessions across providers, projects, and tasks without collapsing everything into one thread
- Schedule recurring work for chat, workspace, folder, and plugin actions
- Extend the bot through custom plugins, using the built-ins as reference implementations

## Typical Uses

- **Fresh remote start:** create a new debugging, review, or refactor session from your phone while away from the desk
- **Desk -> phone handoff:** import a Claude/Codex/Gemini session you already started locally and continue it in Telegram
- **Multi-project flow:** keep separate sessions per repo, task, or provider and switch between them quickly
- **Scheduled project work:** run recurring workspace prompts against a specific project and its `CLAUDE.md`
- **Fast-path utility work:** use plugins for common actions so simple requests do not always pay full AI latency

This workflow is not just a pitch. The bot has been actively developed through Telegram using the same local Claude Code / Codex CLI session flow it provides.

## Why It Holds Up

- Long-running AI jobs run in detached workers instead of living only in the main bot process
- Soft restarts can preserve in-flight work instead of dropping responses
- Persistent queueing, lock state, and delivery tracking reduce lost work and duplicate execution
- Session-level isolation makes multi-session handling safer
- SQLite persistence helps the bot recover operational state instead of relying only on memory

## Why It Automates And Extends

- Workspace and folder aware execution applies each project's `CLAUDE.md`
- MCP-backed tools expose live bot data during AI work
- Plugin fast paths handle common actions instantly
- Custom plugins let you add your own domain-specific workflows without rewriting the core runtime

## Built-In Plugins

Todo, Memo, Diary, Calendar, and Weather ship by default.

Treat them as both useful defaults and reference implementations for extension. Keep detailed UX in the plugin spec, and deeper implementation notes in the maintainer reference:

- Built-in plugin UX spec: [docs/SPEC_PLUGINS_BUILTIN.md](docs/SPEC_PLUGINS_BUILTIN.md)
- Maintainer reference: [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)
- Built-in implementations: [plugins/builtin](plugins/builtin)
- Extension rules and plugin interfaces: [CLAUDE.md](CLAUDE.md)

---

## Quick Start

### 1. Install

```bash
git clone https://github.com/infoqoch/telegram-ai-cli-bot.git
cd telegram-ai-cli-bot
python -m venv venv && source venv/bin/activate
pip install -e .
```

### 2. Create a Telegram Bot

1. Open [@BotFather](https://t.me/BotFather) and run `/newbot`
2. Copy the API token

### 3. Finish Setup

If you have [Claude Code](https://claude.ai/claude-code) installed, let it guide you through setup interactively:

```bash
claude
# Then say: "help me set up"
```

Claude reads the project's `CLAUDE.md` automatically and walks you through creating `.env`, configuring tokens, and starting the bot.

For manual setup, runtime commands, security controls, and environment variables, see [docs/SETUP.md](docs/SETUP.md).

---

## Documentation

| Doc | Content |
|-----|---------|
| [CLAUDE.md](CLAUDE.md) | Compact always-loaded Claude Code guidance |
| [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) | Deep maintainer reference: runtime, plugins, MCP, sessions |
| [docs/SETUP.md](docs/SETUP.md) | User-facing setup, runtime commands, security, and environment variables |
| [docs/SPEC.md](docs/SPEC.md) | Stable Telegram-visible behavior and UX rules |
| [docs/SPEC_PLUGINS_BUILTIN.md](docs/SPEC_PLUGINS_BUILTIN.md) | Detailed built-in plugin UI/UX specs |
| [docs/UI_EMOJI_SYSTEM.md](docs/UI_EMOJI_SYSTEM.md) | Canonical emoji and label system for Telegram UI |

---

## Built With

Developed with [Claude Code](https://claude.ai/claude-code), [Codex CLI](https://github.com/openai/codex), and [Gemini CLI](https://github.com/google-gemini/gemini-cli).

## License

MIT
