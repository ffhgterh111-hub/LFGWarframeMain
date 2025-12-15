#[file name]: main.py
#[file content begin]
import discord
from discord.ext import commands, tasks
import json
import time
import threading
import re
import asyncio
import copy
import os
from typing import Dict, Any, List, Optional, Tuple
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from cachetools import TTLCache
import aiohttp

# ИМПОРТ PLAYWRIGHT
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup, Tag

# Импорт health сервера
from health_check import health_server

# Загрузка переменных окружения
from dotenv import load_dotenv
load_dotenv()

# =================================================================
# 1. КОНСТАНТЫ И НАСТРОЙКИ
# =================================================================

BOT_TOKEN = os.getenv('BOT_TOKEN')
RENDER_URL = os.getenv('RENDER_URL', '')

# Проверка токена
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не установлен. Проверьте переменные окружения.")

# URL-ы для скрапинга
ARBY_URL = 'https://browse.wf/arbys#days=30&tz=utc&hourfmt=24'
FISSURE_URL = 'https://browse.wf/live'

CONFIG_FILE = 'config.json'
SCRAPE_INTERVAL_SECONDS = 5  # Увеличили интервал для уменьшения нагрузки
MISSION_UPDATE_INTERVAL_SECONDS = 15  # Увеличили интервал обновления
MAX_FIELD_LENGTH = 1000

# --- КЭШИРОВАНИЕ ---
# Кэш для арбитражей (5 минут)
ARBITRATION_CACHE = TTLCache(maxsize=10, ttl=300)
# Кэш для разрывов (2 минуты)
FISSURE_CACHE = TTLCache(maxsize=10, ttl=120)
# Кэш для тиров (30 минут)
TIER_CACHE = TTLCache(maxsize=5, ttl=1800)

# --- ГЛОБАЛЬНОЕ СОСТОЯНИЕ ---
CURRENT_MISSION_STATE = {
    "ArbitrationSchedule": {},
    "Fissures": [],
    "SteelPathFissures": []
}
PREVIOUS_MISSION_STATE = {
    "ArbitrationSchedule": {},
    "Fissures": [],
    "SteelPathFissures": []
}
LAST_SCRAPE_TIME = 0
CONFIG: Dict[str, Any] = {}

# Статистика и мониторинг
SCRAPE_STATS = {
    "total_scrapes": 0,
    "successful_scrapes": 0,
    "failed_scrapes": 0,
    "last_error": None,
    "last_error_time": None,
    "fissures_errors": 0,
    "arbitration_errors": 0,
    "start_time": time.time(),
    "cache_hits": 0,
    "cache_misses": 0
}

# Потокобезопасность для изменений
CHANGES_LOCK = threading.Lock()
LAST_CHANGES = {
    "ArbitrationSchedule": False,
    "Fissures": False,
    "SteelPathFissures": False
}

# --- КОНСТАНТЫ ЦВЕТОВ ТИРОВ (АРБИТРАЖ) ---
TIER_COLORS = {
    "S": 0x228BE6,   # Синий
    "A": 0x40C057,   # Зеленый
    "B": 0xFFEE58,   # Желтый
    "C": 0xFAB005,   # Оранжевый
    "D": 0xF57F17,   # Темно-оранжевый
    "F": 0xFA5252    # Красный
}
FALLBACK_COLOR = 0xAAAAAA

# --- ЦВЕТА ДЛЯ ТИКЕТОВ ---
TICKET_COLORS = {
    "арбитраж": 0xFFA500,
    "разрыв": 0x00CCFF,
    "стальной путь": 0x00CCFF,
    "каскад": 0x00FF00
}

# --- СТИЛИЗАЦИЯ И ЭМОДЗИ ---
EMOJI_NAMES = {
    # Фракции
    "Гринир": "gren", "Корпус": "corp", "Зараженные": "infest",
    "Орокин": "orokin", "Шёпот": "murmur",
    # Тиры Арбитража
    "S": "S_", "A": "A_", "B": "B_", "C": "C_", "D": "D_", "F": "F_",
    # Реликвии (Разрывы)
    "Lith": "Lith", "Meso": "Meso", "Neo": "Neo", "Axi": "Axi",
    "Requiem": "Requiem", "Omnia": "Omnia", "SteelPath": "SP",
    # Специальные
    "ВИТУС": "Vitus", "КУВА": "Kuva"
}
RESOLVED_EMOJIS: Dict[str, str] = {}
FACTION_EMOJIS_FINAL: Dict[str, str] = {}
TIER_EMOJIS_FINAL: Dict[str, str] = {}
RELIC_EMOJIS_FINAL: Dict[str, str] = {}
FALLBACK_EMOJI = "❓"

# Ключи для удобства
KUVA_EMOJI_KEY = "КУВА"
VITUS_EMOJI_KEY = "ВИТУС"
SP_EMOJI_KEY = "SteelPath"

# --- КОНСТАНТЫ ФРАКЦИОННЫХ ИЗОБРАЖЕНИЙ ---
FACTION_IMAGE_URLS = {
    "Зараженные": "https://images-ext-1.discordapp.net/external/9_z1utcRwJxSSw4n6ebRLAzqynWnAJAVJDphsjyrg9E/https/assets.empx.cc/Lotus/Interface/Graphics/WorldStatePanel/Infested.png?format=webp&quality=lossless",
    "Гринир": "https://images-ext-1.discordapp.net/external/Wmh0isPGDXG8s1_xJKjSW_F6CHl6aBQXoRIINUdvm0g/https/assets.empx.cc/Lotus/Interface/Graphics/WorldStatePanel/Grineer.png?format=webp&quality=lossless",
    "Корпус": "https://images-ext-1.discordapp.net/external/BUNqoLvclDjqa3OUzE04XI4E1nXvU8qR9f_IIb5AP7o/https/assets.empx.cc/Lotus/Interface/Graphics/WorldStatePanel/Corpus.png?format=webp&quality=lossless",
    "Орокин": "https://media.discordapp.net/attachments/1440089285159161917/1449555462470107318/ca9d48e6-10f7-4a7f-baea-eacad1462ab5.png?ex=693f5339&is=693e01b9&hm=a73a715cd5354a5c197949e5a4d0013d2c4ab9c1fccd07f26104ce5c7e9a154b&=&format=webp&quality=lossless&width=968&height=968",
    "Шёпот": "https://i.imgur.com/gK2oQ9Z.png"
}

# --- БАЗА ДАННЫХ КАРТ АРБИТРАЖА (ПРАВИЛЬНЫЕ ТИРЫ И ФРАКЦИИ) ---
ARBITRATION_MAP_DATABASE = {
    # S-ТИР карты
    "Casta": {"faction": "Гринир", "tier": "S", "mission": "Оборона", "tileset": "Grineer Asteroid"},
    "Cinxia": {"faction": "Гринир", "tier": "S", "mission": "Перехват", "tileset": "Grineer Galleon"},
    "Seimeni": {"faction": "Зараженные", "tier": "S", "mission": "Оборона", "tileset": "Infested Ship"},

    # A-ТИР карты
    "Odin": {"faction": "Гринир", "tier": "A", "mission": "Перехват", "tileset": "Grineer Shipyard"},
    "Sechura": {"faction": "Зараженные", "tier": "A", "mission": "Оборона", "tileset": "Infested Ship"},
    "Hydron": {"faction": "Гринир", "tier": "A", "mission": "Оборона", "tileset": "Grineer Galleon"},
    "Helene": {"faction": "Гринир", "tier": "A", "mission": "Оборона", "tileset": "Grineer Asteroid"},

    # B-ТИР карты
    "Tessara": {"faction": "Корпус", "tier": "B", "mission": "Оборона", "tileset": "Corpus Gas City"},
    "Ose": {"faction": "Корпус", "tier": "B", "mission": "Перехват", "tileset": "Corpus Ice Planet"},
    "Hyf": {"faction": "Зараженные", "tier": "B", "mission": "Оборона", "tileset": "Infested Ship"},
    "Outer Terminus": {"faction": "Корпус", "tier": "B", "mission": "Оборона", "tileset": "Corpus Gas City"},
    "Lazarc": {"faction": "Гринир", "tier": "B", "mission": "Оборона", "tileset": "Grineer Galleon"},
    "Alator": {"faction": "Гринир", "tier": "B", "mission": "Захват", "tileset": "Grineer Shipyard"},
    "Lares": {"faction": "Корпус", "tier": "B", "mission": "Оборона", "tileset": "Corpus Gas City"},
    "Bellinus": {"faction": "Зараженные", "tier": "B", "mission": "Оборона", "tileset": "Infested Ship"},
    "Sinai": {"faction": "Гринир", "tier": "B", "mission": "Оборона", "tileset": "Grineer Galleon"},
    "Stephano": {"faction": "Гринир", "tier": "B", "mission": "Оборона", "tileset": "Grineer Asteroid"},
    "Lith": {"faction": "Гринир", "tier": "B", "mission": "Оборона", "tileset": "Grineer Asteroid"},
    "Cerberus": {"faction": "Корпус", "tier": "B", "mission": "Оборона", "tileset": "Corpus Gas City"},

    # C-ТИР карты
    "Sangeru": {"faction": "Корпус", "tier": "C", "mission": "Оборона", "tileset": "Corpus Ice Planet"},
    "lo": {"faction": "Гринир", "tier": "C", "mission": "Оборона", "tileset": "Grineer Galleon"},
    "Paimon": {"faction": "Корпус", "tier": "C", "mission": "Оборона", "tileset": "Corpus Gas City"},
    "Spear": {"faction": "Гринир", "tier": "C", "mission": "Оборона", "tileset": "Grineer Shipyard"},
    "Gulliver": {"faction": "Корпус", "tier": "C", "mission": "Оборона", "tileset": "Corpus Gas City"},
    "Kala-azar": {"faction": "Зараженные", "tier": "C", "mission": "Оборона", "tileset": "Infested Ship"},
    "Callisto": {"faction": "Гринир", "tier": "C", "mission": "Оборона", "tileset": "Grineer Galleon"},
    "Umbriel": {"faction": "Корпус", "tier": "C", "mission": "Оборона", "tileset": "Corpus Gas City"},
    "Coba": {"faction": "Гринир", "tier": "C", "mission": "Оборона", "tileset": "Grineer Shipyard"},
    "Berehynia": {"faction": "Гринир", "tier": "C", "mission": "Оборона", "tileset": "Grineer Shipyard"},
    "Cytherean": {"faction": "Корпус", "tier": "C", "mission": "Оборона", "tileset": "Corpus Gas City"},
    "Gaia": {"faction": "Корпус", "tier": "C", "mission": "Оборона", "tileset": "Corpus Ice Planet"},
}

# --- ПОЛНАЯ РУСИФИКАЦИЯ ТИПОВ МИССИЙ ---
MISSION_TYPE_TRANSLATIONS = {
    "Exterminate": "Зачистка", "Capture": "Захват", "Mobile Defense": "Мобильная оборона",
    "Defense": "Оборона", "Survival": "Выживание", "Interception": "Перехват",
    "Rescue": "Спасение", "Spy": "Шпионаж", "Sabotage": "Диверсия",
    "Extraction": "Извлечение", "Disruption": "Сбой", "Assault": "Штурм",
    "Crossfire": "Перестрелка", "Alchemy": "Алхимия", "Void Cascade": "Каскад Бездны",
    "Void Flood": "Потоп Бездны", "MD": "Мобильная оборона", "Def": "Оборона",
    "Excavation": "Раскопки", "Conjunction Survival": "Сопряжённое выживание",
    "Defection": "Перебежчики", "Skirmish": "Схватка",
    "Unknown Mission": "Неизвестный тип"
}

# =================================================================
# 2. УТИЛИТЫ И КОНФИГУРАЦИЯ
# =================================================================

def parse_time_to_seconds(time_str: str) -> int:
    """Преобразует строку времени ("1h 30m 5s") в секунды."""
    if time_str in ('N/A', 'Loading...', ''): return 0
    total_seconds = 0
    h_match = re.search(r'(\d+)h', time_str)
    m_match = re.search(r'(\d+)m', time_str)
    s_match = re.search(r'(\d+)s', time_str)
    if h_match: total_seconds += int(h_match.group(1)) * 3600
    if m_match: total_seconds += int(m_match.group(1)) * 60
    if s_match: total_seconds += int(s_match.group(1))
    return total_seconds

def format_seconds_to_time_left(total_seconds: float) -> str:
    """Преобразует секунды в формат '1ч 30м 05с'."""
    if total_seconds <= 0: return "**ИСТЕКЛО**"

    total_seconds = max(0, total_seconds)
    seconds_full = int(total_seconds)
    hours, remainder = divmod(seconds_full, 3600)
    minutes, seconds = divmod(remainder, 60)

    parts = []
    if hours > 0: parts.append(f"{hours}ч")
    if minutes > 0 or hours > 0: parts.append(f"{minutes:02}м")
    parts.append(f"{seconds:02}с")

    return " ".join(parts)

def save_config():
    """Сохраняет настройки в файл JSON."""
    with open(CONFIG_FILE, 'w') as f:
        json.dump(CONFIG, f, indent=4)

def load_config():
    """Загружает настройки из файла JSON и гарантирует наличие всех ключей."""
    DEFAULT_CONFIG = {
        "ARBITRATION_CHANNEL_ID": None,
        'LAST_ARBITRATION_MESSAGE_ID': None,
        'LAST_MENTIONED_NODE': None,
        "FISSURE_CHANNEL_ID": None,
        "STEEL_PATH_CHANNEL_ID": None,
        'LAST_NORMAL_MESSAGE_ID': None,
        'LAST_STEEL_MESSAGE_ID': None,
        "LFG_CHANNEL_ID": None,
        "ARBITRAGE_ROLE_ID": None,
        "CASCAD_ROLE_ID": None,
        "MAP_ROLES": {},
        "LOG_CHANNEL_ID": None,
        "LOG_MESSAGE_ID": None
    }
    global CONFIG
    try:
        with open(CONFIG_FILE, 'r') as f:
            loaded_config = json.load(f)
            CONFIG.update(loaded_config)
    except (FileNotFoundError, json.JSONDecodeError):
        pass

    for key, default_value in DEFAULT_CONFIG.items():
        if key not in CONFIG:
            CONFIG[key] = default_value

    save_config()

def normalize_faction_name(race_name: str, location: str) -> str:
    """Унифицирует имя фракции/тайлсета."""
    norm_location = location.lower()
    norm_race = (race_name or '').lower()

    if 'кува' in norm_race or 'kuva' in norm_race or 'кува' in norm_location or 'kuva' in norm_location:
        return 'Гринир'

    if 'гринир' in norm_race or 'grineer' in norm_race:
        return 'Гринир'

    if 'корпус' in norm_race or 'corpus' in norm_race or 'amalgam' in norm_race or 'амальгама' in norm_race:
        return 'Корпус'

    if 'зараженные' in norm_race or 'infested' in norm_race or 'заражение' in norm_race or 'рой' in norm_race or 'infest' in norm_race or 'пожиратели' in norm_race or 'порождение' in norm_race:
        return 'Зараженные'

    if 'шепот' in norm_race or 'murmur' in norm_race:
        return 'Шёпот'

    if 'бездна' in norm_location or 'void' in norm_location or 'орокин' in norm_race or 'orokin' in norm_race or 'corrupted' in norm_race:
        return 'Орокин'

    return 'Орокин'

def get_faction_image_url(faction_name: str) -> Optional[str]:
    """Возвращает URL изображения фракции."""
    return FACTION_IMAGE_URLS.get(faction_name)

def extract_faction_from_mission_description(description: str) -> Optional[str]:
    """Извлекает название фракции из описания миссии."""
    if not description:
        return None

    # Убираем лишние части описания
    clean_desc = description.split('|')[0].strip()

    # Сначала ищем в скобках
    bracket_patterns = [
        r'\(([^)]*Зараженные[^)]*)\)',
        r'\(([^)]*Гринир[^)]*)\)',
        r'\(([^)]*Корпус[^)]*)\)',
        r'\(([^)]*Орокин[^)]*)\)',
        r'\(([^)]*Шёпот[^)]*)\)',
        r'\(([^)]*Infested[^)]*)\)',
        r'\(([^)]*Grineer[^)]*)\)',
        r'\(([^)]*Corpus[^)]*)\)',
        r'\(([^)]*Corrupted[^)]*)\)',
        r'\(([^)]*Murmur[^)]*)\)'
    ]

    for pattern in bracket_patterns:
        match = re.search(pattern, clean_desc)
        if match:
            faction_text = match.group(1).lower()
            if 'зараженные' in faction_text or 'infested' in faction_text:
                return 'Зараженные'
            elif 'гринир' in faction_text or 'grineer' in faction_text:
                return 'Гринир'
            elif 'корпус' in faction_text or 'corpus' in faction_text:
                return 'Корпус'
            elif 'орокин' in faction_text or 'corrupted' in faction_text:
                return 'Орокин'
            elif 'шепот' in faction_text or 'murmur' in faction_text:
                return 'Шёпот'

    # Ищем в формате "Миссия: Casta (Гринир) - Оборона"
    mission_pattern = r'\(([^)]+)\)\s*-\s*\w+'
    mission_match = re.search(mission_pattern, clean_desc)
    if mission_match:
        faction_candidate = mission_match.group(1).strip()
        faction_candidate_lower = faction_candidate.lower()

        if 'зараженные' in faction_candidate_lower or 'infested' in faction_candidate_lower:
            return 'Зараженные'
        elif 'гринир' in faction_candidate_lower or 'grineer' in faction_candidate_lower:
            return 'Гринир'
        elif 'корпус' in faction_candidate_lower or 'corpus' in faction_candidate_lower:
            return 'Корпус'
        elif 'орокин' in faction_candidate_lower or 'corrupted' in faction_candidate_lower:
            return 'Орокин'
        elif 'шепот' in faction_candidate_lower or 'murmur' in faction_candidate_lower:
            return 'Шёпот'
        else:
            # Если прямо не нашли, но это название фракции из нашего списка
            for faction in ["Гринир", "Корпус", "Зараженные", "Орокин", "Шёпот"]:
                if faction in faction_candidate:
                    return faction

    # Ищем в формате "Зачистка @ Armaros, Europa (Зараженные)"
    location_pattern = r'@[^)]*\(([^)]+)\)'
    location_match = re.search(location_pattern, clean_desc)
    if location_match:
        faction_candidate = location_match.group(1).strip()
        faction_candidate_lower = faction_candidate.lower()

        if 'зараженные' in faction_candidate_lower or 'infested' in faction_candidate_lower:
            return 'Зараженные'
        elif 'гринир' in faction_candidate_lower or 'grineer' in faction_candidate_lower:
            return 'Гринир'
        elif 'корпус' in faction_candidate_lower or 'corpus' in faction_candidate_lower:
            return 'Корпус'
        elif 'орокин' in faction_candidate_lower or 'corrupted' in faction_candidate_lower:
            return 'Орокин'
        elif 'шепот' in faction_candidate_lower or 'murmur' in faction_candidate_lower:
            return 'Шёпот'

    # Ищем просто в тексте без скобок
    lower_desc = clean_desc.lower()
    if 'зараженные' in lower_desc or 'infested' in lower_desc:
        return 'Зараженные'
    elif 'гринир' in lower_desc or 'grineer' in lower_desc:
        return 'Гринир'
    elif 'корпус' in lower_desc or 'corpus' in lower_desc:
        return 'Корпус'
    elif 'орокин' in lower_desc or 'corrupted' in lower_desc:
        return 'Орокин'
    elif 'шепот' in lower_desc or 'murmur' in lower_desc:
        return 'Шёпот'

    return None

async def update_log_message(bot: commands.Bot):
    """Обновляет сообщение с мониторингом в канале логов."""
    log_channel_id = CONFIG.get('LOG_CHANNEL_ID')
    if not log_channel_id:
        return

    log_channel = bot.get_channel(log_channel_id)
    if not log_channel:
        return

    # Расчет времени работы
    uptime_seconds = int(time.time() - SCRAPE_STATS["start_time"])
    hours, remainder = divmod(uptime_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    uptime_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    # Расчет успешности скрапинга
    total = SCRAPE_STATS["total_scrapes"]
    successful = SCRAPE_STATS["successful_scrapes"]
    failed = SCRAPE_STATS["failed_scrapes"]

    if total > 0:
        success_rate = (successful / total) * 100
    else:
        success_rate = 0

    # Определяем цвет статуса
    if success_rate > 90:
        status_color = 0x00FF00  # Зеленый
        status_text = "🟢 ОТЛИЧНО"
    elif success_rate > 70:
        status_color = 0xFFFF00  # Желтый
        status_text = "🟡 НОРМАЛЬНО"
    else:
        status_color = 0xFF0000  # Красный
        status_text = "🔴 ПРОБЛЕМЫ"

    # Определяем статус соединения
    ping = round(bot.latency * 1000)
    if ping < 100:
        connection_status = f"🟢 {ping}ms"
    elif ping < 300:
        connection_status = f"🟡 {ping}ms"
    else:
        connection_status = f"🔴 {ping}ms"

    # Собираем информацию о последней ошибке
    last_error_info = "Нет ошибок"
    if SCRAPE_STATS["last_error"] and SCRAPE_STATS["last_error_time"]:
        error_time = datetime.fromtimestamp(SCRAPE_STATS["last_error_time"]).strftime('%H:%M:%S')
        last_error_info = f"**{error_time}:** {SCRAPE_STATS['last_error'][:100]}..."

    # Текущие данные
    current_arb = CURRENT_MISSION_STATE.get("ArbitrationSchedule", {}).get("Current", {})
    arb_tier = current_arb.get("Tier", "N/A")
    normal_fissures = len(CURRENT_MISSION_STATE.get("Fissures", []))
    sp_fissures = len(CURRENT_MISSION_STATE.get("SteelPathFissures", []))

    # Создаем embed
    embed = discord.Embed(
        title="📊 МОНИТОРИНГ СИСТЕМЫ",
        description="Реальное время работы бота и статистика скрапинга",
        color=status_color,
        timestamp=datetime.now(timezone.utc)
    )

    # Статус системы
    embed.add_field(
        name="🔄 СТАТУС СИСТЕМЫ",
        value=(
            f"**Статус:** {status_text}\n"
            f"**Время работы:** {uptime_str}\n"
            f"**Соединение:** {connection_status}\n"
            f"**Серверов:** {len(bot.guilds)}\n"
            f"**Пользователей:** {len(bot.users)}\n"
            f"**Render URL:** {RENDER_URL if RENDER_URL else 'Не настроен'}"
        ),
        inline=False
    )

    # Статистика скрапинга
    embed.add_field(
        name="📈 СТАТИСТИКА СКРАПИНГА",
        value=(
            f"**Всего скрапов:** {total}\n"
            f"**Успешных:** {successful}\n"
            f"**Неудачных:** {failed}\n"
            f"**Успешность:** {success_rate:.1f}%\n"
            f"**Ошибки разрывов:** {SCRAPE_STATS['fissures_errors']}\n"
            f"**Ошибки арбитража:** {SCRAPE_STATS['arbitration_errors']}\n"
            f"**Cache hits:** {SCRAPE_STATS['cache_hits']}\n"
            f"**Cache misses:** {SCRAPE_STATS['cache_misses']}"
        ),
        inline=True
    )

    # Текущие данные
    embed.add_field(
        name="📊 ТЕКУЩИЕ ДАННЫЕ",
        value=(
            f"**Арбитраж:** {arb_tier}\n"
            f"**Разрывы:** {normal_fissures}\n"
            f"**Разрывы SP:** {sp_fissures}\n"
            f"**Последний скрап:** <t:{int(LAST_SCRAPE_TIME)}:R>\n"
            f"**Интервал:** {SCRAPE_INTERVAL_SECONDS}с"
        ),
        inline=True
    )

    # Настройки каналов
    channels_info = []
    for key, name in [
        ('ARBITRATION_CHANNEL_ID', 'Арбитраж'),
        ('FISSURE_CHANNEL_ID', 'Разрывы'),
        ('STEEL_PATH_CHANNEL_ID', 'Разрывы SP'),
        ('LFG_CHANNEL_ID', 'LFG'),
        ('LOG_CHANNEL_ID', 'Логи')
    ]:
        channel_id = CONFIG.get(key)
        if channel_id:
            channels_info.append(f"✅ **{name}:** <#{channel_id}>")
        else:
            channels_info.append(f"❌ **{name}:** Не настроен")

    embed.add_field(
        name="⚙️ НАСТРОЙКИ КАНАЛОВ",
        value="\n".join(channels_info),
        inline=False
    )

    # Последняя ошибка
    embed.add_field(
        name="⚠️ ПОСЛЕДНЯЯ ОШИБКА",
        value=last_error_info,
        inline=False
    )

    embed.set_footer(text="Обновляется каждые 30 секунд | Warframe LFG Bot")

    # Отправляем или редактируем сообщение
    try:
        message_id = CONFIG.get('LOG_MESSAGE_ID')
        if message_id:
            try:
                message = await log_channel.fetch_message(message_id)
                await message.edit(embed=embed)
                return
            except discord.NotFound:
                pass

        sent_message = await log_channel.send(embed=embed)
        CONFIG['LOG_MESSAGE_ID'] = sent_message.id
        save_config()

    except Exception as e:
        print(f"[{time.strftime('%H:%M:%S')}] ❌ Ошибка обновления сообщения мониторинга: {e}")

def resolve_custom_emojis(bot: commands.Bot):
    """Находит все пользовательские эмодзи."""
    global RESOLVED_EMOJIS, FACTION_EMOJIS_FINAL, TIER_EMOJIS_FINAL, RELIC_EMOJIS_FINAL, FALLBACK_EMOJI

    print("Начало поиска эмодзи...")

    for key_name, emoji_name in EMOJI_NAMES.items():
        custom_emoji = discord.utils.get(bot.emojis, name=emoji_name)
        if custom_emoji:
            RESOLVED_EMOJIS[emoji_name] = str(custom_emoji)
        else:
            # Если эмодзи не найден, используем текстовую замену
            if key_name == "ВИТУС":
                RESOLVED_EMOJIS[emoji_name] = "⭐"  # Звезда вместо витуса
            elif key_name == "КУВА":
                RESOLVED_EMOJIS[emoji_name] = "⚡️"  # Молния вместо кувы
            elif key_name in ["Гринир", "Корпус", "Зараженные", "Орокин", "Шёпот"]:
                RESOLVED_EMOJIS[emoji_name] = "⚔️"  # Скрещенные мечи для фракций
            elif key_name in ["S", "A", "B", "C", "D", "F"]:
                RESOLVED_EMOJIS[emoji_name] = key_name  # Просто буква для тиров
            elif key_name in ["Lith", "Meso", "Neo", "Axi", "Requiem", "Omnia"]:
                RESOLVED_EMOJIS[emoji_name] = f"[{key_name}]"  # В квадратных скобках для реликвий
            else:
                RESOLVED_EMOJIS[emoji_name] = "❓"

    # Настройка эмодзи фракций
    for faction_key in ["Гринир", "Корпус", "Зараженные", "Орокин", "Шёпот"]:
        emoji_name = EMOJI_NAMES.get(faction_key)
        if emoji_name and emoji_name in RESOLVED_EMOJIS:
            FACTION_EMOJIS_FINAL[faction_key] = RESOLVED_EMOJIS[emoji_name]
        else:
            FACTION_EMOJIS_FINAL[faction_key] = "⚔️"

    # Настройка эмодзи тиров
    for tier_key in ["S", "A", "B", "C", "D", "F"]:
        emoji_name = EMOJI_NAMES.get(tier_key)
        if emoji_name and emoji_name in RESOLVED_EMOJIS:
            TIER_EMOJIS_FINAL[tier_key] = RESOLVED_EMOJIS[emoji_name]
        else:
            TIER_EMOJIS_FINAL[tier_key] = tier_key

    # Настройка эмодзи реликвий
    for relic_key in ["Lith", "Meso", "Neo", "Axi", "Requiem", "Omnia"]:
        emoji_name = EMOJI_NAMES.get(relic_key)
        if emoji_name and emoji_name in RESOLVED_EMOJIS:
            RELIC_EMOJIS_FINAL[relic_key] = RESOLVED_EMOJIS[emoji_name]
        else:
            RELIC_EMOJIS_FINAL[relic_key] = f"[{relic_key}]"

    # Устанавливаем дефолтный эмодзи
    FALLBACK_EMOJI = "❓"

    print("Поиск эмодзи завершен.")

# Загружаем конфигурацию
load_config()

# =================================================================
# 3. БЫСТРЫЕ ФУНКЦИИ СРАВНЕНИЯ СОСТОЯНИЙ
# =================================================================

def create_fissure_key(fissure: Dict[str, Any]) -> str:
    """Создает уникальный ключ для разрыва (без времени)."""
    return f"{fissure['Relic']}|{fissure['Type']}|{fissure['Location']}|{fissure['Level']}|{fissure['Race']}"

def create_arbitration_key(arb_data: Dict[str, Any]) -> str:
    """Создает уникальный ключ для арбитража."""
    current = arb_data.get('Current', {})
    if current.get('Node') in ('N/A', '', None):
        return 'N/A'
    return f"{current.get('Node','')}|{current.get('Tier','')}|{current.get('Name','')}|{current.get('Location','')}"

def compare_fissures_fast(old_fissures: List[Dict], new_fissures: List[Dict]) -> bool:
    """Быстрое сравнение разрывов с использованием хеширования."""
    if len(old_fissures) != len(new_fissures):
        return False

    # Создаем множества ключей для быстрого сравнения
    old_keys = set(create_fissure_key(f) for f in old_fissures)
    new_keys = set(create_fissure_key(f) for f in new_fissures)

    return old_keys == new_keys

def compare_arbitration_schedule_fast(old_schedule: Dict, new_schedule: Dict) -> bool:
    """Быстрое сравнение расписания арбитражей."""
    old_current = old_schedule.get('Current', {})
    new_current = new_schedule.get('Current', {})

    # Быстрая проверка на N/A
    old_node = old_current.get('Node', '')
    new_node = new_current.get('Node', '')

    if (old_node in ('N/A', '')) and (new_node in ('N/A', '')):
        return True

    if (old_node in ('N/A', '')) != (new_node in ('N/A', '')):
        return False

    # Сравниваем ключевые поля
    key_fields = ['Tier', 'Name', 'Location', 'Node', 'Tileset', 'Bonus', 'IsActive']
    for field in key_fields:
        if old_current.get(field) != new_current.get(field):
            return False

    # Быстрая проверка upcoming (только количество)
    if len(old_schedule.get('Upcoming', [])) != len(new_schedule.get('Upcoming', [])):
        return False

    return True

def set_current_state(data: Dict[str, Any], scrape_time: float):
    """Обновляет текущее состояние миссий и время скрапинга."""
    global CURRENT_MISSION_STATE, LAST_SCRAPE_TIME, PREVIOUS_MISSION_STATE, LAST_CHANGES, CHANGES_LOCK

    with CHANGES_LOCK:
        changes = {
            "ArbitrationSchedule": False,
            "Fissures": False,
            "SteelPathFissures": False
        }

        # Получаем данные о пустых состояниях
        old_arb = PREVIOUS_MISSION_STATE.get("ArbitrationSchedule", {})
        new_arb = data.get("ArbitrationSchedule", {})
        old_arb_node = old_arb.get('Current', {}).get('Node', '')
        new_arb_node = new_arb.get('Current', {}).get('Node', '')

        old_fissures = PREVIOUS_MISSION_STATE.get("Fissures", [])
        new_fissures = data.get("Fissures", [])

        old_sp_fissures = PREVIOUS_MISSION_STATE.get("SteelPathFissures", [])
        new_sp_fissures = data.get("SteelPathFissures", [])

        # Проверяем изменения только если в новых данных что-то есть
        # Для арбитража
        if new_arb_node != 'N/A' and new_arb_node != '':
            if not compare_arbitration_schedule_fast(old_arb, new_arb):
                changes["ArbitrationSchedule"] = True
        # Если новые данные N/A или пустые, НЕ обновляем (не меняем состояние)

        # Для обычных разрывов
        if len(new_fissures) > 0:
            if not compare_fissures_fast(old_fissures, new_fissures):
                changes["Fissures"] = True
        # Если новых разрывов нет, НЕ обновляем

        # Для разрывов стального пути
        if len(new_sp_fissures) > 0:
            if not compare_fissures_fast(old_sp_fissures, new_sp_fissures):
                changes["SteelPathFissures"] = True
        # Если новых разрывов нет, НЕ обновляем

        # Обновляем предыдущее состояние ТОЛЬКО если новые данные валидны
        # (не N/A и не пустые)
        if new_arb_node != 'N/A' and new_arb_node != '':
            PREVIOUS_MISSION_STATE["ArbitrationSchedule"] = copy.deepcopy(new_arb)

        if len(new_fissures) > 0:
            PREVIOUS_MISSION_STATE["Fissures"] = copy.deepcopy(new_fissures)

        if len(new_sp_fissures) > 0:
            PREVIOUS_MISSION_STATE["SteelPathFissures"] = copy.deepcopy(new_sp_fissures)

        # Фиксируем изменения
        for key in changes:
            if changes[key]:
                LAST_CHANGES[key] = True

        # Обновляем текущее состояние (всегда, чтобы видеть актуальные данные)
        CURRENT_MISSION_STATE.update(data)
        LAST_SCRAPE_TIME = scrape_time

    return changes

# =================================================================
# 4. КЛАССЫ ДЛЯ LFG СИСТЕМЫ (ПОИСК ПАТИ)
# =================================================================

class CommentModal(discord.ui.Modal, title='Добавить комментарий к тикету'):
    """Модальное окно для ввода комментария."""

    comment_input = discord.ui.TextInput(
        label='Ваш комментарий (до 100 символов)',
        style=discord.TextStyle.short,
        placeholder='Например: +Каскад, Нужен хил, 4x60 и т.д.',
        required=False,
        max_length=100,
    )

    def __init__(self, view):
        super().__init__()
        self.view = view

    async def on_submit(self, interaction: discord.Interaction):
        self.view.comment_text = self.comment_input.value

        comment_display = f"✅ **Комментарий добавлен:** *{self.comment_input.value}*" if self.comment_input.value else "Комментарий удален."

        await interaction.response.edit_message(
            content=f"{interaction.message.content}\n\n{comment_display}",
            view=self.view
        )

class LFGTicketView(discord.ui.View):
    """View для управления созданным тикетом LFG."""

    def __init__(self, bot, mission_info: Dict, initiator: discord.Member, slot_names: List[str], message_id: int, comment: Optional[str] = None):
        super().__init__(timeout=3600)
        self.bot = bot
        self.mission_info = mission_info
        self.slots = {slot: "[СВОБОДНО]" for slot in slot_names}
        self.initiator = initiator
        self.slot_names = slot_names
        self.message_id = message_id
        self.comment = comment

        # Назначаем создателя в первый слот
        self.slots[slot_names[0]] = initiator

        self._add_slot_buttons()

    async def on_timeout(self):
        """Удаляет тикет при таймауте."""
        channel_id = CONFIG.get('LFG_CHANNEL_ID')
        if not channel_id:
            return

        channel = self.bot.get_channel(channel_id)
        if not channel:
            return

        try:
            message = await channel.fetch_message(self.message_id)
            await message.delete()
        except:
            pass

    def _add_slot_buttons(self):
        """Добавляет кнопки для занятия слотов."""
        self.clear_items()

        # Кнопки для слотов
        for i, slot_name in enumerate(self.slot_names):
            if self.slots[slot_name] == "[СВОБОДНО]":
                button = discord.ui.Button(
                    label=f"Занять {slot_name}",
                    style=discord.ButtonStyle.secondary,
                    custom_id=f"join_{slot_name}",
                    row=i // 3
                )
                button.callback = self._create_join_callback(slot_name)
                self.add_item(button)

        # Кнопка добавления комментария
        comment_button = discord.ui.Button(
            label="Добавить комментарий" if not self.comment else "Изменить комментарий",
            style=discord.ButtonStyle.primary,
            emoji="📝",
            row=2
        )
        comment_button.callback = self.add_comment_callback
        self.add_item(comment_button)

        # Кнопка закрытия тикета (только для создателя)
        close_button = discord.ui.Button(
            label="Закрыть тикет",
            style=discord.ButtonStyle.danger,
            emoji="❌",
            row=2
        )
        close_button.callback = self.close_ticket_callback
        self.add_item(close_button)

        # Кнопка покинуть слот
        leave_button = discord.ui.Button(
            label="Покинуть слот",
            style=discord.ButtonStyle.blurple,
            emoji="🏃",
            row=2
        )
        leave_button.callback = self.leave_slot_callback
        self.add_item(leave_button)

    def _create_join_callback(self, slot_name: str):
        """Создает callback для кнопки занятия слота."""
        async def join_callback(interaction: discord.Interaction):
            if self.slots[slot_name] != "[СВОБОДНО]":
                await interaction.response.send_message("Этот слот уже занят!", ephemeral=True)
                return

            # Проверяем, не занят ли пользователь уже другой слот
            current_slot = None
            for slot, player in self.slots.items():
                if player == interaction.user:
                    current_slot = slot
                    break

            if current_slot:
                # Перемещаем пользователя
                self.slots[current_slot] = "[СВОБОДНО]"
                self.slots[slot_name] = interaction.user
                await interaction.response.send_message(
                    f"✅ Вы переместились из слота '{current_slot}' в слот '{slot_name}'!",
                    ephemeral=True
                )
            else:
                # Занимаем слот
                self.slots[slot_name] = interaction.user
                await interaction.response.send_message(f"Вы заняли слот {slot_name}!", ephemeral=True)

            self._add_slot_buttons()
            embed = self._create_embed()
            await interaction.message.edit(embed=embed, view=self)

            if all(slot != "[СВОБОДНО]" for slot in self.slots.values()):
                await self._complete_party(interaction)

        return join_callback

    async def _complete_party(self, interaction: discord.Interaction):
        """Завершает сбор пати, удаляет тикет и выводит финальное сообщение."""
        # Создаем финальное сообщение
        mission_type = self.mission_info.get("type", "разрыв")
        mission_name = self.mission_info.get("full_name", "Неизвестная миссия")

        # Определяем фракцию из описания миссии
        faction_name = extract_faction_from_mission_description(mission_name)

        if not faction_name:
            # Если не удалось определить, используем дефолтную
            faction_name = self.mission_info.get("faction", "Орокин")

        faction_emoji = FACTION_EMOJIS_FINAL.get(faction_name, "⚔️")

        embed = discord.Embed(
            title="✅ Пати собрана!",
            color=0x00FF00
        )

        # Добавляем информацию о миссии
        embed.description = f"**Миссия:** {mission_name}\n**Тип:** {mission_type.capitalize()}\n**Фракция:** {faction_emoji} {faction_name}"

        # Добавляем состав группы
        members_info = []
        for slot, player in self.slots.items():
            if player != "[СВОБОДНО]":
                members_info.append(f"**{slot}:** {player.mention}")

        embed.add_field(name="Состав группы:", value="\n".join(members_info), inline=False)

        # Добавляем комментарий если есть
        if self.comment:
            embed.add_field(name="Комментарий:", value=self.comment, inline=False)

        # Добавляем создателя
        embed.add_field(name="Создатель:", value=self.initiator.mention, inline=True)

        # Добавляем время создания
        embed.set_footer(text=f"Собрано: {datetime.now().strftime('%H:%M:%S')}")

        # Добавляем фото фракции (если удалось определить)
        faction_image = get_faction_image_url(faction_name)
        if faction_image:
            embed.set_thumbnail(url=faction_image)

        # Отправляем финальное сообщение в тот же канал
        await interaction.channel.send(embed=embed)

        # Удаляем тикет
        try:
            await interaction.message.delete()
        except:
            pass

        self.stop()

    async def add_comment_callback(self, interaction: discord.Interaction):
        """Обработчик кнопки добавления комментария."""
        modal = CommentModal(self)
        await interaction.response.send_modal(modal)

    async def close_ticket_callback(self, interaction: discord.Interaction):
        """Обработчик кнопки закрытия тикета."""
        if interaction.user.id != self.initiator.id:
            await interaction.response.send_message("Только создатель тикета может его закрыть!", ephemeral=True)
            return

        await interaction.response.send_message("Тикет закрыт!", ephemeral=True)
        await interaction.message.delete()
        self.stop()

    async def leave_slot_callback(self, interaction: discord.Interaction):
        """Обработчик кнопки покидания слота."""
        slot_to_leave = None
        for slot, player in self.slots.items():
            if player == interaction.user:
                slot_to_leave = slot
                break

        if not slot_to_leave:
            await interaction.response.send_message("Вы не заняли ни одного слота!", ephemeral=True)
            return

        if interaction.user.id == self.initiator.id:
            # Если создатель хочет покинуть слот, проверяем не единственный ли он
            occupied_slots = len([p for p in self.slots.values() if p != "[СВОБОДНО]"])
            if occupied_slots == 1:
                await interaction.response.send_message(
                    "Вы создатель тикета и единственный участник. Закройте тикет вместо этого.",
                    ephemeral=True
                )
                return

            # Создатель покидает слот, но тикет остается
            self.slots[slot_to_leave] = "[СВОБОДНО]"
            await interaction.response.send_message(
                f"Вы покинули слот {slot_to_leave}! Тикет остается активным.",
                ephemeral=True
            )
        else:
            # Обычный участник покидает слот
            self.slots[slot_to_leave] = "[СВОБОДНО]"
            await interaction.response.send_message(f"Вы покинули слот {slot_to_leave}!", ephemeral=True)

        self._add_slot_buttons()
        embed = self._create_embed()
        await interaction.message.edit(embed=embed, view=self)

    def _create_embed(self) -> discord.Embed:
        """Создает embed для тикета LFG."""
        mission_type = self.mission_info.get("type", "разрыв")
        mission_full_name = self.mission_info.get('full_name', 'N/A')

        # Определяем фракцию из описания миссии
        faction_name = extract_faction_from_mission_description(mission_full_name)

        # Если не удалось определить из описания, берем из mission_info
        if not faction_name:
            faction_name = self.mission_info.get("faction", "Орокин")

        # Получаем изображение фракции
        faction_image = get_faction_image_url(faction_name)

        # Для арбитража используем цвет тира, для остальных - стандартный
        if mission_type == "арбитраж":
            tier = self.mission_info.get("tier", "N/A").upper()
            # Используем эмодзи тира
            tier_emoji = TIER_EMOJIS_FINAL.get(tier, tier)
            color = TIER_COLORS.get(tier, TICKET_COLORS.get(mission_type, 0x00CCFF))

            # Используем правильные эмодзи для витуса
            vitus_emoji = RESOLVED_EMOJIS.get(EMOJI_NAMES.get(VITUS_EMOJI_KEY), "⭐")

            # Используем правильный формат заголовка
            if self.mission_info.get('map_name'):
                title = f"{vitus_emoji} Поиск пати: ({tier_emoji} Тир) Арбитраж ({self.mission_info['map_name']})"
            else:
                title = f"{vitus_emoji} Поиск пати: ({tier_emoji} Тир) Арбитраж"
        elif mission_type == "каскад":
            # Для каскада используем зеленый цвет и особый заголовок
            color = TICKET_COLORS.get(mission_type, 0x00FF00)
            title = "🌀 Поиск пати: Каскад Бездны"
        else:
            color = TICKET_COLORS.get(mission_type, 0x00CCFF)
            relic_display = self.mission_info.get("relic_display", "")
            relic_type = self.mission_info.get("relic", "")

            # Добавляем SP эмодзи для стального пути
            if mission_type == "стальной путь":
                sp_emoji = RESOLVED_EMOJIS.get(EMOJI_NAMES.get(SP_EMOJI_KEY), "💀")
                title = f"{sp_emoji} Поиск пати: {relic_display} {relic_type} Разрыв Стального Пути"
            else:
                title = f"🎮 Поиск пати: {relic_display} {relic_type} Разрыв"

        # Получаем эмодзи фракции
        faction_emoji = FACTION_EMOJIS_FINAL.get(faction_name, "⚔️")

        # Формируем описание
        description_lines = [
            f"**Создатель:** {self.initiator.mention}",
            f"**Миссия:** {mission_full_name}",
            f"**Фракция:** {faction_emoji} {faction_name}"
        ]

        # Добавляем уровень для разрывов
        if mission_type in ["разрыв", "стальной путь", "каскад"]:
            level = self.mission_info.get("level", "")
            if level:
                description_lines.append(f"**Уровень:** {level}")

        embed = discord.Embed(
            title=title,
            description="\n".join(description_lines),
            color=color
        )

        # ВАЖНО: Добавляем фото фракции в тикет
        if faction_image:
            embed.set_thumbnail(url=faction_image)

        slots_text = []
        for slot_name in self.slot_names:
            player = self.slots[slot_name]
            if player == "[СВОБОДНО]":
                slots_text.append(f"`{slot_name}`: **Свободен**")
            else:
                slots_text.append(f"`{slot_name}`: {player.mention}")

        embed.add_field(name="Слоты (4/4):", value="\n".join(slots_text), inline=False)

        if self.comment:
            embed.add_field(name="📝 Комментарий:", value=self.comment, inline=False)

        embed.set_footer(text=f"Создан: {datetime.now().strftime('%H:%M:%S')} | Автоудаление через 1 час")

        return embed

# =================================================================
# 5. VIEW ДЛЯ ВЫБОРА МИССИЙ В КАНАЛАХ
# =================================================================

class FissureSelectView(discord.ui.View):
    """View для выбора разрыва для создания LFG тикета."""

    def __init__(self, fissures: List[Dict], is_steel_path: bool = False):
        super().__init__(timeout=600)
        self.fissures = fissures
        self.is_steel_path = is_steel_path
        self.selected_fissure = None
        self.comment_text = None

        self.update_fissure_options()

    def update_fissure_options(self):
        """Обновляет опции селектора на основе текущих разрывов."""
        options = []
        for i, fissure in enumerate(self.fissures[:25]):
            relic_type = fissure['Relic']
            # Используем строковое представление эмодзи вместо объекта
            relic_display = RELIC_EMOJIS_FINAL.get(relic_type, f"[{relic_type}]")

            # Для текста метки используем только текстовое представление
            label = f"{relic_type} {fissure['Type']} @ {fissure['Location']}"
            if len(label) > 100:
                label = label[:97] + "..."

            # В description можем использовать эмодзи
            description = f"{fissure['Race']} | Ур. {fissure['Level']}"
            if len(description) > 100:
                description = description[:97] + "..."

            options.append(
                discord.SelectOption(
                    label=label,
                    value=str(i),
                    description=description
                )
            )

        self.clear_items()

        if options:
            self.add_item(FissureSelectDropdown(options, self))

        self.add_item(AddCommentButton(self))
        self.add_item(CreateTicketButton(self))

    @discord.ui.button(label="Обновить список", style=discord.ButtonStyle.secondary, emoji="🔄", row=2)
    async def refresh_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Обновляет список разрывов."""
        await interaction.response.defer(thinking=True, ephemeral=True)

        data = CURRENT_MISSION_STATE
        if self.is_steel_path:
            self.fissures = data.get("SteelPathFissures", [])
        else:
            self.fissures = data.get("Fissures", [])

        self.update_fissure_options()

        embed = interaction.message.embeds[0]
        await interaction.message.edit(embed=embed, view=self)

        await interaction.followup.send("✅ Список разрывов обновлен!", ephemeral=True)

class FissureSelectDropdown(discord.ui.Select):
    """Dropdown для выбора разрыва."""

    def __init__(self, options: List[discord.SelectOption], parent_view: FissureSelectView):
        super().__init__(
            placeholder="Выберите миссию для поиска пати...",
            options=options,
            row=0
        )
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        self.parent_view.selected_fissure = self.parent_view.fissures[int(self.values[0])]

        fissure = self.parent_view.selected_fissure
        relic_type = fissure['Relic']
        relic_display = RELIC_EMOJIS_FINAL.get(relic_type, f"[{relic_type}]")

        # Используем правильный эмодзи в сообщении
        await interaction.response.edit_message(
            content=f"✅ Выбрана миссия: {relic_display} **{fissure['Type']}** @ **{fissure['Location']}**\n\nДобавьте комментарий или создайте тикет:",
            view=self.parent_view
        )

class AddCommentButton(discord.ui.Button):
    """Кнопка для добавления комментария."""

    def __init__(self, parent_view: FissureSelectView):
        super().__init__(
            label="Добавить комментарий",
            style=discord.ButtonStyle.secondary,
            emoji="📝",
            row=1
        )
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        modal = CommentModal(self.parent_view)
        await interaction.response.send_modal(modal)

class CreateTicketButton(discord.ui.Button):
    """Кнопка для создания тикета LFG."""

    def __init__(self, parent_view: FissureSelectView):
        super().__init__(
            label="Создать тикет LFG",
            style=discord.ButtonStyle.success,
            emoji="🎮",
            row=1
        )
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        if not self.parent_view.selected_fissure:
            await interaction.response.send_message("Сначала выберите миссию!", ephemeral=True)
            return

        lfg_channel_id = CONFIG.get('LFG_CHANNEL_ID')
        if not lfg_channel_id:
            await interaction.response.send_message("Канал для поиска пати не настроен! Используйте команду `!set_lfg_channel`.", ephemeral=True)
            return

        lfg_channel = interaction.guild.get_channel(lfg_channel_id)
        if not lfg_channel:
            await interaction.response.send_message("Канал для поиска пати не найден!", ephemeral=True)
            return

        # Закрываем старый тикет пользователя, если он есть
        try:
            # Получаем все сообщения в канале
            async for message in lfg_channel.history(limit=50):
                if message.author == interaction.client.user and message.embeds:
                    # Проверяем, является ли это тикетом
                    for embed in message.embeds:
                        if embed.description and str(interaction.user.id) in embed.description:
                            # Закрываем старый тикет
                            await message.delete()
                            break
        except:
            pass

        fissure = self.parent_view.selected_fissure
        relic_type = fissure['Relic']
        relic_display = RELIC_EMOJIS_FINAL.get(relic_type, f"[{relic_type}]")

        # Определяем фракцию из данных разрыва
        faction_name = fissure['Race']

        # Формируем полное название миссии
        mission_full_name = f"{fissure['Type']} @ {fissure['Location']} ({faction_name}) | Ур. {fissure['Level']}"

        # Проверяем, является ли миссия каскадом бездны
        is_cascade = False
        if fissure['Type'] in ["Void Cascade", "Каскад Бездны", "Void Flood", "Потоп Бездны"]:
            is_cascade = True

        mission_info = {
            "type": "каскад" if is_cascade else ("стальной путь" if self.parent_view.is_steel_path else "разрыв"),
            "name": f"{relic_display} {relic_type} {'Каскад' if is_cascade else 'Разрыв'}",
            "full_name": mission_full_name,
            "faction": faction_name,
            "relic": relic_type,
            "relic_display": relic_display,
            "level": fissure['Level']
        }

        ticket_view = LFGTicketView(
            bot=interaction.client,
            mission_info=mission_info,
            initiator=interaction.user,
            slot_names=["Слот 1", "Слот 2", "Слот 3", "Слот 4"],
            message_id=0,
            comment=self.parent_view.comment_text
        )

        embed = ticket_view._create_embed()

        # Получаем роль "Каскад" для упоминания, если миссия является каскадом
        role_mention = ""
        if is_cascade:
            # Сначала проверяем ID роли из конфига
            cascade_role_id = CONFIG.get('CASCAD_ROLE_ID')
            if cascade_role_id:
                cascade_role = interaction.guild.get_role(cascade_role_id)
                if cascade_role:
                    role_mention = f"{cascade_role.mention} "
            else:
                # Если ID нет, ищем по имени
                cascade_role = discord.utils.get(interaction.guild.roles, name="Каскад")
                if cascade_role:
                    role_mention = f"{cascade_role.mention} "

        # Формируем контент сообщения с упоминанием роли
        content_message = f"{role_mention}🌀 **Пати на Каскад Бездны ищет игроков!** Создатель: {interaction.user.mention}"

        if is_cascade:
            sent_message = await lfg_channel.send(content=content_message, embed=embed, view=ticket_view)
        else:
            sent_message = await lfg_channel.send(embed=embed, view=ticket_view)

        ticket_view.message_id = sent_message.id

        await interaction.response.edit_message(
            content=f"✅ Тикет создан в канале {lfg_channel.mention}! (Старый тикет закрыт)" + (f"\nРоль @Каскад упомянута." if is_cascade else ""),
            view=self.parent_view
        )

# =================================================================
# 6. АРБИТРАЖ: ПОЭТАПНЫЙ ВЫБОР
# =================================================================

class MapSelect(discord.ui.Select):
    """Dropdown для выбора Тира карты (Шаг 1)."""
    def __init__(self, bot, initiator: discord.Member):
        self.bot = bot
        self.initiator = initiator
        options = [
            discord.SelectOption(label="S-Тир (Лучшие)", value="S-ТИР", emoji="🔥"),
            discord.SelectOption(label="A-Тир (Средние)", value="A-ТИР", emoji="⭐"),
            discord.SelectOption(label="B-Тир (Базовые)", value="B-ТИР", emoji="🔰"),
            discord.SelectOption(label="C-Тир", value="C-ТИР"),
        ]
        super().__init__(placeholder="Выберите Тир карты...", options=options)

    async def callback(self, interaction: discord.Interaction):
        selected_tier = self.values[0]

        await interaction.response.edit_message(
            content=f"✅ Вы выбрали **{selected_tier}**.\n\n⏳ **Шаг 2: Выберите название карты:**",
            view=TierSelectView(self.bot, selected_tier, self.initiator)
        )

class TierSelect(discord.ui.Select):
    """Dropdown для выбора конкретной карты внутри выбранного Тира (Шаг 2)."""

    def __init__(self, bot, map_tier: str, initiator: discord.Member):
        self.bot = bot
        self.map_tier = map_tier
        self.initiator = initiator

        # Фильтруем карты по тиру (только S, A, B, C)
        map_options = []
        for map_name, map_data in ARBITRATION_MAP_DATABASE.items():
            if map_data["tier"] == map_tier[0]:  # Берем первую букву: "S-ТИР" -> "S"
                map_options.append({
                    "name": map_name,
                    "faction": map_data["faction"],
                    "mission": map_data["mission"],
                    "tileset": map_data["tileset"],
                    "tier": map_data["tier"]
                })

        # Сортируем карты по имени
        map_options.sort(key=lambda x: x['name'])

        options = []
        for item in map_options:
            # Используем только текст для метки и описания
            label = f"{item['name']} ({item['mission']})"
            value = f"{map_tier}|{item['name']}"

            # Описание без эмодзи - просто текст
            description = f"{item['faction']}"
            if len(description) > 100:
                description = description[:97] + "..."

            options.append(discord.SelectOption(
                label=label,
                value=value,
                description=description
            ))

        super().__init__(placeholder=f"Выберите карту в {map_tier}...", options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        map_id_string = self.values[0]
        _, map_name = map_id_string.split('|')

        await interaction.response.edit_message(
            content=f"✅ Вы выбрали карту **{map_name}**.\n\n⏳ **Шаг 3: Займите свой стартовый слот (и добавьте коммент):**",
            view=RoleSelectView(self.bot, map_id_string, self.initiator)
        )

class RoleSelect(discord.ui.Select):
    """Dropdown для выбора первой роли инициатора (Арбитраж)."""
    def __init__(self, bot, map_id_string: str, initiator: discord.Member):
        self.bot = bot
        self.map_id_string = map_id_string
        self.initiator = initiator

        ARBITRAGE_SLOTS = [
            "Сарина/Цит (Джейд)",
            "Сарина/Цит",
            "Вольт / Хрома / Локи",
            "Висп"
        ]

        options = [
            discord.SelectOption(label=role, value=role)
            for role in ARBITRAGE_SLOTS
        ]

        super().__init__(placeholder="Займите свой первый слот...", options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        selected_role = self.values[0]
        view = self.view

        tier_str, map_name = self.map_id_string.split('|')
        tier = tier_str[0]  # "S-ТИР" -> "S"

        # Получаем данные карты из базы
        map_data = ARBITRATION_MAP_DATABASE.get(map_name)

        if not map_data:
            # Если карты нет в базе, создаем дефолтные данные
            map_data = {
                "faction": "Гринир",
                "tier": tier,
                "mission": "Оборона",
                "tileset": "Grineer Galleon"
            }

        # Определяем фракцию
        faction_name = map_data['faction']

        # Формируем полное название миссии
        mission_full_name = f"{map_name} ({faction_name}) - {map_data['mission']} | {map_data['tileset']}"

        lfg_channel_id = CONFIG.get('LFG_CHANNEL_ID')
        if not lfg_channel_id:
            return await interaction.response.send_message("❌ Канал поиска пати не настроен! Используйте `!set_lfg_channel`.", ephemeral=True)

        lfg_channel = self.bot.get_channel(lfg_channel_id)

        ARBITRAGE_SLOTS = [
            "Сарина/Цит (Джейд)",
            "Сарина/Цит",
            "Вольт / Хрома / Локи",
            "Висп"
        ]

        # Закрываем старый тикет пользователя, если он есть
        try:
            # Получаем все сообщения в канале
            async for message in lfg_channel.history(limit=50):
                if message.author == interaction.client.user and message.embeds:
                    # Проверяем, является ли это тикетом
                    for embed in message.embeds:
                        if embed.description and str(interaction.user.id) in embed.description:
                            # Закрываем старый тикет
                            await message.delete()
                            break
        except:
            pass

        # Ищем роль для упоминания (по имени карты)
        role_mention = ""
        if interaction.guild:
            # Сначала проверяем MAP_ROLES
            map_role_id = CONFIG.get('MAP_ROLES', {}).get(map_name)
            if map_role_id:
                role = interaction.guild.get_role(map_role_id)
                if role:
                    role_mention = f"{role.mention} "
            else:
                # Если ID нет, ищем по имени
                role = discord.utils.get(interaction.guild.roles, name=map_name)
                if role:
                    role_mention = f"{role.mention} "

        # Создаем сообщение с упоминанием роли карты
        content_message = f"{role_mention}🎮 **Пати на Арбитраж ищет игроков!** Создатель: {interaction.user.mention}"

        mission_info = {
            "type": "арбитраж",
            "name": f"{tier_str} Арбитраж",
            "full_name": mission_full_name,
            "faction": faction_name,
            "tier": tier,
            "map_name": map_name
        }

        ticket_view = LFGTicketView(
            bot=interaction.client,
            mission_info=mission_info,
            initiator=interaction.user,
            slot_names=ARBITRAGE_SLOTS,
            message_id=0,
            comment=getattr(view, 'comment_text', None)
        )

        embed = ticket_view._create_embed()

        # Отправляем сообщение с упоминанием и тикетом
        sent_message = await lfg_channel.send(content=content_message, embed=embed, view=ticket_view)
        ticket_view.message_id = sent_message.id

        await interaction.response.edit_message(
            content=f"🎉 **Тикет создан!** Вы выбрали слот **{selected_role}**. Комментарий: {getattr(view, 'comment_text', 'Нет') or 'Нет'}. Проверьте канал {lfg_channel.mention} и займите слот! (Старый тикет закрыт)",
            view=None
        )

class MapSelectView(discord.ui.View):
    """View-контейнер для MapSelect."""
    def __init__(self, bot, initiator: discord.Member):
        super().__init__(timeout=600)
        self.bot = bot
        self.add_item(MapSelect(bot, initiator))

class TierSelectView(discord.ui.View):
    """View-контейнер для TierSelect."""
    def __init__(self, bot, map_tier: str, initiator: discord.Member):
        super().__init__(timeout=600)
        self.bot = bot
        self.add_item(TierSelect(bot, map_tier, initiator))

class RoleSelectView(discord.ui.View):
    """View-контейнер для RoleSelect."""
    def __init__(self, bot, map_id_string: str, initiator: discord.Member):
        super().__init__(timeout=600)
        self.bot = bot
        self.map_id_string = map_id_string
        self.initiator = initiator
        self.comment_text = None

        self.add_item(RoleSelect(bot, map_id_string, initiator))

    @discord.ui.button(label="Добавить коммент 📝", style=discord.ButtonStyle.secondary, row=1)
    async def add_comment_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = CommentModal(view=self)
        await interaction.response.send_modal(modal)

class CurrentArbitrationRoleSelect(discord.ui.Select):
    """Dropdown для выбора роли на текущий арбитраж."""
    def __init__(self, bot, current_arbitration: Dict, initiator: discord.Member):
        self.bot = bot
        self.current_arbitration = current_arbitration
        self.initiator = initiator

        ARBITRAGE_SLOTS = [
            "Сарина/Цит (Джейд)",
            "Сарина/Цит",
            "Вольт / Хрома / Локи",
            "Висп"
        ]

        options = [
            discord.SelectOption(label=role, value=role)
            for role in ARBITRAGE_SLOTS
        ]

        super().__init__(placeholder="Займите свой первый слот...", options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        selected_role = self.values[0]
        view = self.view

        lfg_channel_id = CONFIG.get('LFG_CHANNEL_ID')
        if not lfg_channel_id:
            return await interaction.response.send_message("❌ Канал поиска пати не настроен! Используйте `!set_lfg_channel`.", ephemeral=True)

        lfg_channel = self.bot.get_channel(lfg_channel_id)

        ARBITRAGE_SLOTS = [
            "Сарина/Цит (Джейд)",
            "Сарина/Цит",
            "Вольт / Хрома / Локи",
            "Висп"
        ]

        # Закрываем старый тикет пользователя, если он есть
        try:
            # Получаем все сообщения в канале
            async for message in lfg_channel.history(limit=50):
                if message.author == interaction.client.user and message.embeds:
                    # Проверяем, является ли это тикетом
                    for embed in message.embeds:
                        if embed.description and str(interaction.user.id) in embed.description:
                            # Закрываем старый тикет
                            await message.delete()
                            break
        except:
            pass

        # Получаем название карты для упоминания роли
        node_name = self.current_arbitration.get('Node', '').split(',')[0].strip()
        role_mention = ""
        if interaction.guild and node_name:
            # Сначала проверяем MAP_ROLES
            map_role_id = CONFIG.get('MAP_ROLES', {}).get(node_name)
            if map_role_id:
                role = interaction.guild.get_role(map_role_id)
                if role:
                    role_mention = f"{role.mention} "
            else:
                # Если ID нет, ищем по имени
                role = discord.utils.get(interaction.guild.roles, name=node_name)
                if role:
                    role_mention = f"{role.mention} "

        content_message = f"{role_mention}🎮 **Пати на текущий Арбитраж ищет игроков!** Создатель: {interaction.user.mention}"

        # Определяем фракцию
        faction_name = self.current_arbitration.get('Tileset', 'Орокин')

        # Формируем полное название миссии
        mission_full_name = f"{self.current_arbitration.get('Node', 'N/A')} ({faction_name}) - {self.current_arbitration.get('Name', 'N/A')}"

        mission_info = {
            "type": "арбитраж",
            "name": f"{self.current_arbitration.get('Tier', 'N/A')} Арбитраж",
            "full_name": mission_full_name,
            "faction": faction_name,
            "tier": self.current_arbitration.get('Tier', 'N/A')
        }

        ticket_view = LFGTicketView(
            bot=interaction.client,
            mission_info=mission_info,
            initiator=interaction.user,
            slot_names=ARBITRAGE_SLOTS,
            message_id=0,
            comment=getattr(view, 'comment_text', None)
        )

        embed = ticket_view._create_embed()

        sent_message = await lfg_channel.send(content=content_message, embed=embed, view=ticket_view)
        ticket_view.message_id = sent_message.id

        await interaction.response.edit_message(
            content=f"🎉 **Тикет создан!** Вы выбрали слот **{selected_role}**. Комментарий: {getattr(view, 'comment_text', 'Нет') or 'Нет'}. Проверьте канал {lfg_channel.mention} и займите слот! (Старый тикет закрыт)",
            view=None
        )

class CurrentArbitrationRoleSelectView(discord.ui.View):
    """View-контейнер для выбора роли на текущий арбитраж."""
    def __init__(self, bot, current_arbitration: Dict, initiator: discord.Member):
        super().__init__(timeout=600)
        self.bot = bot
        self.current_arbitration = current_arbitration
        self.initiator = initiator
        self.comment_text = None

        self.add_item(CurrentArbitrationRoleSelect(bot, current_arbitration, initiator))

    @discord.ui.button(label="Добавить коммент 📝", style=discord.ButtonStyle.secondary, row=1)
    async def add_comment_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = CommentModal(view=self)
        await interaction.response.send_modal(modal)

class ArbitrationLfgView(discord.ui.View):
    """View для создания LFG тикетов на арбитраж в канале."""

    def __init__(self, current_arbitration: Dict):
        super().__init__(timeout=None)
        self.current_arbitration = current_arbitration

    @discord.ui.button(label="Создать пати на Арбитраж", style=discord.ButtonStyle.green, emoji="🎯", row=0)
    async def create_arbitration_party(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "⏳ **Шаг 1: Выберите Тир карты Арбитража (S, A, B, C):**",
            view=MapSelectView(interaction.client, interaction.user),
            ephemeral=True
        )

    @discord.ui.button(label="На текущий арбитраж", style=discord.ButtonStyle.blurple, emoji="🎯", row=0)
    async def current_arbitration_party(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.current_arbitration or self.current_arbitration.get('Node') == 'N/A':
            await interaction.response.send_message("Нет данных о текущем арбитраже!", ephemeral=True)
            return

        await interaction.response.send_message(
            "⏳ **Выберите роль для текущего арбитража (и добавьте коммент):**",
            view=CurrentArbitrationRoleSelectView(interaction.client, self.current_arbitration, interaction.user),
            ephemeral=True
        )

# =================================================================
# 7. ОПТИМИЗИРОВАННАЯ ЛОГИКА СКРАПИНГА С КЭШИРОВАНИЕМ
# =================================================================

def parse_arbitration_schedule(soup: BeautifulSoup, current_scrape_time: float) -> Dict[str, Any]:
    """Парсит данные о расписании Арбитражей."""
    schedule = {"Current": {}, "Upcoming": []}

    log_div = soup.find('div', id='log')
    if not log_div: return schedule

    all_missions = log_div.find_all(['b', 'span'], attrs={'data-timestamp': True})
    msk_tz = timezone(timedelta(hours=3))
    parsed_missions = []

    for tag in all_missions:
        try:
            text_content = tag.text.strip()
            tier_bonus_match = re.search(r'\((.+?)\s*tier(?:,\s*(.+?))?\)$', text_content)
            if not tier_bonus_match: continue

            tier = tier_bonus_match.group(1).strip().upper()
            bonus = tier_bonus_match.group(2).strip() if tier_bonus_match.group(2) else 'N/A'
            mission_info_raw = re.sub(r'^\d{2}:\d{2}\s*•\s*', '', text_content)
            mission_info_raw = re.sub(r'\s*\(.+\)$', '', mission_info_raw).strip()

            mission_match = re.search(r'(.+?)\s*-\s*(.+?)\s*@\s*(.+?),\s*(.+?)$', mission_info_raw)
            if not mission_match: continue

            mission_type_raw = mission_match.group(1).strip()
            faction_raw = mission_match.group(2).strip()
            node = mission_match.group(3).strip()
            planet = mission_match.group(4).strip()

            location_combined = f"{node}, {planet}"

            start_timestamp = int(tag.attrs['data-timestamp'])
            end_timestamp = start_timestamp + 3600

            utc_dt = datetime.fromtimestamp(start_timestamp, tz=timezone.utc)
            msk_dt = utc_dt.astimezone(msk_tz)
            msk_start_time_display = msk_dt.strftime('%H:%M')

            parsed_missions.append({
                "Tier": tier,
                "Type": MISSION_TYPE_TRANSLATIONS.get(mission_type_raw, mission_type_raw),
                "Faction": normalize_faction_name(faction_raw, location_combined),
                "Node": node,
                "Location": location_combined,
                "Bonus": bonus,
                "StartTimeDisplay": msk_start_time_display,
                "StartTimestamp": start_timestamp,
                "EndTimestamp": end_timestamp,
            })
        except Exception:
            continue

    now = current_scrape_time
    parsed_missions.sort(key=lambda m: m['StartTimestamp'])
    current_mission: Optional[Dict[str, Any]] = None
    upcoming_missions_list: List[Dict[str, Any]] = []

    for mission in parsed_missions:
        start = mission['StartTimestamp']
        end = mission['EndTimestamp']

        if start <= now < end:
            current_mission = mission
        elif start > now:
            upcoming_missions_list.append(mission)

    target_mission = current_mission
    is_active = True

    if not target_mission and upcoming_missions_list:
        target_mission = upcoming_missions_list.pop(0)
        is_active = False

    if target_mission:
        target_ts = target_mission['EndTimestamp'] if is_active else target_mission['StartTimestamp']

        schedule["Current"] = {
            "Tier": target_mission["Tier"],
            "Name": target_mission["Type"],
            "Location": target_mission["Location"],
            "Node": target_mission["Node"],
            "Type": target_mission["Type"],
            "Tileset": target_mission["Faction"],
            "Bonus": target_mission["Bonus"],
            "StartTimestamp": target_mission["StartTimestamp"],
            "TargetTimestamp": target_ts,
            "IsActive": is_active
        }
    else:
        schedule["Current"] = {"Tier": "N/A", "IsActive": False, "Node": "N/A"}

    for mission in upcoming_missions_list:
        if mission['StartTimestamp'] > now:
            mission['TargetTimestamp'] = mission['StartTimestamp']
            schedule["Upcoming"].append(mission)

    schedule["Upcoming"] = schedule["Upcoming"][:20]

    return schedule

def parse_fissure_table(table: Tag, current_scrape_time: float, is_steel_path_table: bool = False) -> List[Dict[str, Any]]:
    """Парсит строки из одной таблицы разрывов."""
    fissures_list: List[Dict[str, Any]] = []
    rows = table.find_all('tr')
    last_relic_type = "N/A"

    for row in rows:
        relic_th = row.find('th')
        if relic_th and relic_th.text.strip():
            last_relic_type = relic_th.text.strip()

        mission_td = row.find('td')
        if not mission_td:
            continue

        mission_type_tag = mission_td.find('b')
        mission_type_raw = mission_type_tag.text.strip() if mission_type_tag else "Unknown Mission"
        if mission_type_raw.startswith("М."):
            mission_type_raw = mission_type_raw[2:].strip()

        mission_type = MISSION_TYPE_TRANSLATIONS.get(mission_type_raw, mission_type_raw)

        expiry_span = mission_td.find('span', class_='badge')
        time_str = expiry_span.text.strip() if expiry_span else "N/A"
        time_in_seconds = parse_time_to_seconds(time_str)
        expiry_time = current_scrape_time + time_in_seconds

        # Ищем все span элементы
        all_spans = mission_td.find_all('span')

        location_span = None
        for span in all_spans:
            # Ищем span без классов (обычно содержит уровень и локацию)
            if not span.get('class') and not span.get('data-expiry'):
                location_span = span
                break

        level_range, location, race = "N/A", "N/A", "N/A"

        if location_span:
            location_raw = location_span.text.strip()

            # Исправленный regex для обработки формата "(2-4) - Гринир @ Mantle, Земля"
            level_match = re.search(r'\(([^)]+)\)\s*-\s*([^@]+)(?:@\s*(.+))?', location_raw)

            if level_match:
                level_range = level_match.group(1).strip()
                race = level_match.group(2).strip()
                location = level_match.group(3).strip() if level_match.group(3) else 'N/A'
            else:
                # Попробуем другой формат
                level_match = re.search(r'\(([^)]+)\)', location_raw)
                if level_match:
                    level_range = level_match.group(1).strip()
                    remaining = location_raw.replace(f'({level_range})', '').strip()

                    if '@' in remaining:
                        parts = remaining.split('@', 1)
                        race = parts[0].replace('-', '').strip()
                        location = parts[1].strip()
                    else:
                        location = remaining
                        race = 'N/A'

        if mission_type != "Unknown Mission" or mission_type_raw != "Unknown Mission":
            fissure_data = {
                "Relic": last_relic_type,
                "Type": mission_type,
                "Level": level_range,
                "Location": location,
                "Race": normalize_faction_name(race, location),
                "ExpiryTime": expiry_time
            }

            if fissure_data["Relic"] == "Omnia":
                fissure_data["Race"] = "Гринир"

            if is_steel_path_table or "Steel Path" in location or "Steel Path" in mission_type_raw:
                fissure_data['Type'] = fissure_data['Type'].replace("(Steel Path)", "").strip()
                fissure_data['Location'] = fissure_data['Location'].replace(" (Steel Path)", "").strip()

            # Добавляем только если есть реликвия
            if fissure_data["Relic"] != "N/A":
                fissures_list.append(fissure_data)

    return fissures_list

# ThreadPoolExecutor для запуска синхронных задач в отдельных потоках
from concurrent.futures import ThreadPoolExecutor
executor = ThreadPoolExecutor(max_workers=1)

def get_cached_arbitration():
    """Получает данные арбитража из кэша."""
    cache_key = "arbitration_current"
    if cache_key in ARBITRATION_CACHE:
        SCRAPE_STATS["cache_hits"] += 1
        return ARBITRATION_CACHE[cache_key]
    SCRAPE_STATS["cache_misses"] += 1
    return None

def set_cached_arbitration(data, ttl=300):
    """Сохраняет данные арбитража в кэш."""
    cache_key = "arbitration_current"
    ARBITRATION_CACHE[cache_key] = data

def get_cached_fissures(fissure_type="normal"):
    """Получает данные разрывов из кэша."""
    cache_key = f"fissures_{fissure_type}"
    if cache_key in FISSURE_CACHE:
        SCRAPE_STATS["cache_hits"] += 1
        return FISSURE_CACHE[cache_key]
    SCRAPE_STATS["cache_misses"] += 1
    return None

def set_cached_fissures(data, fissure_type="normal", ttl=120):
    """Сохраняет данные разрывов в кэш."""
    cache_key = f"fissures_{fissure_type}"
    FISSURE_CACHE[cache_key] = data

def get_cached_tier_mission(tier):
    """Получает данные тира из кэша."""
    cache_key = f"tier_{tier}"
    if cache_key in TIER_CACHE:
        SCRAPE_STATS["cache_hits"] += 1
        return TIER_CACHE[cache_key]
    SCRAPE_STATS["cache_misses"] += 1
    return None

def set_cached_tier_mission(tier, data, ttl=1800):
    """Сохраняет данные тира в кэш."""
    cache_key = f"tier_{tier}"
    TIER_CACHE[cache_key] = data

def sync_scrape_all_data():
    """Синхронная версия скрапинга данных Разрывов и Арбитража с кэшированием."""
    print(f"[{time.strftime('%H:%M:%S')}] 🔄 Запуск синхронного скрапинга...")
    current_scrape_time = time.time()
    results = {"Fissures": [], "SteelPathFissures": [], "ArbitrationSchedule": {}}

    SCRAPE_STATS["total_scrapes"] += 1

    max_retries = 2  # Уменьшили количество попыток для экономии ресурсов
    retry_count = 0

    while retry_count < max_retries:
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(
                    viewport={'width': 1920, 'height': 1080},
                    user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
                )

                # Скрапинг Разрывов с повторными попытками
                page_fissures = context.new_page()
                page_fissures.set_default_timeout(30000)  # Уменьшили таймаут

                try:
                    # Добавляем дополнительные заголовки
                    page_fissures.set_extra_http_headers({
                        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                        'Accept-Language': 'ru-RU,ru;q=0.8,en-US;q=0.5,en;q=0.3',
                        'Accept-Encoding': 'gzip, deflate, br',
                    })

                    # Пытаемся загрузить страницу с разрывами
                    print(f"[{time.strftime('%H:%M:%S')}]   -> Загрузка страницы разрывов...")
                    response = page_fissures.goto(
                        FISSURE_URL,
                        wait_until="domcontentloaded",  # Изменили на domcontentloaded для скорости
                        timeout=30000
                    )

                    if not response or response.status != 200:
                        print(f"[{time.strftime('%H:%M:%S')}] ⚠️ Не удалось загрузить страницу разрывов")
                        retry_count += 1
                        continue

                    # Ждем появления таблиц
                    print(f"[{time.strftime('%H:%M:%S')}]   -> Ожидание таблиц...")
                    try:
                        page_fissures.wait_for_selector('table', timeout=15000)
                    except:
                        print(f"[{time.strftime('%H:%M:%S')}] ⚠️ Таблицы не найдены")
                        retry_count += 1
                        continue

                    time.sleep(1.0)  # Уменьшили паузу

                    # Получаем HTML контент
                    html_content = page_fissures.content()
                    soup_fissures = BeautifulSoup(html_content, 'html.parser')

                    # Ищем все таблицы
                    tables = soup_fissures.find_all('table')
                    print(f"[{time.strftime('%H:%M:%S')}]   -> Найдено таблиц: {len(tables)}")

                    # Ищем таблицу обычных разрывов (Void Fissures)
                    normal_table = None
                    sp_table = None

                    for table in tables:
                        table_html = str(table).lower()

                        # Ищем таблицу обычных разрывов
                        if ('lith' in table_html and 'meso' in table_html and 'neo' in table_html and 'axi' in table_html) or 'fissures-table' in table_html:
                            # Проверяем, что это не Steel Path таблица
                            if 'steel path' not in table_html and 'sp-fissures' not in table_html:
                                normal_table = table
                                print(f"[{time.strftime('%H:%M:%S')}]   -> Найдена таблица обычных разрывов")
                                break

                    # Ищем таблицу SP отдельно
                    for table in tables:
                        table_html = str(table).lower()
                        if 'sp-fissures' in table_html or 'steel path' in table_html:
                            sp_table = table
                            print(f"[{time.strftime('%H:%M:%S')}]   -> Найдена таблица SP")
                            break

                    # Если не нашли, пробуем другие методы
                    if not normal_table:
                        normal_table = soup_fissures.find('table', id='fissures-table')
                        if normal_table:
                            print(f"[{time.strftime('%H:%M:%S')}]   -> Найдена таблица обычных разрывов по ID")

                    if not sp_table:
                        sp_table = soup_fissures.find('table', id='sp-fissures-table')
                        if sp_table:
                            print(f"[{time.strftime('%H:%M:%S')}]   -> Найдена таблица SP по ID")

                    # Парсим таблицы
                    if normal_table:
                        normal_fissures = parse_fissure_table(normal_table, current_scrape_time, False)
                        results["Fissures"] = normal_fissures
                        set_cached_fissures(normal_fissures, "normal")
                        print(f"[{time.strftime('%H:%M:%S')}]   -> Обычные разрывы: {len(normal_fissures)}")
                    else:
                        print(f"[{time.strftime('%H:%M:%S')}] ⚠️ Таблица обычных разрывов не найдена")
                        SCRAPE_STATS["fissures_errors"] += 1

                    if sp_table:
                        sp_fissures = parse_fissure_table(sp_table, current_scrape_time, True)
                        results["SteelPathFissures"] = sp_fissures
                        set_cached_fissures(sp_fissures, "steel_path")
                        print(f"[{time.strftime('%H:%M:%S')}]   -> Разрывы SP: {len(sp_fissures)}")
                    else:
                        print(f"[{time.strftime('%H:%M:%S')}] ⚠️ Таблица SP не найдена")

                except Exception as e:
                    print(f"[{time.strftime('%H:%M:%S')}] 🚨 Ошибка скрапинга Разрывов: {e}")
                    SCRAPE_STATS["failed_scrapes"] += 1
                    SCRAPE_STATS["fissures_errors"] += 1
                    SCRAPE_STATS["last_error"] = f"Ошибка разрывов: {str(e)}"
                    SCRAPE_STATS["last_error_time"] = time.time()

                    if retry_count < max_retries - 1:
                        retry_count += 1
                        time.sleep(3)
                        continue
                finally:
                    page_fissures.close()

                # Скрапинг Арбитражей
                page_arbys = context.new_page()
                page_arbys.set_default_timeout(20000)  # Уменьшили таймаут
                try:
                    print(f"[{time.strftime('%H:%M:%S')}]   -> Загрузка страницы арбитража...")
                    page_arbys.goto(ARBY_URL, wait_until="domcontentloaded")
                    page_arbys.wait_for_selector('#log', timeout=15000)
                    time.sleep(1.0)

                    soup_arbys = BeautifulSoup(page_arbys.content(), 'html.parser')
                    arbitration_data = parse_arbitration_schedule(soup_arbys, current_scrape_time)
                    results["ArbitrationSchedule"] = arbitration_data
                    set_cached_arbitration(arbitration_data)

                    arb_tier = results["ArbitrationSchedule"].get("Current", {}).get("Tier", "N/A")
                    print(f"[{time.strftime('%H:%M:%S')}]   -> Арбитраж: {arb_tier}")

                    SCRAPE_STATS["successful_scrapes"] += 1

                except Exception as e:
                    print(f"[{time.strftime('%H:%M:%S')}] 🚨 Ошибка скрапинга Арбитража: {e}")
                    SCRAPE_STATS["failed_scrapes"] += 1
                    SCRAPE_STATS["arbitration_errors"] += 1
                    SCRAPE_STATS["last_error"] = f"Ошибка арбитража: {str(e)}"
                    SCRAPE_STATS["last_error_time"] = time.time()
                finally:
                    page_arbys.close()

                context.close()
                browser.close()

                break  # Успешный скрапинг

        except Exception as e:
            print(f"[{time.strftime('%H:%M:%S')}] 💥 Критическая ошибка Playwright: {e}")
            SCRAPE_STATS["failed_scrapes"] += 1
            SCRAPE_STATS["last_error"] = f"Критическая ошибка: {str(e)}"
            SCRAPE_STATS["last_error_time"] = time.time()

            if retry_count < max_retries - 1:
                retry_count += 1
                time.sleep(5)
                continue

    set_current_state(results, current_scrape_time)

    changed_channels = []
    if results.get("ArbitrationSchedule", {}).get("Current", {}).get("Node", "N/A") != "N/A" and results["ArbitrationSchedule"]["Current"]["Node"] != '':
        changed_channels.append("Арбитраж")
    if len(results.get("Fissures", [])) > 0:
        changed_channels.append("Обычные разрывы")
    if len(results.get("SteelPathFissures", [])) > 0:
        changed_channels.append("Разрывы стального пути")

    if changed_channels:
        print(f"[{time.strftime('%H:%M:%S')}] 📢 Обнаружены изменения в: {', '.join(changed_channels)}")

    return results

async def scrape_all_data():
    """Асинхронная обертка для скрапинга данных (запускает в отдельном потоке)."""
    loop = asyncio.get_event_loop()
    try:
        # Проверяем кэш перед скрапингом
        cached_arb = get_cached_arbitration()
        cached_normal_fissures = get_cached_fissures("normal")
        cached_sp_fissures = get_cached_fissures("steel_path")
        
        # Если есть свежие данные в кэше, используем их
        current_time = time.time()
        if (cached_arb and cached_normal_fissures and cached_sp_fissures and 
            current_time - LAST_SCRAPE_TIME < 60):  # Если прошло меньше минуты с последнего скрапинга
            print(f"[{time.strftime('%H:%M:%S')}] 🔄 Используем кэшированные данные")
            results = {
                "Fissures": cached_normal_fissures,
                "SteelPathFissures": cached_sp_fissures,
                "ArbitrationSchedule": cached_arb
            }
            set_current_state(results, current_time)
            return results
        
        # Иначе запускаем скрапинг
        results = await loop.run_in_executor(executor, sync_scrape_all_data)
        return results
    except Exception as e:
        print(f"[{time.strftime('%H:%M:%S')}] 💥 Ошибка в скрапинге: {e}")
        return {"Fissures": [], "SteelPathFissures": [], "ArbitrationSchedule": {}}

async def continuous_scraping():
    """Непрерывный скрапинг с минимальными интервалами."""
    print(f"[{time.strftime('%H:%M:%S')}] 🔄 Запуск непрерывного скрапинга...")

    # Начальный скрапинг
    await scrape_all_data()

    while True:
        try:
            start_time = time.time()
            results = await scrape_all_data()

            # Минимальная пауза между проверками
            elapsed = time.time() - start_time
            sleep_time = max(3.0, SCRAPE_INTERVAL_SECONDS - elapsed)  # Минимум 3 сек
            await asyncio.sleep(sleep_time)

        except Exception as e:
            print(f"[{time.strftime('%H:%M:%S')}] 💥 Ошибка в непрерывном скрапинге: {e}")
            await asyncio.sleep(10)  # Пауза при ошибке

# =================================================================
# 8. КЭШ И ОПТИМИЗИРОВАННАЯ ЛОГИКА ОБНОВЛЕНИЯ КАНАЛОВ
# =================================================================

class ChannelCache:
    """Кэш для хранения состояния каналов."""

    def __init__(self):
        self.last_arbitration_embed = None
        self.last_fissure_embed = None
        self.last_sp_embed = None
        self.cache_lock = asyncio.Lock()

    async def should_update_channel(self, channel_type: str, new_embed: discord.Embed) -> bool:
        """Проверяет, нужно ли обновлять канал."""
        async with self.cache_lock:
            if channel_type == "arbitration":
                if self.last_arbitration_embed is None:
                    self.last_arbitration_embed = new_embed
                    return True

                # Сравниваем только важные поля эмбеда
                old_dict = self.last_arbitration_embed.to_dict()
                new_dict = new_embed.to_dict()

                # Игнорируем временные метки в сравнении
                if 'footer' in old_dict:
                    old_dict.pop('footer', None)
                if 'footer' in new_dict:
                    new_dict.pop('footer', None)

                if old_dict != new_dict:
                    self.last_arbitration_embed = new_embed
                    return True
                return False

            elif channel_type == "fissure":
                if self.last_fissure_embed is None:
                    self.last_fissure_embed = new_embed
                    return True

                old_dict = self.last_fissure_embed.to_dict()
                new_dict = new_embed.to_dict()

                if 'footer' in old_dict:
                    old_dict.pop('footer', None)
                if 'footer' in new_dict:
                    new_dict.pop('footer', None)

                if old_dict != new_dict:
                    self.last_fissure_embed = new_embed
                    return True
                return False

            elif channel_type == "steel_path":
                if self.last_sp_embed is None:
                    self.last_sp_embed = new_embed
                    return True

                old_dict = self.last_sp_embed.to_dict()
                new_dict = new_embed.to_dict()

                if 'footer' in old_dict:
                    old_dict.pop('footer', None)
                if 'footer' in new_dict:
                    new_dict.pop('footer', None)

                if old_dict != new_dict:
                    self.last_sp_embed = new_embed
                    return True
                return False

            return True

channel_cache = ChannelCache()

async def send_or_edit_message(message_id_key: str, channel: discord.TextChannel, embed: discord.Embed, content: str = None, view: discord.ui.View = None):
    """Отправляет или редактирует сообщение в канале."""
    if content is None or content.strip() == "":
        content = None

    try:
        message_id = CONFIG.get(message_id_key)

        if message_id:
            try:
                message = await channel.fetch_message(message_id)
                await message.edit(content=content, embed=embed, view=view)
                return
            except discord.NotFound:
                pass

        sent_message = await channel.send(content=content, embed=embed, view=view)
        CONFIG[message_id_key] = sent_message.id
        save_config()

    except discord.Forbidden:
        print(f"[{time.strftime('%H:%M:%S')}] ❌ Нет прав для отправки/редактирования в канале {channel.name}.")
    except Exception as e:
        print(f"[{time.strftime('%H:%M:%S')}] 🚨 Ошибка при обновлении канала {channel.name}: {e}")

def format_fissure_list_vertical(fissures: List[Dict[str, Any]]) -> str:
    """Форматирует список всех разрывов с правильным разделением."""
    grouped_fissures = defaultdict(list)
    current_time = time.time()

    for mission in fissures:
        if mission['ExpiryTime'] > current_time:
            grouped_fissures[mission['Relic']].append(mission)

    output = []
    relic_order = ["Lith", "Meso", "Neo", "Axi", "Requiem", "Omnia", "Steel Path"]

    for i, relic_type in enumerate(relic_order):
        missions = grouped_fissures.get(relic_type, [])
        if not missions: continue

        missions.sort(key=lambda x: x['ExpiryTime'])

        relic_display = RELIC_EMOJIS_FINAL.get(relic_type, f"[{relic_type}]")

        # Добавляем разделитель между эрами (кроме первой)
        if i > 0:
            output.append("—" * 40)

        output.append(f"**{relic_display} {relic_type}**")

        for mission in missions:
            expiry_timestamp = int(mission['ExpiryTime'])
            faction_emoji = FACTION_EMOJIS_FINAL.get(mission['Race'], FALLBACK_EMOJI)

            line = (
                f"**<t:{expiry_timestamp}:R>** | {faction_emoji} `{mission['Type']}` @ {mission['Location']} ({mission['Level']})"
            )
            output.append(line)

    return "\n".join(output)

def split_fissures_into_fields(fissures_content: str) -> List[Tuple[str, str]]:
    """Разбивает полный список миссий на поля Discord."""
    if not fissures_content:
        return [("Нет активных Разрывов.", "\u200b")]

    lines = fissures_content.split('\n')
    fields = []
    current_field_content = []
    current_field_length = 0

    for line in lines:
        line_length = len(line) + 1

        if current_field_length + line_length > MAX_FIELD_LENGTH and current_field_content:
            field_value = "\n".join(current_field_content)
            fields.append(("", field_value))
            current_field_content = []
            current_field_length = 0

        current_field_content.append(line)
        current_field_length += line_length

    if current_field_content:
        field_value = "\n".join(current_field_content)
        fields.append(("", field_value))

    if not fields:
        return [("Нет активных Разрывов.", "\u200b")]

    return fields

def sync_get_earliest_tier_mission(tier: str, current_scrape_time: float) -> Optional[Dict[str, Any]]:
    """Синхронно получает ближайшую миссию определенного тира с сайта с кэшированием."""
    
    # Сначала проверяем кэш
    cached_mission = get_cached_tier_mission(tier)
    if cached_mission:
        print(f"[{time.strftime('%H:%M:%S')}] 🔄 Используем кэшированные данные для {tier}-тира")
        return cached_mission
    
    tier_urls = {
        "S": "https://browse.wf/arbys#days=30&tz=local&hourfmt=mil&exclude=tier-A.tier-B.tier-C.tier-D.tier-F",
        "A": "https://browse.wf/arbys#days=30&tz=local&hourfmt=mil&exclude=tier-S.tier-B.tier-C.tier-D.tier-F",
        "B": "https://browse.wf/arbys#days=30&tz=local&hourfmt=mil&exclude=tier-S.tier-A.tier-C.tier-D.tier-F"
    }

    url = tier_urls.get(tier)
    if not url:
        print(f"[{time.strftime('%H:%M:%S')}] ⚠️ Нет URL для тира {tier}")
        return None

    print(f"[{time.strftime('%H:%M:%S')}] 🔍 Начинаем загрузку {tier}-тира по URL: {url}")

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            print(f"[{time.strftime('%H:%M:%S')}]   -> Браузер запущен для {tier}-тира")

            context = browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            )

            page = context.new_page()
            page.set_default_timeout(20000)  # Уменьшили таймаут

            print(f"[{time.strftime('%H:%M:%S')}]   -> Загрузка страницы для {tier}-тира...")
            response = page.goto(url, wait_until="domcontentloaded", timeout=20000)

            if not response or response.status != 200:
                print(f"[{time.strftime('%H:%M:%S')}] ⚠️ Не удалось загрузить страницу для {tier}-тира, статус: {response.status if response else 'нет ответа'}")
                page.close()
                context.close()
                browser.close()
                return None

            print(f"[{time.strftime('%H:%M:%S')}]   -> Страница загружена, ожидаем элемент #log...")
            try:
                page.wait_for_selector('#log', timeout=15000)
                print(f"[{time.strftime('%H:%M:%S')}]   -> Элемент #log найден")
            except Exception as e:
                print(f"[{time.strftime('%H:%M:%S')}] ⚠️ Элемент #log не найден для {tier}-тира: {e}")
                # Попробуем взять контент даже если #log не найден
                print(f"[{time.strftime('%H:%M:%S')}]   -> Пытаемся получить контент страницы...")

            time.sleep(0.5)  # Уменьшили задержку

            content = page.content()

            page.close()
            context.close()
            browser.close()

            soup = BeautifulSoup(content, 'html.parser')

            # Используем существующую функцию парсинга
            schedule = parse_arbitration_schedule(soup, current_scrape_time)

            # Возвращаем текущую или следующую миссию
            current = schedule.get("Current", {})
            upcoming = schedule.get("Upcoming", [])

            print(f"[{time.strftime('%H:%M:%S')}]   -> Для {tier}-тира найдено: текущих - {1 if current.get('Node') != 'N/A' else 0}, upcoming - {len(upcoming)}")

            mission_result = None

            # Если есть текущая активная миссия нужного тира
            if current.get('Node') != 'N/A' and current.get('Tier', '').upper() == tier:
                print(f"[{time.strftime('%H:%M:%S')}]   -> Найден текущий {tier}-тир: {current.get('Node')}")
                mission_result = current

            # Ищем первую upcoming миссию нужного тира
            if not mission_result:
                for mission in upcoming:
                    if mission.get('Tier', '').upper() == tier:
                        print(f"[{time.strftime('%H:%M:%S')}]   -> Найден upcoming {tier}-тир: {mission.get('Location')} в {mission.get('StartTimeDisplay')}")
                        mission_result = mission
                        break

            if mission_result:
                # Сохраняем в кэш
                set_cached_tier_mission(tier, mission_result)
                print(f"[{time.strftime('%H:%M:%S')}]   -> {tier}-тир сохранен в кэш")
            else:
                print(f"[{time.strftime('%H:%M:%S')}] ⚠️ {tier}-тир не найден на странице")

            return mission_result

    except Exception as e:
        print(f"[{time.strftime('%H:%M:%S')}] 🚨 Критическая ошибка при получении {tier}-тира: {e}")
        import traceback
        traceback.print_exc()
        return None

async def update_arbitration_channel(bot: commands.Bot):
    """Обновляет канал с Расписанием Арбитражей только при изменениях."""
    arb_id = CONFIG.get('ARBITRATION_CHANNEL_ID')
    if not arb_id:
        return

    arb_channel = bot.get_channel(arb_id)
    if not arb_channel:
        return

    data = CURRENT_MISSION_STATE.get("ArbitrationSchedule", {})
    current_arb = data.get("Current", {})

    # Если арбитраж N/A или пустой, НЕ обновляем сообщение
    if current_arb.get('Node') == 'N/A' or not current_arb.get('Node'):
        return

    upcoming = data.get("Upcoming", [])

    embed_tier = current_arb.get("Tier", "N/A").upper()
    embed_color = TIER_COLORS.get(embed_tier, FALLBACK_COLOR)
    tier_emoji = TIER_EMOJIS_FINAL.get(embed_tier, embed_tier)
    is_active = current_arb.get('IsActive', False)
    target_ts = current_arb.get('TargetTimestamp')

    faction_name = current_arb.get('Tileset', 'N/A')
    faction_emoji = FACTION_EMOJIS_FINAL.get(faction_name, FALLBACK_EMOJI)
    faction_url = get_faction_image_url(faction_name)

    # Используем правильные эмодзи вместо сломанных
    vitus_emoji = RESOLVED_EMOJIS.get(EMOJI_NAMES.get(VITUS_EMOJI_KEY), "⭐")
    kuva_emoji = RESOLVED_EMOJIS.get(EMOJI_NAMES.get(KUVA_EMOJI_KEY), "⚡️")

    time_line = "Время: **`Нет данных`**"
    if target_ts:
        time_line = f"завершится <t:{int(target_ts)}:R>" if is_active else f"начнется <t:{int(target_ts)}:R>"

    # ВАЖНО: Всегда формируем упоминание роли в основном сообщении
    content_to_send: Optional[str] = None
    node_name = current_arb.get('Node', '').split(',')[0].strip()

    # Получаем роль для текущей карты
    role_mention = ""
    if node_name and arb_channel.guild:
        # Сначала проверяем MAP_ROLES
        map_role_id = CONFIG.get('MAP_ROLES', {}).get(node_name)
        if map_role_id:
            role = arb_channel.guild.get_role(map_role_id)
            if role:
                role_mention = f"{role.mention} "
        else:
            # Ищем роль по имени
            role = discord.utils.get(arb_channel.guild.roles, name=node_name)
            if role:
                role_mention = f"{role.mention} "

        # Если есть роль для упоминания, добавляем в контент
        if role_mention:
            content_to_send = f"{role_mention}"

    embed = discord.Embed(
        title=f"{vitus_emoji}{vitus_emoji}{vitus_emoji} РАСПИСАНИЕ АРБИТРАЖЕЙ {vitus_emoji}{vitus_emoji}{vitus_emoji}",
        url="https://browse.wf/arbys", color=embed_color
    )

    if current_arb.get("Name"):
        tier_display = f"{tier_emoji} Тир" if embed_tier != "N/A" else ""

        title_line = f"{kuva_emoji} **ТЕКУЩИЙ АРБИТРАЖ {kuva_emoji} ({tier_display}):**" if is_active else f"{kuva_emoji} **СЛЕДУЮЩИЙ АРБИТРАЖ ({tier_display}):**"

        description_value = (
            f"**{current_arb.get('Name', 'N/A')}**\n"
            f"Локация: **{current_arb.get('Location', 'N/A')}**\n"
            f"Враг: {faction_emoji} **{faction_name}**\n"
            f"Бонус: **{current_arb.get('Bonus', 'N/A')}**\n"
            f"Время: {time_line}"
        )
        embed.add_field(name=title_line, value=description_value, inline=False)

        if faction_url:
            embed.set_thumbnail(url=faction_url)

    else:
        embed.description = "**Актуальное расписание миссий не найдено.**\nПожалуйста, подождите следующего скрапинга. (Тир: N/A)"
        embed.color = discord.Color.red()

    upcoming_lines = []
    UPCOMING_LIMIT = 5

    if upcoming:
        missions_to_display = upcoming[:UPCOMING_LIMIT]

        for m in missions_to_display:
            upc_tier_emoji = TIER_EMOJIS_FINAL.get(m['Tier'], m['Tier'])
            upc_faction_emoji = FACTION_EMOJIS_FINAL.get(m['Faction'], FALLBACK_EMOJI)

            line = (
                f"{upc_tier_emoji} | <t:{m['StartTimestamp']}:t> • {upc_faction_emoji} ({m['Location']}) **<t:{m['StartTimestamp']}:R>**"
            )
            upcoming_lines.append(line)

    if upcoming_lines:
        field_value = "\n".join(upcoming_lines)
    else:
        field_value = "Нет данных о грядущих миссиях."

    embed.add_field(
        name="\u200b\n— — — БЛИЖАЙШИЕ 5 МИССИЙ — — —",
        value=field_value,
        inline=False
    )

    TIERS_TO_HIGHLIGHT = ["S", "A", "B"]
    embed.add_field(name="\u200b", value="— — — ВЫДЕЛЕННЫЕ ТИРЫ — — —", inline=False)

    # Получаем ближайшие тиры из отдельных запросов
    current_time = time.time()
    tier_missions = {}

    # Запускаем все запросы последовательно в отдельных потоках
    for tier in TIERS_TO_HIGHLIGHT:
        try:
            print(f"[{time.strftime('%H:%M:%S')}] 🔄 Начинаем запрос для {tier}-тира...")

            # Запускаем синхронную функцию в отдельном потоке
            loop = asyncio.get_event_loop()
            mission = await loop.run_in_executor(
                executor,
                sync_get_earliest_tier_mission,
                tier,
                current_time
            )

            if mission:
                print(f"[{time.strftime('%H:%M:%S')}] ✅ Получен {tier}-тир: {mission.get('Node', 'N/A')}")
                tier_missions[tier] = mission
            else:
                print(f"[{time.strftime('%H:%M:%S')}] ⚠️ Не удалось получить {tier}-тир")
        except Exception as e:
            print(f"[{time.strftime('%H:%M:%S')}] 🚨 Исключение при получении {tier}-тира: {e}")
            import traceback
            traceback.print_exc()

    for tier in TIERS_TO_HIGHLIGHT:
        tier_emoji = TIER_EMOJIS_FINAL.get(tier, tier)
        field_name = f"Ближайший {tier_emoji} Тир"

        if tier in tier_missions:
            mission = tier_missions[tier]

            # Извлекаем название ноды (первая часть до запятой)
            location = mission.get('Location', 'N/A')
            node_name_only = location.split(',')[0].strip() if ',' in location else location

            # Получаем эмодзи фракции
            mission_faction = mission.get('Faction', 'Орокин')
            faction_emoji = FACTION_EMOJIS_FINAL.get(mission_faction, FALLBACK_EMOJI)

            # Получаем timestamp
            timestamp = mission.get('TargetTimestamp', mission.get('StartTimestamp', current_time))

            # Форматируем дату и время (день.месяц в (часы:минуты))
            dt = datetime.fromtimestamp(timestamp, timezone(timedelta(hours=3)))  # МСК время
            date_str = dt.strftime("%d.%m")
            time_str = dt.strftime("%H:%M")

            # Определяем, активна ли миссия сейчас
            is_mission_active = mission.get('IsActive', False)

            if is_mission_active:
                time_display = f"**СЕЙЧАС**\n(завершится <t:{int(timestamp)}:R>)"
            else:
                # Форматируем вывод как "15.12 в (7:00)"
                time_display = f"**{date_str}** в **({time_str})**\n(<t:{int(timestamp)}:R>)"

            field_value = (
                f":bell:   **{node_name_only}**\n"
                f"{time_display}"
            )
            embed.add_field(name=field_name, value=field_value, inline=True)
        else:
            # Если тир не найден
            embed.add_field(name=field_name, value="Нет в расписании", inline=True)

    embed.set_footer(text=f"Обновлено: {time.strftime('%H:%M:%S')} | Данные: browse.wf/arbys | Время: МСК (UTC+3)")

    # Проверяем, нужно ли обновлять
    if not await channel_cache.should_update_channel("arbitration", embed):
        return

    lfg_view = ArbitrationLfgView(current_arb)

    await send_or_edit_message('LAST_ARBITRATION_MESSAGE_ID', arb_channel, embed, content=content_to_send, view=lfg_view)

async def update_normal_fissure_channel(bot: commands.Bot):
    """Обновляет канал с Обычными Разрывами только при изменениях."""
    fissure_id = CONFIG.get('FISSURE_CHANNEL_ID')
    if not fissure_id:
        return

    fissure_channel = bot.get_channel(fissure_id)
    if not fissure_channel:
        return

    data = CURRENT_MISSION_STATE
    normal_fissures = data.get("Fissures", [])

    # Если нет разрывов, НЕ обновляем
    if len(normal_fissures) == 0:
        return

    normal_content = format_fissure_list_vertical(normal_fissures)

    fields = split_fissures_into_fields(normal_content)

    title_text = "      ✦✦✦ РАЗРЫВЫ БЕЗДНЫ ✦✦✦      "

    embed = discord.Embed(
        title=title_text,
        color=0x00CCFF
    )

    for name, value in fields:
        embed.add_field(name=name, value=value, inline=False)

    embed.set_footer(text=f"Обновлено: {time.strftime('%H:%M:%S')} | Данные: browse.wf")

    # Проверяем, нужно ли обновлять
    if not await channel_cache.should_update_channel("fissure", embed):
        return

    lfg_view = FissureSelectView(normal_fissures, is_steel_path=False)

    await send_or_edit_message('LAST_NORMAL_MESSAGE_ID', fissure_channel, embed, view=lfg_view)

async def update_steel_path_channel(bot: commands.Bot):
    """Обновляет канал с Разрывами Пути Стали только при изменениях."""
    sp_fissure_id = CONFIG.get('STEEL_PATH_CHANNEL_ID')
    if not sp_fissure_id:
        return

    sp_channel = bot.get_channel(sp_fissure_id)
    if not sp_channel:
        return

    data = CURRENT_MISSION_STATE
    steel_fissures = data.get("SteelPathFissures", [])

    # Если нет разрывов, НЕ обновляем
    if len(steel_fissures) == 0:
        return

    steel_content = format_fissure_list_vertical(steel_fissures)

    fields = split_fissures_into_fields(steel_content)

    sp_emoji = RESOLVED_EMOJIS.get(EMOJI_NAMES.get(SP_EMOJI_KEY), "💀")
    title_text = f"      {sp_emoji} ✦✦✦ РАЗРЫВЫ СТАЛЬНОГО ПУТИ ✦✦✦      "

    embed = discord.Embed(
        title=title_text,
        color=0x00CCFF
    )

    for name, value in fields:
        embed.add_field(name=name, value=value, inline=False)

    embed.set_footer(text=f"Обновлено: {time.strftime('%H:%M:%S')} | Данные: browse.wf")

    # Проверяем, нужно ли обновлять
    if not await channel_cache.should_update_channel("steel_path", embed):
        return

    lfg_view = FissureSelectView(steel_fissures, is_steel_path=True)

    await send_or_edit_message('LAST_STEEL_MESSAGE_ID', sp_channel, embed, view=lfg_view)

# =================================================================
# 9. ОСНОВНОЙ КОД БОТА И КОМАНДЫ
# =================================================================

intents = discord.Intents.default()
intents.emojis_and_stickers = True
intents.message_content = True
intents.guilds = True

bot = commands.Bot(command_prefix='!', intents=intents)

# Удаляем стандартную команду help, чтобы использовать свою
bot.remove_command('help')

@tasks.loop(seconds=MISSION_UPDATE_INTERVAL_SECONDS)
async def mission_update_task():
    """Задача для периодического обновления Discord-сообщений при обнаружении изменений."""

    if LAST_SCRAPE_TIME > 0:
        global LAST_CHANGES, CHANGES_LOCK

        changes_to_process = {}
        with CHANGES_LOCK:
            changes_to_process = LAST_CHANGES.copy()
            for key in LAST_CHANGES:
                LAST_CHANGES[key] = False

        if changes_to_process.get("ArbitrationSchedule"):
            print(f"[{time.strftime('%H:%M:%S')}] 📢 Обновление канала арбитража (обнаружены изменения)...")
            await update_arbitration_channel(bot)

        if changes_to_process.get("Fissures"):
            print(f"[{time.strftime('%H:%M:%S')}] 📢 Обновление канала обычных разрывов (обнаружены изменения)...")
            await update_normal_fissure_channel(bot)

        if changes_to_process.get("SteelPathFissures"):
            print(f"[{time.strftime('%H:%M:%S')}] 📢 Обновление канала разрывов стального пути (обнаружены изменения)...")
            await update_steel_path_channel(bot)
    else:
        print(f"[{time.strftime('%H:%M:%S')}] ⏳ Ожидание первого успешного скрапинга...")

@tasks.loop(seconds=30)
async def update_monitoring_task():
    """Задача для периодического обновления мониторинга."""
    await update_log_message(bot)

@bot.event
async def on_ready():
    print(f'Бот готов: {bot.user}')
    print(f'Render URL: {RENDER_URL}')

    resolve_custom_emojis(bot)

    # Запускаем HTTP сервер для health check и авто-пинга
    try:
        await health_server.start()
        print("✅ Health сервер запущен")
    except Exception as e:
        print(f"❌ Ошибка запуска health сервера: {e}")

    # Запускаем непрерывный скрапинг в фоне
    asyncio.create_task(continuous_scraping())

    # Запускаем задачу мониторинга
    if not update_monitoring_task.is_running():
        update_monitoring_task.start()

    # Запускаем задачу проверки изменений
    if not mission_update_task.is_running():
        mission_update_task.start()

    # Отправляем сообщение в канал логов если он настроен
    log_channel_id = CONFIG.get('LOG_CHANNEL_ID')
    if log_channel_id:
        log_channel = bot.get_channel(log_channel_id)
        if log_channel:
            embed = discord.Embed(
                title="🟢 Бот запущен",
                description=f"Бот **{bot.user}** успешно запущен в оптимизированном режиме.",
                color=0x00FF00,
                timestamp=datetime.now(timezone.utc)
            )
            embed.add_field(name="🆔 ID бота", value=f"`{bot.user.id}`", inline=True)
            embed.add_field(name="🏓 Пинг", value=f"`{round(bot.latency * 1000)}ms`", inline=True)
            embed.add_field(name="📊 Серверов", value=f"`{len(bot.guilds)}`", inline=True)
            embed.add_field(name="👥 Пользователей", value=f"`{len(bot.users)}`", inline=True)
            embed.add_field(name="⚡ Режим", value="Непрерывный скрапинг", inline=False)
            embed.add_field(name="🌐 Render URL", value=RENDER_URL if RENDER_URL else "Не настроен", inline=False)
            embed.add_field(name="🔄 Авто-пинг", value="Включен (каждые 5 минут)", inline=False)
            embed.set_footer(text="Система мониторинга Warframe LFG Bot")
            await log_channel.send(embed=embed)

# =================================================================
# 10. КОМАНДЫ БОТА (ОПТИМИЗИРОВАННЫЕ)
# =================================================================

@bot.command(name='command', aliases=['commands', 'help'])
async def command_list(ctx):
    """Показывает список всех доступных команд бота."""
    embed = discord.Embed(
        title="📚 Список команд бота",
        description="Все доступные команды для управления ботом",
        color=0x00CCFF
    )

    # Настройка каналов
    embed.add_field(
        name="⚙️ Настройка каналов (требует прав управлять сервером)",
        value=(
            "`!set_arbitration_channel` - Установить текущий канал для Арбитражей\n"
            "`!set_normal_ruptures` - Установить канал для обычных Разрывов\n"
            "`!set_steel_path_ruptures` - Установить канал для Разрывов Стального Пути\n"
            "`!set_lfg_channel [канал]` - Установить канал для поиска пати (LFG)\n"
            "`!set_log_channel [канал]` - Установить канал для логов и мониторинга"
        ),
        inline=False
    )

    # Настройка ролей
    embed.add_field(
        name="👥 Настройка ролей (требует прав управлять сервером)",
        value=(
            "`!set_arbitrage_role @роль` - Установить роль для пинга арбитража\n"
            "`!set_cascade_role @роль` - Установить роль для пинга каскада\n"
            "`!set_map_role название_карты @роль` - Установить роль для конкретной карты"
        ),
        inline=False
    )

    # Информационные команды
    embed.add_field(
        name="📊 Информационные команды",
        value=(
            "`!status` - Показать состояние бота и настройки\n"
            "`!command` или `!commands` или `!help` - Показать этот список команд\n"
            "`!force_update` - Принудительно обновить все каналы\n"
            "`!ping_self` - Пингнуть себя для предотвращения сна (Render.com)\n"
        ),
        inline=False
    )

    # Общие команды
    embed.add_field(
        name="🎮 Команды для всех пользователей",
        value=(
            "• Нажмите кнопки под сообщениями в каналах:\n"
            "  - **Арбитраж**: 'Создать пати на Арбитраж' или 'На текущий арбитраж'\n"
            "  - **Разрывы**: Выберите миссию из выпадающего списка\n"
            "• В тикете LFG можно:\n"
            "  - Занять свободные слоты\n"
            "  - Добавить комментарий\n"
            "  - Покинуть слот\n"
            "  - Закрыть тикет (только создатель)"
        ),
        inline=False
    )

    embed.set_footer(text=f"Бот автоматически обновляет данные каждые {SCRAPE_INTERVAL_SECONDS} секунд")

    await ctx.send(embed=embed)

@bot.command(name='set_arbitration_channel')
@commands.has_permissions(manage_guild=True)
async def set_arbitration_channel(ctx):
    """Устанавливает текущий канал как канал Расписания Арбитражей."""
    CONFIG['ARBITRATION_CHANNEL_ID'] = ctx.channel.id
    save_config()

    await update_arbitration_channel(bot)
    await ctx.send(f"✅ Канал **Расписания Арбитражей** установлен на: {ctx.channel.mention} и запущен.", delete_after=10)

@bot.command(name='set_normal_ruptures')
@commands.has_permissions(manage_guild=True)
async def set_normal_fissure_channel(ctx):
    """Устанавливает текущий канал как канал Обычных Разрывов."""
    CONFIG['FISSURE_CHANNEL_ID'] = ctx.channel.id
    save_config()

    await update_normal_fissure_channel(bot)
    await ctx.send(f"✅ Канал **Обычных Разрывов** установлен на: {ctx.channel.mention} и запущен.", delete_after=10)

@bot.command(name='set_steel_path_ruptures')
@commands.has_permissions(manage_guild=True)
async def set_steel_path_channel(ctx):
    """Устанавливает текущий канал как канал Разрывов Пути Стали."""
    CONFIG['STEEL_PATH_CHANNEL_ID'] = ctx.channel.id
    save_config()

    await update_steel_path_channel(bot)
    await ctx.send(f"✅ Канал **Разрывов Пути Стали** установлен на: {ctx.channel.mention} и запущен.", delete_after=10)

@bot.command(name='set_lfg_channel')
@commands.has_permissions(manage_guild=True)
async def set_lfg_channel(ctx, channel: discord.TextChannel = None):
    """Устанавливает канал для поиска пати (LFG)."""
    if channel is None:
        channel = ctx.channel

    CONFIG['LFG_CHANNEL_ID'] = channel.id
    save_config()

    await ctx.send(f"✅ Канал **поиска пати (LFG)** установлен на: {channel.mention}", delete_after=10)

@bot.command(name='set_arbitrage_role')
@commands.has_permissions(manage_guild=True)
async def set_arbitrage_role(ctx, role: discord.Role):
    """Устанавливает роль для пинга арбитража."""
    CONFIG['ARBITRAGE_ROLE_ID'] = role.id
    save_config()
    await ctx.send(f"✅ Роль для арбитража установлена: {role.mention}", delete_after=10)

@bot.command(name='set_cascade_role')
@commands.has_permissions(manage_guild=True)
async def set_cascade_role(ctx, role: discord.Role):
    """Устанавливает роль для пинга каскада."""
    CONFIG['CASCAD_ROLE_ID'] = role.id
    save_config()
    await ctx.send(f"✅ Роль для каскада установлена: {role.mention}", delete_after=10)

@bot.command(name='set_map_role')
@commands.has_permissions(manage_guild=True)
async def set_map_role(ctx, map_name: str, role: discord.Role):
    """Устанавливает роль для конкретной карты."""
    if 'MAP_ROLES' not in CONFIG:
        CONFIG['MAP_ROLES'] = {}

    CONFIG['MAP_ROLES'][map_name] = role.id
    save_config()
    await ctx.send(f"✅ Роль для карты **{map_name}** установлена: {role.mention}", delete_after=10)

@bot.command(name='set_log_channel')
@commands.has_permissions(manage_guild=True)
async def set_log_channel(ctx, channel: discord.TextChannel = None):
    """Устанавливает канал для логов и мониторинга."""
    if channel is None:
        channel = ctx.channel

    CONFIG['LOG_CHANNEL_ID'] = channel.id
    save_config()

    # Отправляем начальное сообщение
    embed = discord.Embed(
        title="📊 Система мониторинга бота",
        description="Этот канал предназначен для логов и мониторинга состояния бота.",
        color=0x00FF00,
        timestamp=datetime.now(timezone.utc)
    )

    embed.add_field(name="🟢 Статус", value="Бот активен", inline=True)
    embed.add_field(name="🕒 Последнее обновление", value=f"<t:{int(time.time())}:R>", inline=True)
    embed.add_field(name="📈 Производительность", value="Нормальная", inline=True)

    # Формируем информацию о настройках
    settings_info = []

    arb_channel = CONFIG.get('ARBITRATION_CHANNEL_ID')
    fissure_channel = CONFIG.get('FISSURE_CHANNEL_ID')
    sp_channel = CONFIG.get('STEEL_PATH_CHANNEL_ID')
    lfg_channel = CONFIG.get('LFG_CHANNEL_ID')

    if arb_channel:
        settings_info.append(f"**Канал арбитража:** <#{arb_channel}>")
    else:
        settings_info.append("**Канал арбитража:** ❌ Не настроен")

    if fissure_channel:
        settings_info.append(f"**Канал разрывов:** <#{fissure_channel}>")
    else:
        settings_info.append("**Канал разрывов:** ❌ Не настроен")

    if sp_channel:
        settings_info.append(f"**Канал SP:** <#{sp_channel}>")
    else:
        settings_info.append("**Канал SP:** ❌ Не настроен")

    if lfg_channel:
        settings_info.append(f"**Канал LFG:** <#{lfg_channel}>")
    else:
        settings_info.append("**Канал LFG:** ❌ Не настроен")

    embed.add_field(
        name="🔧 Настройки",
        value="\n".join(settings_info),
        inline=False
    )

    embed.set_footer(text="Система мониторинга Warframe LFG Bot")

    await channel.send(embed=embed)
    await ctx.send(f"✅ Канал **логов и мониторинга** установлен на: {channel.mention}", delete_after=10)

@bot.command(name='status')
@commands.has_permissions(manage_guild=True)
async def status_command(ctx):
    """Показывает текущее состояние бота."""
    embed = discord.Embed(
        title="📊 Состояние бота",
        color=0x00FF00,
        timestamp=datetime.now(timezone.utc)
    )

    # Информация о скрапинге
    last_scrape_time = datetime.fromtimestamp(LAST_SCRAPE_TIME, timezone.utc) if LAST_SCRAPE_TIME > 0 else None
    scrape_info = ""
    if LAST_SCRAPE_TIME > 0:
        scrape_info = f"**Последний скрапинг:** <t:{int(LAST_SCRAPE_TIME)}:R>\n"
    else:
        scrape_info = "**Последний скрапинг:** Никогда\n"

    scrape_info += f"**Интервал скрапинга:** {SCRAPE_INTERVAL_SECONDS} секунд\n"
    scrape_info += f"**Интервал обновления:** {MISSION_UPDATE_INTERVAL_SECONDS} секунд\n"
    scrape_info += f"**Cache hits:** {SCRAPE_STATS['cache_hits']}\n"
    scrape_info += f"**Cache misses:** {SCRAPE_STATS['cache_misses']}"

    embed.add_field(name="🔄 Скрапинг", value=scrape_info, inline=False)

    # Текущие данные
    data_info = f"**Арбитраж:** {CURRENT_MISSION_STATE.get('ArbitrationSchedule', {}).get('Current', {}).get('Tier', 'N/A')}\n"
    data_info += f"**Обычные разрывы:** {len(CURRENT_MISSION_STATE.get('Fissures', []))}\n"
    data_info += f"**Разрывы SP:** {len(CURRENT_MISSION_STATE.get('SteelPathFissures', []))}\n"
    data_info += f"**Render URL:** {RENDER_URL if RENDER_URL else 'Не настроен'}"

    embed.add_field(name="📊 Данные", value=data_info, inline=False)

    # Настройки каналов
    channels_info = []
    for key, name in [
        ('ARBITRATION_CHANNEL_ID', 'Арбитраж'),
        ('FISSURE_CHANNEL_ID', 'Разрывы'),
        ('STEEL_PATH_CHANNEL_ID', 'Разрывы SP'),
        ('LFG_CHANNEL_ID', 'LFG'),
        ('LOG_CHANNEL_ID', 'Логи')
    ]:
        channel_id = CONFIG.get(key)
        if channel_id:
            channels_info.append(f"**{name}:** <#{channel_id}>")
        else:
            channels_info.append(f"**{name}:** ❌ Не настроен")

    embed.add_field(name="⚙️ Настройки", value="\n".join(channels_info), inline=False)

    # Производительность
    embed.add_field(name="📈 Производительность", value=f"**Пинг:** `{round(bot.latency * 1000)}ms`\n**Серверов:** `{len(bot.guilds)}`\n**Пользователей:** `{len(bot.users)}`", inline=False)

    embed.set_footer(text=f"Запущен: {datetime.fromtimestamp(bot.user.created_at.timestamp()).strftime('%Y-%m-%d %H:%M:%S')}")

    await ctx.send(embed=embed)

@bot.command(name='force_update')
@commands.has_permissions(manage_guild=True)
async def force_update(ctx):
    """Принудительно обновляет все каналы."""
    await ctx.send("🔄 Принудительное обновление всех каналов...", delete_after=5)

    await update_arbitration_channel(bot)
    await update_normal_fissure_channel(bot)
    await update_steel_path_channel(bot)

    await ctx.send("✅ Все каналы обновлены!", delete_after=5)

@bot.command(name='ping_self')
@commands.has_permissions(manage_guild=True)
async def ping_self_command(ctx):
    """Пингнуть самого себя для предотвращения сна на Render.com."""
    if not RENDER_URL:
        await ctx.send("❌ RENDER_URL не настроен в переменных окружения!", delete_after=10)
        return
    
    await ctx.send("🔄 Пингую самого себя...", delete_after=5)
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f'{RENDER_URL}/ping-self', timeout=10) as response:
                if response.status == 200:
                    data = await response.json()
                    last_ping = health_server.last_ping_time
                    if last_ping:
                        last_ping_str = last_ping.strftime('%H:%M:%S')
                        await ctx.send(f"✅ Успешный пинг! Ответ: {data.get('message', 'OK')}\nПоследний пинг: {last_ping_str}", delete_after=10)
                    else:
                        await ctx.send(f"✅ Успешный пинг! Ответ: {data.get('message', 'OK')}", delete_after=10)
                else:
                    await ctx.send(f"❌ Ошибка пинга: Status {response.status}", delete_after=10)
    except Exception as e:
        await ctx.send(f"❌ Ошибка при пинге: {str(e)}", delete_after=10)

if __name__ == '__main__':
    print(f"[{time.strftime('%H:%M:%S')}] Запуск бота...")
    print(f"[{time.strftime('%H:%M:%S')}] Render URL: {RENDER_URL}")
    
    try:
        bot.run(BOT_TOKEN)
    except discord.errors.LoginFailure:
        print("\n\n-- ОШИБКА АВТОРИЗАЦИИ --")
        print("Проверьте, правильно ли вы вставили BOT_TOKEN!")
        print("Убедитесь, что переменная BOT_TOKEN установлена в переменных окружения.")
    except Exception as e:
        print(f"Произошла ошибка при запуске бота: {e}")
#[file content end]