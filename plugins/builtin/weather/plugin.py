"""날씨 플러그인 - 버튼 기반 날씨 조회 (Open-Meteo API)."""

import json
import re
from pathlib import Path
from typing import Optional

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from src.plugins.loader import Plugin, PluginResult


class WeatherPlugin(Plugin):
    """버튼 기반 날씨 조회 플러그인 (Open-Meteo API)."""

    name = "weather"
    description = "날씨 조회 및 위치 설정"
    usage = (
        "🌤️ <b>날씨 플러그인 사용법</b>\n\n"
        "<b>날씨 조회</b>\n"
        "• <code>날씨</code> - 버튼으로 도시 선택\n"
        "• <code>서울 날씨</code> - 특정 도시 날씨\n\n"
        "<b>위치 설정</b>\n"
        "• <code>위치 설정: 서울</code>"
    )

    # callback_data 접두사
    CALLBACK_PREFIX = "weather:"

    # Open-Meteo API (무료, 키 불필요)
    GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
    WEATHER_URL = "https://api.open-meteo.com/v1/forecast"

    # 빠른 선택용 주요 도시
    QUICK_CITIES = ["서울", "부산", "대구", "인천", "광주", "대전", "제주"]

    # 한글 → 영문 도시명 매핑 (Open-Meteo는 한글 검색 미지원)
    KOREAN_TO_ENGLISH = {
        "서울": "Seoul",
        "부산": "Busan",
        "인천": "Incheon",
        "대구": "Daegu",
        "대전": "Daejeon",
        "광주": "Gwangju",
        "울산": "Ulsan",
        "수원": "Suwon",
        "성남": "Seongnam",
        "고양": "Goyang",
        "용인": "Yongin",
        "창원": "Changwon",
        "청주": "Cheongju",
        "전주": "Jeonju",
        "천안": "Cheonan",
        "제주": "Jeju",
        "세종": "Sejong",
        "포항": "Pohang",
        "김해": "Gimhae",
        "평택": "Pyeongtaek",
        "안산": "Ansan",
        "안양": "Anyang",
        "파주": "Paju",
        "의정부": "Uijeongbu",
        "김포": "Gimpo",
        "화성": "Hwaseong",
        "시흥": "Siheung",
        "구미": "Gumi",
        "양산": "Yangsan",
        "춘천": "Chuncheon",
        "원주": "Wonju",
        "강릉": "Gangneung",
        "속초": "Sokcho",
        "목포": "Mokpo",
        "여수": "Yeosu",
        "순천": "Suncheon",
        "군산": "Gunsan",
        "익산": "Iksan",
        "경주": "Gyeongju",
        "거제": "Geoje",
        "통영": "Tongyeong",
        "진주": "Jinju",
        "안동": "Andong",
        "충주": "Chungju",
        "제천": "Jecheon",
        "논산": "Nonsan",
        "공주": "Gongju",
        "서산": "Seosan",
        "당진": "Dangjin",
        "아산": "Asan",
        "보령": "Boryeong",
        "나주": "Naju",
        "광양": "Gwangyang",
        "정읍": "Jeongeup",
        "남원": "Namwon",
        "김천": "Gimcheon",
        "상주": "Sangju",
        "영주": "Yeongju",
        "문경": "Mungyeong",
        "영천": "Yeongcheon",
        "밀양": "Miryang",
        "사천": "Sacheon",
        # 해외 주요 도시
        "도쿄": "Tokyo",
        "오사카": "Osaka",
        "교토": "Kyoto",
        "후쿠오카": "Fukuoka",
        "삿포로": "Sapporo",
        "베이징": "Beijing",
        "상하이": "Shanghai",
        "홍콩": "Hong Kong",
        "타이베이": "Taipei",
        "방콕": "Bangkok",
        "싱가포르": "Singapore",
        "뉴욕": "New York",
        "로스앤젤레스": "Los Angeles",
        "샌프란시스코": "San Francisco",
        "시애틀": "Seattle",
        "런던": "London",
        "파리": "Paris",
        "베를린": "Berlin",
        "로마": "Rome",
        "시드니": "Sydney",
        "멜버른": "Melbourne",
    }

    # 트리거 패턴
    WEATHER_PATTERNS = [
        r"날씨",
        r"기온",
        r"weather",
    ]
    CITY_WEATHER_PATTERNS = [
        r"(.+)\s*날씨",  # "서울 날씨"
    ]
    SET_LOCATION_PATTERNS = [
        r"위치\s*설정\s*[:\-]?\s*(.+)",
        r"날씨\s*위치\s*[:\-]?\s*(.+)",
        r"(.+)\s*날씨\s*설정",
    ]
    # 제외 패턴 - AI에게 넘김
    EXCLUDE_PATTERNS = [
        r"(란|이란|가|이)\s*(뭐|무엇|뭔)",  # "날씨란 뭐야"
        r"영어로|번역|translate",            # 번역 요청
        r"어떻게|왜|원리",                   # 질문
        r"알려줘|설명",                      # 설명 요청
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
        msg = message.strip()

        # 제외 패턴 먼저 체크 - AI에게 넘김
        for pattern in self.EXCLUDE_PATTERNS:
            if re.search(pattern, msg, re.IGNORECASE):
                return False

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

        # "서울 날씨" 형태 확인
        for pattern in self.CITY_WEATHER_PATTERNS:
            match = re.search(pattern, msg, re.IGNORECASE)
            if match:
                city = match.group(1).strip()
                if city and city in self.KOREAN_TO_ENGLISH:
                    return await self._get_city_weather(chat_id, city)

        # 저장된 위치 또는 도시 선택 버튼 표시
        return await self._get_weather(chat_id)

    # ==================== Callback 처리 ====================

    def handle_callback(self, callback_data: str, chat_id: int) -> dict:
        """callback_data 처리."""
        import asyncio

        # weather:xxx 형식 파싱
        parts = callback_data.split(":")
        if len(parts) < 2:
            return {"text": "❌ 잘못된 요청", "edit": True}

        action = parts[1]

        if action == "city":
            # weather:city:서울
            city = parts[2] if len(parts) > 2 else "서울"
            # 비동기 함수 실행
            loop = asyncio.get_event_loop()
            return loop.run_until_complete(self._handle_city_weather(chat_id, city))
        elif action == "refresh":
            loop = asyncio.get_event_loop()
            return loop.run_until_complete(self._handle_refresh(chat_id))
        elif action == "select":
            return self._handle_city_select(chat_id)
        else:
            return {"text": "❌ 알 수 없는 명령", "edit": True}

    async def handle_callback_async(self, callback_data: str, chat_id: int) -> dict:
        """비동기 callback_data 처리."""
        parts = callback_data.split(":")
        if len(parts) < 2:
            return {"text": "❌ 잘못된 요청", "edit": True}

        action = parts[1]

        if action == "city":
            city = parts[2] if len(parts) > 2 else "서울"
            return await self._handle_city_weather(chat_id, city)
        elif action == "refresh":
            return await self._handle_refresh(chat_id)
        elif action == "select":
            return self._handle_city_select(chat_id)
        else:
            return {"text": "❌ 알 수 없는 명령", "edit": True}

    def _handle_city_select(self, chat_id: int) -> dict:
        """도시 선택 화면."""
        buttons = []

        # 주요 도시 버튼 (2열)
        row = []
        for city in self.QUICK_CITIES:
            row.append(InlineKeyboardButton(city, callback_data=f"weather:city:{city}"))
            if len(row) == 2:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)

        return {
            "text": "🌤️ <b>도시 선택</b>\n\n날씨를 확인할 도시를 선택하세요:",
            "reply_markup": InlineKeyboardMarkup(buttons),
            "edit": True,
        }

    async def _handle_city_weather(self, chat_id: int, city: str) -> dict:
        """특정 도시 날씨 조회."""
        geo = await self._geocode(city)
        if not geo:
            return {"text": f"❌ '{city}'을(를) 찾을 수 없습니다.", "edit": True}

        weather = await self._fetch_weather(geo["lat"], geo["lon"])
        if not weather:
            return {"text": "❌ 날씨 정보를 가져올 수 없습니다.", "edit": True}

        text = self._format_weather(geo, weather)

        buttons = [
            [InlineKeyboardButton(c, callback_data=f"weather:city:{c}") for c in self.QUICK_CITIES[:4]],
            [InlineKeyboardButton(c, callback_data=f"weather:city:{c}") for c in self.QUICK_CITIES[4:]],
            [
                InlineKeyboardButton("🔄 새로고침", callback_data=f"weather:city:{city}"),
                InlineKeyboardButton("📍 다른 도시", callback_data="weather:select"),
            ]
        ]

        return {
            "text": text,
            "reply_markup": InlineKeyboardMarkup(buttons),
            "edit": True,
        }

    async def _handle_refresh(self, chat_id: int) -> dict:
        """현재 위치 날씨 새로고침."""
        location = self._load_location(chat_id)
        if not location:
            return self._handle_city_select(chat_id)

        weather = await self._fetch_weather(location["lat"], location["lon"])
        if not weather:
            return {"text": "❌ 날씨 정보를 가져올 수 없습니다.", "edit": True}

        text = self._format_weather(location, weather)

        buttons = [
            [InlineKeyboardButton(c, callback_data=f"weather:city:{c}") for c in self.QUICK_CITIES[:4]],
            [InlineKeyboardButton(c, callback_data=f"weather:city:{c}") for c in self.QUICK_CITIES[4:]],
            [
                InlineKeyboardButton("🔄 새로고침", callback_data="weather:refresh"),
                InlineKeyboardButton("📍 다른 도시", callback_data="weather:select"),
            ]
        ]

        return {
            "text": text,
            "reply_markup": InlineKeyboardMarkup(buttons),
            "edit": True,
        }

    def _format_weather(self, location: dict, weather: dict) -> str:
        """날씨 데이터 포맷팅."""
        current = weather.get("current", {})
        temp = current.get("temperature_2m", "?")
        humidity = current.get("relative_humidity_2m", "?")
        wind = current.get("wind_speed_10m", "?")
        code = current.get("weather_code", 0)
        emoji, desc = self.WEATHER_CODES.get(code, ("🌡️", "알 수 없음"))

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
            f"{emoji} <b>{location['name']} 날씨</b>\n\n"
            f"<b>현재</b>\n"
            f"• 날씨: {desc}\n"
            f"• 기온: {temp}°C\n"
            f"• 습도: {humidity}%\n"
            f"• 풍속: {wind} km/h\n\n"
            f"<b>3일 예보</b>\n"
            f"<code>{forecast_text}</code>"
        )

    # ==================== 기존 메서드 ====================

    def _get_location_file(self, chat_id: int) -> Path:
        """위치 설정 파일 경로 (레거시 지원)."""
        data_dir = self.get_data_dir(self._base_dir)
        return data_dir / f"{chat_id}.json"

    def _load_location(self, chat_id: int) -> Optional[dict]:
        """저장된 위치 로드 - Repository 우선."""
        # Repository 사용 가능하면 사용
        if self.repository:
            loc = self.repository.get_weather_location(chat_id)
            if loc:
                return {
                    "name": loc.name,
                    "country": loc.country,
                    "lat": loc.lat,
                    "lon": loc.lon,
                }
            return None

        # 레거시 JSON 폴백
        file_path = self._get_location_file(chat_id)
        if not file_path.exists():
            return None
        try:
            return json.loads(file_path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _save_location(self, chat_id: int, location: dict) -> None:
        """위치 저장 - Repository 우선."""
        # Repository 사용 가능하면 사용
        if self.repository:
            self.repository.set_weather_location(
                chat_id=chat_id,
                name=location.get("name", "Unknown"),
                lat=location.get("lat", 0.0),
                lon=location.get("lon", 0.0),
                country=location.get("country"),
            )
            return

        # 레거시 JSON 폴백
        file_path = self._get_location_file(chat_id)
        file_path.write_text(
            json.dumps(location, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

    async def _geocode(self, query: str) -> Optional[dict]:
        """지명 → 좌표 변환."""
        try:
            search_query = self.KOREAN_TO_ENGLISH.get(query, query)

            async with httpx.AsyncClient(timeout=10.0) as client:
                params = {"name": search_query, "count": 1, "language": "ko"}
                resp = await client.get(self.GEOCODING_URL, params=params)
                if resp.status_code != 200:
                    return None
                data = resp.json()
                results = data.get("results", [])
                if not results:
                    return None
                r = results[0]
                return {
                    "name": query,  # 원래 한글 이름 유지
                    "country": r.get("country", ""),
                    "lat": r["latitude"],
                    "lon": r["longitude"],
                }
        except Exception:
            return None

    async def _fetch_weather(self, lat: float, lon: float) -> Optional[dict]:
        """날씨 데이터 조회."""
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
                resp = await client.get(self.WEATHER_URL, params=params)
                if resp.status_code != 200:
                    return None
                return resp.json()
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

        keyboard = [[
            InlineKeyboardButton("🌤️ 날씨 보기", callback_data="weather:refresh"),
        ]]

        return PluginResult(
            handled=True,
            response=(
                f"📍 위치 설정 완료!\n\n"
                f"<b>{geo['name']}</b> ({geo['country']})\n"
                f"위도: {geo['lat']:.4f}, 경도: {geo['lon']:.4f}"
            ),
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    async def _get_city_weather(self, chat_id: int, city: str) -> PluginResult:
        """특정 도시 날씨 조회 (PluginResult 반환)."""
        geo = await self._geocode(city)
        if not geo:
            return PluginResult(
                handled=True,
                response=f"❌ '{city}'을(를) 찾을 수 없습니다."
            )

        weather = await self._fetch_weather(geo["lat"], geo["lon"])
        if not weather:
            return PluginResult(
                handled=True,
                response="❌ 날씨 정보를 가져올 수 없습니다."
            )

        text = self._format_weather(geo, weather)

        buttons = [
            [InlineKeyboardButton(c, callback_data=f"weather:city:{c}") for c in self.QUICK_CITIES[:4]],
            [InlineKeyboardButton(c, callback_data=f"weather:city:{c}") for c in self.QUICK_CITIES[4:]],
            [
                InlineKeyboardButton("🔄 새로고침", callback_data=f"weather:city:{city}"),
            ]
        ]

        return PluginResult(
            handled=True,
            response=text,
            reply_markup=InlineKeyboardMarkup(buttons)
        )

    async def _get_weather(self, chat_id: int) -> PluginResult:
        """날씨 조회 - 저장된 위치 또는 도시 선택."""
        location = self._load_location(chat_id)

        if not location:
            # 위치 없으면 도시 선택 버튼 표시
            buttons = []
            row = []
            for city in self.QUICK_CITIES:
                row.append(InlineKeyboardButton(city, callback_data=f"weather:city:{city}"))
                if len(row) == 2:
                    buttons.append(row)
                    row = []
            if row:
                buttons.append(row)

            return PluginResult(
                handled=True,
                response="🌤️ <b>날씨 조회</b>\n\n도시를 선택하세요:",
                reply_markup=InlineKeyboardMarkup(buttons)
            )

        # 저장된 위치로 날씨 조회
        weather = await self._fetch_weather(location["lat"], location["lon"])
        if not weather:
            return PluginResult(
                handled=True,
                response="❌ 날씨 정보를 가져올 수 없습니다. 잠시 후 다시 시도해주세요."
            )

        text = self._format_weather(location, weather)

        buttons = [
            [InlineKeyboardButton(c, callback_data=f"weather:city:{c}") for c in self.QUICK_CITIES[:4]],
            [InlineKeyboardButton(c, callback_data=f"weather:city:{c}") for c in self.QUICK_CITIES[4:]],
            [
                InlineKeyboardButton("🔄 새로고침", callback_data="weather:refresh"),
                InlineKeyboardButton("📍 다른 도시", callback_data="weather:select"),
            ]
        ]

        return PluginResult(
            handled=True,
            response=text,
            reply_markup=InlineKeyboardMarkup(buttons)
        )
