"""Google Calendar API client using service account authentication."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

from src.logging_config import logger

load_dotenv()

try:
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build

    HAS_GOOGLE_API = True
except ImportError:
    HAS_GOOGLE_API = False


@dataclass
class CalendarEvent:
    """One Google Calendar event."""

    id: str
    summary: str
    start: datetime
    end: datetime
    location: Optional[str] = None
    description: Optional[str] = None
    all_day: bool = False


def _extract_error_reason(e: Exception) -> str:
    """Extract error reason string from Google API exceptions. Returns raw Google message."""
    return str(e)


def _log_safe(msg: str) -> str:
    """Escape curly braces so loguru .format() doesn't choke on Google API error details."""
    return msg.replace("{", "{{").replace("}", "}}")


class GoogleCalendarClient:
    """Thin wrapper around Google Calendar API."""

    SCOPES = ["https://www.googleapis.com/auth/calendar"]

    def __init__(
        self,
        credentials_file: str | Path | None = None,
        calendar_id: str | None = None,
    ):
        self._credentials_file = str(
            credentials_file or os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "")
        )
        self._calendar_id = calendar_id or os.getenv("GOOGLE_CALENDAR_ID", "primary")
        self._service = None
        self.last_error: str = ""

    @property
    def available(self) -> bool:
        if not HAS_GOOGLE_API:
            return False
        if not self._credentials_file or not Path(self._credentials_file).exists():
            return False
        return True

    def _get_service(self):
        if self._service is not None:
            return self._service
        if not HAS_GOOGLE_API:
            raise RuntimeError("google-api-python-client not installed")
        creds = Credentials.from_service_account_file(
            self._credentials_file, scopes=self.SCOPES
        )
        self._service = build("calendar", "v3", credentials=creds)
        return self._service

    def list_events(
        self,
        date_start: datetime,
        date_end: datetime,
        max_results: int = 50,
    ) -> list[CalendarEvent]:
        try:
            service = self._get_service()
            result = (
                service.events()
                .list(
                    calendarId=self._calendar_id,
                    timeMin=date_start.isoformat(),
                    timeMax=date_end.isoformat(),
                    maxResults=max_results,
                    singleEvents=True,
                    orderBy="startTime",
                )
                .execute()
            )
            return [self._parse_event(item) for item in result.get("items", [])]
        except Exception as e:
            self.last_error = _extract_error_reason(e)
            logger.error("Google Calendar list_events error: " + _log_safe(self.last_error), exc_info=True)
            return []

    def get_event(self, event_id: str) -> Optional[CalendarEvent]:
        try:
            service = self._get_service()
            item = (
                service.events()
                .get(calendarId=self._calendar_id, eventId=event_id)
                .execute()
            )
            return self._parse_event(item)
        except Exception as e:
            self.last_error = _extract_error_reason(e)
            logger.error("Google Calendar get_event error: " + _log_safe(self.last_error), exc_info=True)
            return None

    def create_event(
        self,
        summary: str,
        start: datetime,
        end: datetime | None = None,
        all_day: bool = False,
    ) -> Optional[CalendarEvent]:
        try:
            service = self._get_service()

            if all_day:
                body = {
                    "summary": summary,
                    "start": {"date": start.strftime("%Y-%m-%d")},
                    "end": {
                        "date": (end or start + timedelta(days=1)).strftime("%Y-%m-%d")
                    },
                }
            else:
                if end is None:
                    end = start + timedelta(hours=1)
                tz = os.getenv("APP_TIMEZONE", "Asia/Seoul")
                body = {
                    "summary": summary,
                    "start": {"dateTime": start.isoformat(), "timeZone": tz},
                    "end": {"dateTime": end.isoformat(), "timeZone": tz},
                }

            item = (
                service.events()
                .insert(calendarId=self._calendar_id, body=body)
                .execute()
            )
            return self._parse_event(item)
        except Exception as e:
            self.last_error = _extract_error_reason(e)
            logger.error("Google Calendar create_event error: " + _log_safe(self.last_error), exc_info=True)
            return None

    def delete_event(self, event_id: str) -> bool:
        try:
            service = self._get_service()
            service.events().delete(
                calendarId=self._calendar_id, eventId=event_id
            ).execute()
            return True
        except Exception as e:
            self.last_error = _extract_error_reason(e)
            logger.error("Google Calendar delete_event error: " + _log_safe(self.last_error), exc_info=True)
            return False

    def update_event(
        self,
        event_id: str,
        summary: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> Optional[CalendarEvent]:
        try:
            service = self._get_service()
            existing = (
                service.events()
                .get(calendarId=self._calendar_id, eventId=event_id)
                .execute()
            )

            tz = os.getenv("APP_TIMEZONE", "Asia/Seoul")
            if summary is not None:
                existing["summary"] = summary
            if start is not None:
                existing["start"] = {"dateTime": start.isoformat(), "timeZone": tz}
            if end is not None:
                existing["end"] = {"dateTime": end.isoformat(), "timeZone": tz}

            item = (
                service.events()
                .update(
                    calendarId=self._calendar_id, eventId=event_id, body=existing
                )
                .execute()
            )
            return self._parse_event(item)
        except Exception as e:
            self.last_error = _extract_error_reason(e)
            logger.error("Google Calendar update_event error: " + _log_safe(self.last_error), exc_info=True)
            return None

    @staticmethod
    def _parse_event(item: dict) -> CalendarEvent:
        start_raw = item.get("start", {})
        end_raw = item.get("end", {})
        all_day = "date" in start_raw and "dateTime" not in start_raw

        if all_day:
            start = datetime.fromisoformat(start_raw["date"])
            end = datetime.fromisoformat(end_raw.get("date", start_raw["date"]))
        else:
            start = datetime.fromisoformat(
                start_raw.get("dateTime", "")
            )
            end = datetime.fromisoformat(
                end_raw.get("dateTime", start_raw.get("dateTime", ""))
            )

        return CalendarEvent(
            id=item.get("id", ""),
            summary=item.get("summary", "(제목 없음)"),
            start=start,
            end=end,
            location=item.get("location"),
            description=item.get("description"),
            all_day=all_day,
        )
