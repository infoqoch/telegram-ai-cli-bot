"""Retry failed Telegram message deliveries."""

from __future__ import annotations

from telegram import InlineKeyboardMarkup

from typing import TYPE_CHECKING

from src.bot.formatters import escape_html, split_message
from src.logging_config import logger
from src.services.delivery_markup import decode_delivery_markup_json

if TYPE_CHECKING:
    from telegram import Bot
    from src.repository.repository import Repository

MAX_DELIVERY_ATTEMPTS = 10


class DeliveryRetryService:
    """Periodically retries failed Telegram message deliveries."""

    def __init__(self, repo: Repository) -> None:
        self._repo = repo

    async def retry_failed_deliveries(self, bot: Bot) -> int:
        """Retry all eligible failed deliveries. Returns count of successfully retried."""
        failed = self._repo.get_failed_deliveries(max_attempts=MAX_DELIVERY_ATTEMPTS)
        if not failed:
            return 0

        logger.info(f"[DeliveryRetry] Found {len(failed)} failed deliveries to retry")
        success_count = 0

        for row in failed:
            job_id = row["id"]
            chat_id = row["chat_id"]
            delivery_text = row["delivery_text"]
            delivery_markup_json = row.get("delivery_markup_json")
            attempts = row["delivery_attempts"]

            # Optimistic lock - claim this row
            if not self._repo.claim_delivery_for_retry(job_id):
                logger.debug(f"[DeliveryRetry] job_id={job_id} already claimed, skipping")
                continue

            try:
                # Increment before send: counts attempt even if send fails (reset to 'failed' on error)
                self._repo.increment_delivery_attempts(job_id)
                chunks = split_message(delivery_text)
                markup = self._build_retry_markup(delivery_markup_json)

                for index, chunk in enumerate(chunks, start=1):
                    chunk_markup = markup if index == len(chunks) else None
                    try:
                        await bot.send_message(
                            chat_id=chat_id,
                            text=chunk,
                            parse_mode="HTML",
                            reply_markup=chunk_markup,
                        )
                    except Exception as html_err:
                        logger.debug(f"[DeliveryRetry] HTML send failed, trying plain: {html_err}")
                        await bot.send_message(
                            chat_id=chat_id,
                            text=chunk,
                            reply_markup=chunk_markup,
                        )

                self._repo.mark_message_delivered(job_id)
                success_count += 1
                logger.info(f"[DeliveryRetry] Successfully retried job_id={job_id}, chat_id={chat_id}")

            except Exception as exc:
                logger.warning(
                    f"[DeliveryRetry] Retry failed - job_id={job_id}, "
                    f"attempts={attempts + 1}, error={exc}"
                )
                # Check if max attempts reached
                if attempts + 1 >= MAX_DELIVERY_ATTEMPTS:
                    self._repo.mark_delivery_abandoned(job_id)
                    logger.warning(f"[DeliveryRetry] Abandoned job_id={job_id} after {attempts + 1} attempts")
                    # Try to notify user about abandoned message
                    try:
                        preview = escape_html(
                            (delivery_text[:100] + "...") if len(delivery_text) > 100 else delivery_text
                        )
                        await bot.send_message(
                            chat_id=chat_id,
                            text=f"⚠️ Message delivery failed (attempt {attempts + 1}).\n\n"
                                 f"<i>Preview:</i> {preview}",
                            parse_mode="HTML",
                        )
                    except Exception:
                        logger.error(f"[DeliveryRetry] Abandon notification also failed - job_id={job_id}")
                else:
                    # Reset back to failed for next retry cycle
                    self._repo.mark_message_delivery_failed(job_id, str(exc))

        if success_count:
            logger.info(f"[DeliveryRetry] Retry cycle complete: {success_count}/{len(failed)} succeeded")

        return success_count

    @staticmethod
    def _build_retry_markup(delivery_markup_json: str | None) -> InlineKeyboardMarkup | None:
        """Rebuild persisted inline buttons for a retry send."""
        try:
            rows = decode_delivery_markup_json(delivery_markup_json)
        except Exception:
            return None
        return InlineKeyboardMarkup(rows) if rows else None
