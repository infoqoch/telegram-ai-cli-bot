"""날씨 플러그인 - Open-Meteo API 사용 (무료, 키 불필요)."""

import json
import re
from pathlib import Path
from typing import Optional
import aiohttp

from src.plugins.loader import Plugin, PluginResult


class WeatherPlugin(Plugin):
    """날씨 조회 플러그인 (Open-Meteo API)."""

    name = "weather"
    description = "날씨 조회 및 위치 설정"

    # Open-Meteo API (무료, 키 불필요)
    GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
    WEATHER_URL = "https://api.open-meteo.com/v1/forecast"

    # 트리거 패턴
    WEATHER_PATTERNS = [
        r"날씨",
        r"기온",
        r"weather",
    ]
    SET_LOCATION_PATTERNS = [
        r"위치\s*설정\s*[:\-]?\s*(.+)",
        r"날씨\s*위치\s*[:\-]?\s*(.+)",
        r"(.+)\s*날씨\s*설정",
    ]

    # 날씨 코드 -> 이모지/설명
    WEATHER_CODES = {
        0: ("☀️", "맑음"),
        1: ("🌤️", "대체로 맑음"),
        2: ("⛅", "구름 조금"),
        3: ("☁️", "흐림"),
        45: ("🌫️", "안개"),
        48: ("🌫️", "안개(서리)"),
        51: ("🌧️", "이슬비"),
        53: ("🌧️", "이슬비"),
        55: ("🌧️", "이슬비"),
        61: ("🌧️", "비"),
        63: ("🌧️", "비"),
        65: ("🌧️", "폭우"),
        71: ("🌨️", "눈"),
        73: ("🌨️", "눈"),
        75: ("❄️", "폭설"),
        80: ("🌦️", "소나기"),
        81: ("🌦️", "소나기"),
        82: ("⛈️", "폭우"),
        95: ("⛈️", "뇌우"),
        96: ("⛈️", "뇌우(우박)"),
        99: ("⛈️", "뇌우(우박)"),
    }

    async def can_handle(self, message: str, chat_id: int) -> bool:
        """날씨 관련 메시지인지 확인."""
        msg = message.strip().lower()

        for pattern in self.WEATHER_PATTERNS:
            if re.search(pattern, msg, re.IGNORECASE):
                return True

        for pattern in self.SET_LOCATION_PATTERNS:
            if re.search(pattern, msg, re.IGNORECASE):
                return True

        return False

    async def handle(self, message: str, chat_id: int) -> PluginResult:
        """날씨 명령 처리."""
        msg = message.strip()

        # 위치 설정 확인
        for pattern in self.SET_LOCATION_PATTERNS:
            match = re.search(pattern, msg, re.IGNORECASE)
            if match:
                location = match.group(1).strip()
                if location:
                    return await self._set_location(chat_id, location)

        # 날씨 조회
        return await self._get_weather(chat_id)

    def _get_location_file(self, chat_id: int) -> Path:
        """위치 설정 파일 경로."""
        data_dir = self.get_data_dir(self._base_dir)
        return data_dir / f"{chat_id}.json"

    def _load_location(self, chat_id: int) -> Optional[dict]:
        """저장된 위치 로드."""
        file_path = self._get_location_file(chat_id)
        if not file_path.exists():
            return None
        try:
            return json.loads(file_path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _save_location(self, chat_id: int, location: dict) -> None:
        """위치 저장."""
        file_path = self._get_location_file(chat_id)
        file_path.write_text(
            json.dumps(location, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

    async def _geocode(self, query: str) -> Optional[dict]:
        """지명 → 좌표 변환."""
        try:
            async with aiohttp.ClientSession() as session:
                params = {"name": query, "count": 1, "language": "ko"}
                async with session.get(self.GEOCODING_URL, params=params) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json()
                    results = data.get("results", [])
                    if not results:
                        return None
                    r = results[0]
                    return {
                        "name": r.get("name", query),
                        "country": r.get("country", ""),
                        "lat": r["latitude"],
                        "lon": r["longitude"],
                    }
        except Exception:
            return None

    async def _fetch_weather(self, lat: float, lon: float) -> Optional[dict]:
        """날씨 데이터 조회."""
        try:
            async with aiohttp.ClientSession() as session:
                params = {
                    "latitude": lat,
                    "longitude": lon,
                    "current": "temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m",
                    "daily": "temperature_2m_max,temperature_2m_min,weather_code",
                    "timezone": "Asia/Seoul",
                    "forecast_days": 3,
                }
                async with session.get(self.WEATHER_URL, params=params) as resp:
                    if resp.status != 200:
                        return None
                    return await resp.json()
        except Exception:
            return None

    async def _set_location(self, chat_id: int, location_name: str) -> PluginResult:
        """위치 설정."""
        geo = await self._geocode(location_name)
        if not geo:
            return PluginResult(
                handled=True,
                response=f"❌ '{location_name}'을(를) 찾을 수 없습니다.\n다른 지명으로 시도해주세요."
            )

        self._save_location(chat_id, geo)

        return PluginResult(
            handled=True,
            response=(
                f"📍 위치 설정 완료!\n\n"
                f"<b>{geo['name']}</b> ({geo['country']})\n"
                f"위도: {geo['lat']:.4f}, 경도: {geo['lon']:.4f}\n\n"
                f"<code>날씨</code>로 날씨를 확인하세요."
            )
        )

    async def _get_weather(self, chat_id: int) -> PluginResult:
        """날씨 조회."""
        location = self._load_location(chat_id)
        if not location:
            return PluginResult(
                handled=True,
                response=(
                    "📍 위치가 설정되지 않았습니다.\n\n"
                    "<code>위치 설정: 서울</code>\n"
                    "<code>날씨 위치: 부산</code>\n\n"
                    "위 형식으로 위치를 먼저 설정하세요."
                )
            )

        weather = await self._fetch_weather(location["lat"], location["lon"])
        if not weather:
            return PluginResult(
                handled=True,
                response="❌ 날씨 정보를 가져올 수 없습니다. 잠시 후 다시 시도해주세요."
            )

        # 현재 날씨
        current = weather.get("current", {})
        temp = current.get("temperature_2m", "?")
        humidity = current.get("relative_humidity_2m", "?")
        wind = current.get("wind_speed_10m", "?")
        code = current.get("weather_code", 0)
        emoji, desc = self.WEATHER_CODES.get(code, ("🌡️", "알 수 없음"))

        # 일별 예보
        daily = weather.get("daily", {})
        dates = daily.get("time", [])
        max_temps = daily.get("temperature_2m_max", [])
        min_temps = daily.get("temperature_2m_min", [])
        codes = daily.get("weather_code", [])

        forecast_lines = []
        for i, date in enumerate(dates[:3]):
            day_emoji, day_desc = self.WEATHER_CODES.get(codes[i] if i < len(codes) else 0, ("🌡️", ""))
            max_t = max_temps[i] if i < len(max_temps) else "?"
            min_t = min_temps[i] if i < len(min_temps) else "?"
            forecast_lines.append(f"{date[5:]} {day_emoji} {min_t}° / {max_t}°")

        forecast_text = "\n".join(forecast_lines)

        return PluginResult(
            handled=True,
            response=(
                f"{emoji} <b>{location['name']} 날씨</b>\n\n"
                f"<b>현재</b>\n"
                f"• 날씨: {desc}\n"
                f"• 기온: {temp}°C\n"
                f"• 습도: {humidity}%\n"
                f"• 풍속: {wind} km/h\n\n"
                f"<b>3일 예보</b>\n"
                f"<code>{forecast_text}</code>\n\n"
                f"<code>위치 설정: 도시명</code>으로 위치 변경"
            )
        )
