"""Helpers for persisted Telegram inline-button metadata."""

from __future__ import annotations

import json
from typing import Any, Optional

from telegram import InlineKeyboardButton


def decode_delivery_markup_rows(payload: object) -> list[list[InlineKeyboardButton]]:
    """Decode nested button payload rows into Telegram inline buttons."""
    if not isinstance(payload, list):
        return []

    rows: list[list[InlineKeyboardButton]] = []
    for row in payload:
        if not isinstance(row, list):
            continue
        buttons: list[InlineKeyboardButton] = []
        for item in row:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            callback_data = item.get("callback_data")
            url = item.get("url")
            if not isinstance(text, str):
                continue
            if isinstance(callback_data, str):
                buttons.append(InlineKeyboardButton(text, callback_data=callback_data))
            elif isinstance(url, str):
                buttons.append(InlineKeyboardButton(text, url=url))
        if buttons:
            rows.append(buttons)
    return rows


def decode_delivery_markup_json(markup_json: Optional[str]) -> list[list[InlineKeyboardButton]]:
    """Decode persisted button JSON into Telegram inline-button rows."""
    if not markup_json:
        return []
    payload: Any = json.loads(markup_json)
    return decode_delivery_markup_rows(payload)
