"""Weather plugin - button-based weather lookup (Open-Meteo API)."""

import csv
from collections import OrderedDict
from pathlib import Path
from typing import Optional, cast

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from src.network_guard import NetworkUnavailable, network_guard
from src.plugins.loader import (
    PLUGIN_SURFACE_CATALOG,
    PLUGIN_SURFACE_MAIN_MENU,
    Plugin,
    PluginMenuEntry,
    PluginResult,
)
from src.plugins.storage import WeatherLocationStore
from src.repository.adapters import RepositoryWeatherLocationStore


def _load_cities_csv(csv_path: Path) -> tuple[dict[str, str], dict[str, list[str]]]:
    """Load city data from CSV file.

    Returns:
        (korean_to_english, province_to_cities):
            - korean_to_english: Korean city name -> English name mapping
            - province_to_cities: province/metro -> [list of cities]
    """
    korean_to_english: dict[str, str] = {}
    province_to_cities: dict[str, list[str]] = OrderedDict()

    if not csv_path.exists():
        return korean_to_english, province_to_cities

    with csv_path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            province = row["province"]
            city = row["city"]
            english = row["english"]
            korean_to_english[city] = english
            if province not in province_to_cities:
                province_to_cities[province] = []
            if city not in province_to_cities[province]:
                province_to_cities[province].append(city)

    return korean_to_english, province_to_cities


# Metro/special cities: province and city name are identical
_METRO_CITIES = {"서울", "부산", "대구", "인천", "광주", "대전", "울산", "세종"}


class WeatherPlugin(Plugin):
    """Button-based weather plugin (Open-Meteo API)."""

    name = "weather"
    description = "Weather lookup and location settings"
    display_name = "Weather"
    MENU_ENTRY = PluginMenuEntry(
        label="🌤️ Weather",
        surfaces=(PLUGIN_SURFACE_CATALOG, PLUGIN_SURFACE_MAIN_MENU),
        priority=30,
        default_promoted=True,
    )
    usage = (
        "🌤️ <b>Weather Plugin</b>\n\n"
        "<b>Check Weather</b>\n"
        "• <code>weather</code> - Select a city and check weather\n\n"
        "<b>Location Settings</b>\n"
        "• Use the buttons on the weather screen to select a city"
    )

    CALLBACK_PREFIX = "weather:"

    TRIGGER_KEYWORDS = ["weather", "날씨", "기온"]

    def get_schema(self) -> str:
        return """
CREATE TABLE IF NOT EXISTS weather_locations (
    chat_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    country TEXT,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

    GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
    WEATHER_URL = "https://api.open-meteo.com/v1/forecast"

    # Load from CSV once at class load time
    _CITIES_CSV = Path(__file__).parent / "cities.csv"
    KOREAN_TO_ENGLISH, PROVINCE_TO_CITIES = _load_cities_csv(_CITIES_CSV)

    EXCLUDE_PATTERNS = [
        r"(란|이란|가|이)\s*(뭐|무엇|뭔)",
        r"영어로|번역|translate",
        r"어떻게|왜|원리",
        r"알려줘|설명",
    ]

    WEATHER_CODES = {
        0: ("☀️", "Clear"), 1: ("🌤️", "Mostly clear"), 2: ("⛅", "Partly cloudy"),
        3: ("☁️", "Overcast"), 45: ("🌫️", "Fog"), 48: ("🌫️", "Rime fog"),
        51: ("🌧️", "Drizzle"), 53: ("🌧️", "Drizzle"), 55: ("🌧️", "Drizzle"),
        61: ("🌧️", "Rain"), 63: ("🌧️", "Rain"), 65: ("🌧️", "Heavy rain"),
        71: ("🌨️", "Snow"), 73: ("🌨️", "Snow"), 75: ("❄️", "Heavy snow"),
        80: ("🌦️", "Showers"), 81: ("🌦️", "Showers"), 82: ("⛈️", "Heavy showers"),
        95: ("⛈️", "Thunderstorm"), 96: ("⛈️", "Thunderstorm (hail)"), 99: ("⛈️", "Thunderstorm (hail)"),
    }

    @property
    def store(self) -> WeatherLocationStore:
        """Weather location storage adapter bound by the plugin runtime."""
        return cast(WeatherLocationStore, self.storage)

    def build_storage(self, repository):
        """Bind weather persistence through a bounded adapter."""
        return RepositoryWeatherLocationStore(repository)

    async def handle(self, message: str, chat_id: int) -> PluginResult:
        return await self._get_weather(chat_id)

    def handle_callback(self, callback_data: str, chat_id: int) -> dict:
        import asyncio

        parts = callback_data.split(":")
        if len(parts) < 2:
            return {"text": "❌ Invalid request.", "edit": True}

        action = parts[1]

        if action == "city":
            city = parts[2] if len(parts) > 2 else "서울"
            loop = asyncio.get_event_loop()
            return loop.run_until_complete(self._handle_city_weather(chat_id, city))
        elif action == "refresh":
            loop = asyncio.get_event_loop()
            return loop.run_until_complete(self._handle_refresh(chat_id))
        elif action == "select":
            return self._handle_province_select()
        elif action == "province":
            province = parts[2] if len(parts) > 2 else ""
            return self._handle_province_cities(province)
        else:
            return {"text": "❌ Unknown command.", "edit": True}

    async def handle_callback_async(self, callback_data: str, chat_id: int) -> dict:
        parts = callback_data.split(":")
        if len(parts) < 2:
            return {"text": "❌ Invalid request.", "edit": True}

        action = parts[1]

        if action == "city":
            city = parts[2] if len(parts) > 2 else "서울"
            return await self._handle_city_weather(chat_id, city)
        elif action == "refresh":
            return await self._handle_refresh(chat_id)
        elif action == "select":
            return self._handle_province_select()
        elif action == "province":
            province = parts[2] if len(parts) > 2 else ""
            return self._handle_province_cities(province)
        else:
            return {"text": "❌ Unknown command.", "edit": True}

    def _handle_province_select(self) -> dict:
        """Step 1: Show province/metro list."""
        buttons = []
        row = []
        for province in self.PROVINCE_TO_CITIES:
            row.append(InlineKeyboardButton(
                province,
                callback_data=f"weather:province:{province}"
                if province not in _METRO_CITIES
                else f"weather:city:{province}",
            ))
            if len(row) == 3:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)

        return {
            "text": "🌤️ <b>Select Region</b>\n\nChoose a region to check weather:",
            "reply_markup": InlineKeyboardMarkup(buttons),
            "edit": True,
        }

    def _handle_province_cities(self, province: str) -> dict:
        """Step 2: Show cities in the selected province."""
        cities = self.PROVINCE_TO_CITIES.get(province, [])
        if not cities:
            return {"text": f"❌ No cities registered for '{province}'.", "edit": True}

        buttons = []
        row = []
        for city in cities:
            row.append(InlineKeyboardButton(city, callback_data=f"weather:city:{city}"))
            if len(row) == 3:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)

        buttons.append([InlineKeyboardButton("◀️ Back", callback_data="weather:select")])

        return {
            "text": f"🌤️ <b>{province} - Select City</b>\n\nChoose a city:",
            "reply_markup": InlineKeyboardMarkup(buttons),
            "edit": True,
        }

    async def _handle_city_weather(self, chat_id: int, city: str) -> dict:
        try:
            geo = await self._geocode(city)
        except NetworkUnavailable as exc:
            return {"text": f"❌ Weather network unavailable.\n\n<code>{exc}</code>", "edit": True}
        if not geo:
            return {"text": f"❌ City '{city}' not found.", "edit": True}

        try:
            weather = await self._fetch_weather(geo["lat"], geo["lon"])
        except NetworkUnavailable as exc:
            return {"text": f"❌ Weather network unavailable.\n\n<code>{exc}</code>", "edit": True}
        if not weather:
            return {"text": "❌ Unable to fetch weather data.", "edit": True}

        text = self._format_weather(geo, weather)

        buttons = [
            [
                InlineKeyboardButton("🔄 Refresh", callback_data=f"weather:city:{city}"),
                InlineKeyboardButton("📍 Other city", callback_data="weather:select"),
            ],
            [InlineKeyboardButton("✨ Work with AI", callback_data="aiwork:weather")],
        ]

        return {
            "text": text,
            "reply_markup": InlineKeyboardMarkup(buttons),
            "edit": True,
        }

    async def _handle_refresh(self, chat_id: int) -> dict:
        location = self._load_location(chat_id)
        if not location:
            return self._handle_province_select()

        try:
            weather = await self._fetch_weather(location["lat"], location["lon"])
        except NetworkUnavailable as exc:
            return {"text": f"❌ Weather network unavailable.\n\n<code>{exc}</code>", "edit": True}
        if not weather:
            return {"text": "❌ Unable to fetch weather data.", "edit": True}

        text = self._format_weather(location, weather)

        buttons = [
            [
                InlineKeyboardButton("🔄 Refresh", callback_data="weather:refresh"),
                InlineKeyboardButton("📍 Other city", callback_data="weather:select"),
            ]
        ]

        return {
            "text": text,
            "reply_markup": InlineKeyboardMarkup(buttons),
            "edit": True,
        }

    def _format_weather(self, location: dict, weather: dict) -> str:
        current = weather.get("current", {})
        temp = current.get("temperature_2m", "?")
        humidity = current.get("relative_humidity_2m", "?")
        wind = current.get("wind_speed_10m", "?")
        code = current.get("weather_code", 0)
        emoji, desc = self.WEATHER_CODES.get(code, ("🌡️", "Unknown"))

        daily = weather.get("daily", {})
        dates = daily.get("time", [])
        max_temps = daily.get("temperature_2m_max", [])
        min_temps = daily.get("temperature_2m_min", [])
        codes = daily.get("weather_code", [])

        forecast_lines = []
        for i, date in enumerate(dates[:3]):
            day_emoji, _ = self.WEATHER_CODES.get(codes[i] if i < len(codes) else 0, ("🌡️", ""))
            max_t = max_temps[i] if i < len(max_temps) else "?"
            min_t = min_temps[i] if i < len(min_temps) else "?"
            forecast_lines.append(f"{date[5:]} {day_emoji} {min_t}° / {max_t}°")

        forecast_text = "\n".join(forecast_lines)

        return (
            f"{emoji} <b>{location['name']} Weather</b>\n\n"
            f"<b>Current</b>\n"
            f"• Weather: {desc}\n"
            f"• Temp: {temp}°C\n"
            f"• Humidity: {humidity}%\n"
            f"• Wind: {wind} km/h\n\n"
            f"<b>3-Day Forecast</b>\n"
            f"<code>{forecast_text}</code>"
        )

    def _load_location(self, chat_id: int) -> Optional[dict]:
        loc = self.store.get(chat_id)
        if loc:
            return {
                "name": loc.name,
                "country": loc.country,
                "lat": loc.lat,
                "lon": loc.lon,
            }
        return None

    def _save_location(self, chat_id: int, location: dict) -> None:
        self.store.set(
            chat_id=chat_id,
            name=location.get("name", "Unknown"),
            lat=location.get("lat", 0.0),
            lon=location.get("lon", 0.0),
            country=location.get("country"),
        )

    async def _geocode(self, query: str) -> Optional[dict]:
        try:
            search_query = self.KOREAN_TO_ENGLISH.get(query, query)

            async with httpx.AsyncClient(timeout=10.0) as client:
                params = {"name": search_query, "count": 1, "language": "ko"}
                resp = await network_guard.run_async(
                    "weather",
                    client.get,
                    self.GEOCODING_URL,
                    params=params,
                )
                if resp.status_code != 200:
                    return None
                data = resp.json()
                results = data.get("results", [])
                if not results:
                    return None
                r = results[0]
                return {
                    "name": query,
                    "country": r.get("country", ""),
                    "lat": r["latitude"],
                    "lon": r["longitude"],
                }
        except NetworkUnavailable:
            raise
        except Exception:
            return None

    async def _fetch_weather(self, lat: float, lon: float) -> Optional[dict]:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                params = {
                    "latitude": lat,
                    "longitude": lon,
                    "current": "temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m",
                    "daily": "temperature_2m_max,temperature_2m_min,weather_code",
                    "timezone": "Asia/Seoul",
                    "forecast_days": 3,
                }
                resp = await network_guard.run_async(
                    "weather",
                    client.get,
                    self.WEATHER_URL,
                    params=params,
                )
                if resp.status_code != 200:
                    return None
                return resp.json()
        except NetworkUnavailable:
            raise
        except Exception:
            return None

    async def _set_location(self, chat_id: int, location_name: str) -> PluginResult:
        try:
            geo = await self._geocode(location_name)
        except NetworkUnavailable as exc:
            return PluginResult(
                handled=True,
                response=f"❌ Weather network unavailable.\n\n<code>{exc}</code>",
            )
        if not geo:
            return PluginResult(
                handled=True,
                response=f"❌ '{location_name}' not found.\nTry a different location."
            )

        self._save_location(chat_id, geo)

        keyboard = [[
            InlineKeyboardButton("🌤️ Check weather", callback_data="weather:refresh"),
        ]]

        return PluginResult(
            handled=True,
            response=(
                f"📍 Location set!\n\n"
                f"<b>{geo['name']}</b> ({geo['country']})\n"
                f"Lat: {geo['lat']:.4f}, Lon: {geo['lon']:.4f}"
            ),
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    async def _get_weather(self, chat_id: int) -> PluginResult:
        location = self._load_location(chat_id)

        if not location:
            # No saved location -> show province/metro select
            result = self._handle_province_select()
            return PluginResult(
                handled=True,
                response=result["text"],
                reply_markup=result.get("reply_markup"),
            )

        try:
            weather = await self._fetch_weather(location["lat"], location["lon"])
        except NetworkUnavailable as exc:
            return PluginResult(
                handled=True,
                response=f"❌ Weather network unavailable.\n\n<code>{exc}</code>",
            )
        if not weather:
            return PluginResult(
                handled=True,
                response="❌ Unable to fetch weather data. Please try again later."
            )

        text = self._format_weather(location, weather)

        buttons = [
            [
                InlineKeyboardButton("🔄 Refresh", callback_data="weather:refresh"),
                InlineKeyboardButton("📍 Other city", callback_data="weather:select"),
            ]
        ]

        return PluginResult(
            handled=True,
            response=text,
            reply_markup=InlineKeyboardMarkup(buttons)
        )
