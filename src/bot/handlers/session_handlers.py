"""Session-related command handlers."""

from pathlib import Path

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from src.logging_config import logger, clear_context
from src.constants import SUPPORTED_MODELS, DEFAULT_MODEL
from ..constants import MAX_SESSION_NAME_LENGTH, get_model_emoji
from ..formatters import truncate_message
from ..middleware import authorized_only, authenticated_only
from .base import BaseHandler


class SessionHandlers(BaseHandler):
    """Session command handlers."""

    @authorized_only
    @authenticated_only
    async def new_session(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /new command.

        Usage:
            /new              - Show model selection buttons
            /new opus         - Opus model
            /new haiku name   - Haiku model + session name
        """
        chat_id = update.effective_chat.id
        user_id = str(chat_id)
        self._setup_request_context(chat_id)

        if not context.args:
            keyboard = [
                [
                    InlineKeyboardButton("Opus", callback_data="sess:new:opus"),
                    InlineKeyboardButton("Sonnet", callback_data="sess:new:sonnet"),
                    InlineKeyboardButton("Haiku", callback_data="sess:new:haiku"),
                ],
                [
                    InlineKeyboardButton("📋 세션 목록", callback_data="sess:list"),
                ]
            ]
            await update.message.reply_text(
                "🆕 <b>새 세션 생성</b>\n\n사용할 모델을 선택하세요:",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML"
            )
            clear_context()
            return

        model = DEFAULT_MODEL
        session_name = ""

        first_arg = context.args[0].lower()
        if first_arg in SUPPORTED_MODELS:
            model = first_arg
            if len(context.args) > 1:
                session_name = " ".join(context.args[1:])
        else:
            session_name = " ".join(context.args)

        if len(session_name) > MAX_SESSION_NAME_LENGTH:
            session_name = session_name[:MAX_SESSION_NAME_LENGTH]

        logger.info(f"/new 명령 수신 - 새 세션 요청 (model={model}, name={session_name or '(없음)'})")

        model_emoji = get_model_emoji(model)
        logger.trace("세션 생성 메시지 전송")
        await update.message.reply_text(f"새 Claude 세션 생성 중... {model_emoji} {model}")

        logger.trace(f"Claude 세션 생성 - model={model}")
        session_id = await self.claude.create_session()
        if not session_id:
            logger.error("Claude 세션 생성 실패")
            await update.message.reply_text("❌ Claude 세션 생성 실패. 다시 시도해주세요.")
            clear_context()
            return

        logger.info(f"새 세션 생성됨: {session_id[:8]}, model={model}")

        logger.trace("세션 저장 중")
        self.sessions.create_session(user_id, session_id, "(새 세션)", model=model, name=session_name)

        name_line = f"\n- 이름: {session_name}" if session_name else ""
        await update.message.reply_text(
            f"✅ 새 세션 시작!\n"
            f"- ID: <code>{session_id[:8]}</code>{name_line}\n"
            f"- 모델: {model_emoji} {model}",
            parse_mode="HTML"
        )
        logger.trace("/new 완료")
        clear_context()

    async def new_session_opus(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /new_opus command - shortcut for /new opus."""
        context.args = ["opus"]
        await self.new_session(update, context)

    async def new_session_sonnet(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /new_sonnet command - shortcut for /new sonnet."""
        context.args = ["sonnet"]
        await self.new_session(update, context)

    async def new_session_haiku(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /new_haiku command - shortcut for /new haiku."""
        context.args = ["haiku"]
        await self.new_session(update, context)

    async def new_session_haiku_speedy(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /new_haiku_speedy command - quick haiku session with name."""
        context.args = ["haiku", "Speedy"]
        await self.new_session(update, context)

    async def new_session_opus_smarty(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /new_opus_smarty command - smart opus session with name."""
        context.args = ["opus", "Smarty"]
        await self.new_session(update, context)

    @authorized_only
    @authenticated_only
    async def new_workspace_session(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /new_workspace command - create workspace-bound session.

        Usage:
            /new_workspace /path/to/workspace           - Default model (sonnet)
            /new_workspace /path/to/workspace opus      - Opus model
            /new_workspace /path/to/workspace haiku name - Haiku model + session name
        """
        from src.config import get_settings

        chat_id = update.effective_chat.id
        user_id = str(chat_id)
        args = context.args or []

        if not args:
            await update.message.reply_text(
                "📁 <b>워크스페이스 세션 사용법</b>\n\n"
                "<code>/new_workspace 경로 [모델] [이름]</code>\n\n"
                "예시:\n"
                "• <code>/new_workspace ~/Projects/my-app</code>\n"
                "• <code>/new_workspace ~/AiSandbox/bot opus</code>\n"
                "• <code>/new_workspace ~/work/api haiku API봇</code>",
                parse_mode="HTML"
            )
            return

        workspace_path = args[0]

        settings = get_settings()
        is_valid, error_msg = settings.validate_project_path(workspace_path)
        if not is_valid:
            await update.message.reply_text(f"{error_msg}", parse_mode="HTML")
            return

        model = None
        session_name = ""
        if len(args) > 1:
            potential_model = args[1].lower()
            if potential_model in SUPPORTED_MODELS:
                model = potential_model
                if len(args) > 2:
                    session_name = " ".join(args[2:])
            else:
                session_name = " ".join(args[1:])

        expanded_path = str(Path(workspace_path).expanduser().resolve())
        workspace_name = Path(expanded_path).name
        display_name = session_name or f"[ws]{workspace_name}"

        logger.info(f"/new_workspace - path={expanded_path}, model={model}, name={display_name}")

        session_id = await self.claude.create_session(workspace_path=expanded_path)
        if not session_id:
            await update.message.reply_text("❌ 세션 생성 실패", parse_mode="HTML")
            return

        self.sessions.create_session(
            user_id, session_id, f"(workspace: {workspace_name})",
            model=model, name=display_name, workspace_path=expanded_path
        )

        model_emoji = {"opus": "Opus", "sonnet": "Sonnet", "haiku": "Haiku"}.get(model or "sonnet", "Sonnet")

        claude_md_exists = (Path(expanded_path) / "CLAUDE.md").exists()
        claude_dir_exists = (Path(expanded_path) / ".claude").exists()
        config_status = "CLAUDE.md" if claude_md_exists else (".claude/" if claude_dir_exists else "설정 없음")

        await update.message.reply_text(
            f"📁 <b>워크스페이스 세션 생성됨</b>\n\n"
            f"- 경로: <code>{expanded_path}</code>\n"
            f"- 모델: {model_emoji}\n"
            f"- 이름: {display_name}\n"
            f"- 설정: {config_status}\n\n"
            f"이 세션은 워크스페이스의 CLAUDE.md 규칙을 따릅니다.",
            parse_mode="HTML"
        )

    async def model_opus_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /model_opus command - shortcut for /model opus."""
        context.args = ["opus"]
        await self.model_command(update, context)

    async def model_sonnet_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /model_sonnet command - shortcut for /model sonnet."""
        context.args = ["sonnet"]
        await self.model_command(update, context)

    async def model_haiku_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /model_haiku command - shortcut for /model haiku."""
        context.args = ["haiku"]
        await self.model_command(update, context)

    @authorized_only
    @authenticated_only
    async def model_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /model command - change current session's model.

        Usage:
            /model         - Show current model
            /model opus    - Change to Opus
            /model sonnet  - Change to Sonnet
            /model haiku   - Change to Haiku
        """
        chat_id = update.effective_chat.id
        user_id = str(chat_id)
        self._setup_request_context(chat_id)
        logger.info("/model command received")

        session_id = self.sessions.get_current_session_id(user_id)
        if not session_id:
            logger.trace("활성 세션 없음")
            await update.message.reply_text(
                "❌ 활성 세션이 없습니다.\n\n"
                "새 세션 시작:\n"
                "/new_opus - Opus\n"
                "/new_sonnet - Sonnet\n"
                "/new_haiku - Haiku",
                parse_mode="HTML"
            )
            clear_context()
            return

        current_model = self.sessions.get_session_model(user_id, session_id)

        if not context.args:
            model_emoji = get_model_emoji(current_model)
            logger.trace(f"현재 모델 표시: {current_model}")
            await update.message.reply_text(
                f"<b>현재 모델</b>: {model_emoji} {current_model}\n\n"
                f"변경하려면:\n"
                f"/model opus - 최고 품질\n"
                f"/model sonnet - 균형\n"
                f"/model haiku - 빠름",
                parse_mode="HTML"
            )
            clear_context()
            return

        new_model = context.args[0].lower()
        if new_model not in SUPPORTED_MODELS:
            await update.message.reply_text(
                f"❌ 지원하지 않는 모델: {new_model}\n\n"
                f"사용 가능: {', '.join(SUPPORTED_MODELS)}",
            )
            clear_context()
            return

        if new_model == current_model:
            model_emoji = get_model_emoji(current_model)
            await update.message.reply_text(f"이미 {model_emoji} {current_model} 모델을 사용 중입니다.")
            clear_context()
            return

        if self.sessions.set_session_model(user_id, session_id, new_model):
            logger.info(f"모델 변경됨: {current_model} -> {new_model}, session={session_id[:8]}")

            model_emoji = get_model_emoji(new_model)
            await update.message.reply_text(
                f"✅ 모델 변경됨!\n\n"
                f"- 이전: {current_model}\n"
                f"- 현재: {model_emoji} {new_model}",
                parse_mode="HTML"
            )
        else:
            await update.message.reply_text("❌ 세션을 찾을 수 없습니다.")

        clear_context()

    @authorized_only
    @authenticated_only
    async def session_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /session command - show current session info with buttons."""
        chat_id = update.effective_chat.id
        self._setup_request_context(chat_id)
        logger.info("/session command received")

        user_id = str(chat_id)

        logger.trace("Getting current session")
        session_id = self.sessions.get_current_session_id(user_id)
        if not session_id:
            logger.trace("활성 세션 없음")
            keyboard = [
                [
                    InlineKeyboardButton("+Opus", callback_data="sess:new:opus"),
                    InlineKeyboardButton("+Sonnet", callback_data="sess:new:sonnet"),
                    InlineKeyboardButton("+Haiku", callback_data="sess:new:haiku"),
                ],
                [
                    InlineKeyboardButton("📋 세션 목록", callback_data="sess:list"),
                ]
            ]
            await update.message.reply_text(
                "❌ 활성 세션이 없습니다.\n\n새 세션 생성:",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML"
            )
            clear_context()
            return

        logger.trace(f"Getting session history - session={session_id[:8]}")
        history_entries = self.sessions.get_session_history_entries(user_id, session_id)
        count = len(history_entries)
        model = self.sessions.get_session_model(user_id, session_id)
        model_emoji = get_model_emoji(model)
        session_name = self.sessions.get_session_name(user_id, session_id)
        logger.trace(f"History count: {count}, model: {model}, name: {session_name or '(none)'}")

        recent = history_entries[-10:]
        history_lines = []
        start_idx = len(history_entries) - len(recent) + 1

        processor_emoji = {
            "claude": "",
            "command": "[cmd]",
            "rejected": "[x]",
        }

        for i, entry in enumerate(recent, start=start_idx):
            msg = entry.get("message", "") if isinstance(entry, dict) else str(entry)
            processor = entry.get("processor", "claude") if isinstance(entry, dict) else "claude"

            if processor.startswith("plugin:"):
                emoji = "[plugin]"
            else:
                emoji = processor_emoji.get(processor, "")

            short_q = truncate_message(msg, 35)
            history_lines.append(f"{i}. {emoji} {short_q}")

        history_text = "\n".join(history_lines) if history_lines else "(empty)"

        name_line = f"- Name: {session_name}\n" if session_name else ""

        keyboard = [
            [
                InlineKeyboardButton("Opus", callback_data=f"sess:model:opus:{session_id}"),
                InlineKeyboardButton("Sonnet", callback_data=f"sess:model:sonnet:{session_id}"),
                InlineKeyboardButton("Haiku", callback_data=f"sess:model:haiku:{session_id}"),
            ],
            [
                InlineKeyboardButton("📜 히스토리", callback_data=f"sess:history:{session_id}"),
                InlineKeyboardButton("🗑️ 삭제", callback_data=f"sess:delete:{session_id}"),
            ],
            [
                InlineKeyboardButton("📋 세션 목록", callback_data="sess:list"),
            ]
        ]

        await update.message.reply_text(
            f"<b>현재 세션</b>\n\n"
            f"- ID: <code>{session_id[:8]}</code>\n"
            f"{name_line}"
            f"- 모델: {model_emoji} {model}\n"
            f"- 메시지: {count}개\n\n"
            f"<b>대화 내용</b> (최근 10개)\n{history_text}",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )
        logger.trace("/session complete")
        clear_context()

    @authorized_only
    @authenticated_only
    async def session_list_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /session_list command - button-based session list."""
        from ..session_queue import session_queue_manager

        chat_id = update.effective_chat.id
        self._setup_request_context(chat_id)
        logger.info("/session_list command received")

        user_id = str(chat_id)

        logger.trace("Getting session list")
        sessions = self.sessions.list_sessions(user_id)

        current_session_id = self.sessions.get_current_session_id(user_id)

        lines = ["<b>세션 목록</b>\n"]
        buttons = []

        if not sessions:
            lines.append("세션이 없습니다.")
        else:
            for s in sessions[:10]:
                sid = s["full_session_id"]
                short_id = s["session_id"]
                name = s.get("name") or f"Session {short_id}"
                model = s.get("model", "sonnet")
                model_emoji = {"opus": "[O]", "sonnet": "[S]", "haiku": "[H]"}.get(model, "[S]")

                is_current = "> " if sid == current_session_id else ""
                is_locked = session_queue_manager.is_locked(sid)
                lock_indicator = " [locked]" if is_locked else ""
                lines.append(f"{is_current}{model_emoji} <b>{name}</b> (<code>{short_id}</code>){lock_indicator}")

                buttons.append([
                    InlineKeyboardButton(f"{name[:10]}", callback_data=f"sess:switch:{sid}"),
                    InlineKeyboardButton("History", callback_data=f"sess:history:{sid}"),
                    InlineKeyboardButton("Del", callback_data=f"sess:delete:{sid}"),
                ])

        buttons.append([
            InlineKeyboardButton("+Opus", callback_data="sess:new:opus"),
            InlineKeyboardButton("+Sonnet", callback_data="sess:new:sonnet"),
            InlineKeyboardButton("+Haiku", callback_data="sess:new:haiku"),
        ])
        buttons.append([
            InlineKeyboardButton("Refresh", callback_data="sess:list"),
            InlineKeyboardButton("Tasks", callback_data="lock:refresh"),
        ])

        await update.message.reply_text(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode="HTML"
        )
        logger.trace("/session_list complete")
        clear_context()

    @authorized_only
    @authenticated_only
    async def switch_session_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /s_<id> command for session switching."""
        chat_id = update.effective_chat.id
        self._setup_request_context(chat_id)

        user_id = str(chat_id)

        text = update.message.text
        if not text.startswith("/s_"):
            clear_context()
            return

        target = text[3:]
        logger.info(f"Session switch request: /s_{target}")

        logger.trace(f"Searching session - prefix={target}")
        target_info = self.sessions.get_session_by_prefix(user_id, target)
        if not target_info:
            logger.debug(f"Session not found: {target}")
            await update.message.reply_text(f"Session '{target}' not found.")
            clear_context()
            return

        logger.trace(f"Switching session - target={target_info['session_id']}")
        if self.sessions.switch_session(user_id, target):
            logger.info(f"Session switch successful: {target_info['session_id']}")
            await update.message.reply_text(
                f"Session switched!\n\n"
                f"- ID: <code>{target_info['session_id']}</code>\n"
                f"- Messages: {target_info['history_count']}",
                parse_mode="HTML"
            )
        else:
            logger.error(f"Session switch failed: {target}")
            await update.message.reply_text("Session switch failed")

        clear_context()

    @authorized_only
    @authenticated_only
    async def rename_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /rename command - rename current session or specific session."""
        chat_id = update.effective_chat.id
        user_id = str(chat_id)
        self._setup_request_context(chat_id)
        logger.info("/rename command received")

        text = update.message.text

        # /r_sessionID_newname format support
        if text.startswith("/r_") and "_" in text[3:]:
            parts = text[3:].split("_", 1)
            if len(parts) == 2:
                target_prefix = parts[0]
                new_name = parts[1]

                target_info = self.sessions.get_session_by_prefix(user_id, target_prefix)
                if not target_info:
                    logger.debug(f"Session not found: {target_prefix}")
                    await update.message.reply_text(f"Session <code>{target_prefix}</code> not found.", parse_mode="HTML")
                    clear_context()
                    return

                session_id = target_info["full_session_id"]

                if len(new_name) > 50:
                    await update.message.reply_text("Name too long. (max 50 chars)")
                    clear_context()
                    return

                if self.sessions.rename_session(user_id, session_id, new_name):
                    logger.info(f"Session renamed: {session_id[:8]} -> {new_name}")
                    await update.message.reply_text(
                        f"Session renamed!\n\n"
                        f"- Session: <code>{session_id[:8]}</code>\n"
                        f"- Name: {new_name}",
                        parse_mode="HTML"
                    )
                else:
                    await update.message.reply_text("Rename failed")

                clear_context()
                return
            else:
                await update.message.reply_text(
                    "Usage: <code>/r_sessionID_newname</code>\n"
                    "Example: <code>/r_a1b2c3d4_MyBot</code>",
                    parse_mode="HTML"
                )
                clear_context()
                return

        # Current session rename (/rename or /rename_newname)
        session_id = self.sessions.get_current_session_id(user_id)
        if not session_id:
            logger.trace("활성 세션 없음")
            await update.message.reply_text("❌ 활성 세션이 없습니다.")
            clear_context()
            return

        # /rename_newname format support
        if text.startswith("/rename_"):
            new_name = text[8:]
        elif context.args:
            new_name = " ".join(context.args)
        else:
            current_name = self.sessions.get_session_name(user_id, session_id)
            logger.trace(f"현재 이름: {current_name or '(없음)'}")
            await update.message.reply_text(
                f"<b>세션 이름 변경</b>\n\n"
                f"- 현재: {current_name or '(이름 없음)'}\n"
                f"- 세션: <code>{session_id[:8]}</code>\n\n"
                f"사용법: <code>/rename_새이름</code>\n"
                f"또는: <code>/r_세션ID_새이름</code>",
                parse_mode="HTML"
            )
            clear_context()
            return

        if len(new_name) > 50:
            await update.message.reply_text("❌ 이름이 너무 깁니다. (최대 50자)")
            clear_context()
            return

        if self.sessions.rename_session(user_id, session_id, new_name):
            await update.message.reply_text(
                f"✅ 세션 이름 변경됨!\n\n"
                f"- 세션: <code>{session_id[:8]}</code>\n"
                f"- 이름: {new_name}",
                parse_mode="HTML"
            )
        else:
            await update.message.reply_text("❌ 이름 변경 실패")

        clear_context()

    @authorized_only
    @authenticated_only
    async def delete_session_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /d_<id> command for deleting a session."""
        chat_id = update.effective_chat.id
        user_id = str(chat_id)
        self._setup_request_context(chat_id)

        text = update.message.text
        if text.startswith("/delete_"):
            target = text[8:]
        elif text.startswith("/d_"):
            target = text[3:]
        else:
            clear_context()
            return

        logger.info(f"세션 삭제 요청: {target}")

        target_info = self.sessions.get_session_by_prefix(user_id, target)
        if not target_info:
            logger.debug(f"세션을 찾을 수 없음: {target}")
            await update.message.reply_text(f"❌ 세션 '{target}'을(를) 찾을 수 없습니다.")
            clear_context()
            return

        full_session_id = target_info["full_session_id"]
        session_name = target_info.get("name", "")

        current_session_id = self.sessions.get_current_session_id(user_id)
        if current_session_id == full_session_id:
            name_info = f" ({session_name})" if session_name else ""
            await update.message.reply_text(
                f"❌ 현재 세션은 삭제할 수 없습니다.\n\n"
                f"- ID: <code>{target_info['session_id']}</code>{name_info}\n\n"
                f"먼저 다른 세션으로 전환하거나 새 세션을 만드세요.",
                parse_mode="HTML"
            )
            clear_context()
            return

        if self.sessions.delete_session(user_id, full_session_id):
            name_info = f" ({session_name})" if session_name else ""
            await update.message.reply_text(
                f"🗑️ 세션 삭제됨!\n\n"
                f"- ID: <code>{target_info['session_id']}</code>{name_info}\n"
                f"- 메시지: {target_info['history_count']}개",
                parse_mode="HTML"
            )
        else:
            await update.message.reply_text("❌ 세션 삭제 실패")

        clear_context()

    @authorized_only
    @authenticated_only
    async def history_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /h_<id> command for viewing session history."""
        chat_id = update.effective_chat.id
        self._setup_request_context(chat_id)

        user_id = str(chat_id)

        text = update.message.text
        if text.startswith("/history_"):
            target = text[9:]
        elif text.startswith("/h_"):
            target = text[3:]
        else:
            clear_context()
            return

        logger.info(f"히스토리 조회 요청: {target}")

        logger.trace(f"세션 검색 - prefix={target}")
        target_info = self.sessions.get_session_by_prefix(user_id, target)
        if not target_info:
            logger.debug(f"세션을 찾을 수 없음: {target}")
            await update.message.reply_text(f"❌ 세션 '{target}'을(를) 찾을 수 없습니다.")
            clear_context()
            return

        logger.trace(f"히스토리 조회 - session={target_info['full_session_id'][:8]}")
        history = self.sessions.get_session_history(user_id, target_info["full_session_id"])
        if not history:
            logger.trace("히스토리 없음")
            await update.message.reply_text("📭 히스토리가 없습니다.")
            clear_context()
            return

        logger.trace(f"History count: {len(history)}")

        history_lines = []
        for i, q in enumerate(history, start=1):
            short_q = truncate_message(q, 60)
            history_lines.append(f"{i}. {short_q}")

        history_text = "\n".join(history_lines)

        await update.message.reply_text(
            f"<b>세션 히스토리</b>\n"
            f"- ID: <code>{target_info['session_id']}</code>\n"
            f"- 메시지: {len(history)}개\n\n"
            f"{history_text}\n\n"
            f"/s_{target_info['session_id']} 이 세션으로 전환",
            parse_mode="HTML"
        )
        logger.trace("히스토리 조회 완료")
        clear_context()

    @authorized_only
    async def back_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /back command - return to previous session."""
        chat_id = update.effective_chat.id
        user_id = str(chat_id)
        self._setup_request_context(chat_id)
        logger.info("/back 명령 수신")

        prev_session_id = self.sessions.get_previous_session_id(user_id)
        if not prev_session_id:
            await update.message.reply_text(
                "❌ 이전 세션이 없습니다.\n\n"
                "/session_list 로 세션 목록 확인"
            )
            clear_context()
            return

        session_info = self.sessions.get_session_by_prefix(user_id, prev_session_id[:8])
        if not session_info:
            await update.message.reply_text("❌ 이전 세션을 찾을 수 없습니다.")
            self.sessions.set_previous_session_id(user_id, None)
            clear_context()
            return

        self.sessions.set_current(user_id, prev_session_id)
        self.sessions.set_previous_session_id(user_id, None)

        name = self.sessions.get_session_name(user_id, prev_session_id)
        name_display = f" ({name})" if name else ""

        await update.message.reply_text(
            f"✅ 세션으로 돌아왔습니다!\n\n"
            f"- ID: <code>{prev_session_id[:8]}</code>{name_display}",
            parse_mode="HTML"
        )
        clear_context()
