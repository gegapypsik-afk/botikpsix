# -*- coding: utf-8 -*-
"""
Дискорд-бот с системой тикетов (на русском языке).

Возможности:
- Система тикетов с категориями и логами.
- Тикеты видят только настроенные роли (поддержка / администрация).
- При создании тикета создаётся отдельный канал с панелью управления
  (кнопки «Принять тикет», «Закрыть тикет», «Закрыть с причиной»).
- Настройки тикетов: категория Discord для каналов тикетов, канал логов,
  канал с панелью создания тикетов.
- Настройки панели создания тикетов (заголовок и описание).
- 4 категории тикетов по умолчанию (редактируются: текст, эмодзи, описание).
- Панель администрации: настройки + статистика.
- Настройка роли администрации командой !роль_админ (упоминание роли или ID).
- Модерация: баны, разбаны, кики, муты (тайм-ауты) и размуты.
- Автомод: авто-мут за спам/флуд и за приглашения в Discord (ссылки discord.gg).
- Красивая панель настроек автомода с кнопками-переключателями.
- ИИ-собеседник: если упомянуть бота (@), он отвечает как ролевой персонаж,
  который шарит за мемы и интернет-культуру (через OpenAI-совместимый API).
  Отвечает с упоминанием автора и умеет тегать участников (@ник → реальный пинг).
- Выбор модели ИИ под цель: !ии_модель <чат|кодинг|картинка> <модель>.
- ИИ-команды: !код (помощь с кодом) и !картинка (генерация изображений).
- Команда !скажи — отправить сообщение от лица бота (поддержка/администрация).
- Прогноз погоды по городам командой !погода (бесплатный Open-Meteo, без ключа).
- Развлечения и утилиты: !мем, !аватар, !юзер, !сервер, !кости, !шар, !выбери.
- Все команды через префикс "!" и на русском языке.
- Токен читается из переменной окружения DISCORD_TOKEN (или из config.json).

Требования: Python 3.9+, discord.py >= 2.3.2, aiohttp
"""

import io
import re
import json
import os
import copy
import time
import base64
import random
import asyncio
from typing import Optional, Tuple
from datetime import datetime, timezone, timedelta

import aiohttp
import discord
from discord.ext import commands

# Проверка PyNaCl для голосовых каналов
try:
    import nacl
except ImportError:
    print("WARNING: PyNaCl not installed - voice features will not work")

# ---------------------------------------------------------------------------
# Загрузка конфигурации
# ---------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")

# Каталог для постоянных данных (data.json). На хостингах вроде Railway можно
# подключить том (volume) и указать DATA_DIR на него, чтобы настройки не
# терялись при передеплое.
DATA_DIR = os.environ.get("DATA_DIR", BASE_DIR)
os.makedirs(DATA_DIR, exist_ok=True)
DATA_FILE = os.path.join(DATA_DIR, "data.json")

# Конфигурация может приходить из переменных окружения (рекомендуется для
# хостинга) или из локального файла config.json (удобно при разработке).
CONFIG = {}
if os.path.exists(CONFIG_FILE):
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        CONFIG = json.load(f)

TOKEN = os.environ.get("DISCORD_TOKEN") or os.environ.get("TOKEN") or CONFIG.get("token", "")
PREFIX = os.environ.get("PREFIX") or CONFIG.get("prefix", "!")

if not TOKEN:
    raise SystemExit(
        "❌ Не задан токен бота. Установите переменную окружения DISCORD_TOKEN "
        "или добавьте токен в config.json (см. config.example.json)."
    )

# ---------------------------------------------------------------------------
# Оформление — единая фиолетовая тема
# ---------------------------------------------------------------------------
# Название и ссылка бренда для подписи в эмбедах (можно переопределить через env).
BRAND_NAME = os.environ.get("BRAND_NAME") or CONFIG.get("brand_name", "yooma.su")
BRAND_URL = os.environ.get("BRAND_URL") or CONFIG.get("brand_url", "")

# ---------------------------------------------------------------------------
# Настройки валюты для системы уровней
# ---------------------------------------------------------------------------
CURRENCY_EMOJI = os.environ.get("CURRENCY_EMOJI") or CONFIG.get("currency_emoji", "💰")
CURRENCY_NAME = os.environ.get("CURRENCY_NAME") or CONFIG.get("currency_name", "монеты")
CURRENCY_PLURAL = os.environ.get("CURRENCY_PLURAL") or CONFIG.get("currency_plural", "монет")


# ---------------------------------------------------------------------------
# Настройки ИИ-собеседника (OpenAI-совместимый API)
# ---------------------------------------------------------------------------
# Ключ и адрес API берутся из переменных окружения (рекомендуется) либо из
# config.json. Подойдёт любой OpenAI-совместимый провайдер: OpenAI, Groq,
# OpenRouter, Together и т.п. — достаточно поменять AI_BASE_URL и AI_MODEL.
AI_API_KEY = (
    os.environ.get("AI_API_KEY")
    or os.environ.get("OPENAI_API_KEY")
    or CONFIG.get("ai_api_key", "")
)
AI_BASE_URL = (
    os.environ.get("AI_BASE_URL")
    or CONFIG.get("ai_base_url", "https://api.openai.com/v1")
).rstrip("/")
AI_MODEL = os.environ.get("AI_MODEL") or CONFIG.get("ai_model", "gpt-4o-mini")
# Модели по умолчанию под конкретные цели. Если не заданы — берётся AI_MODEL
# (а для картинок команда попросит указать модель). На сервере их можно
# переопределить командой !ии_модель <цель> <модель>.
AI_MODEL_CODING = os.environ.get("AI_MODEL_CODING") or CONFIG.get("ai_model_coding", "")
AI_IMAGE_MODEL = os.environ.get("AI_IMAGE_MODEL") or CONFIG.get("ai_image_model", "")
# Имя персонажа по умолчанию (можно переопределить на сервере командой !ии_имя).
AI_PERSONA_NAME = os.environ.get("AI_BOT_NAME") or CONFIG.get("ai_bot_name", "Ботя")

# Общий HTTP-клиент для запросов к ИИ и к API погоды/мемов.
_http_session: "aiohttp.ClientSession | None" = None


async def get_http_session() -> aiohttp.ClientSession:
    """Ленивая инициализация общего aiohttp-клиента."""
    global _http_session
    if _http_session is None or _http_session.closed:
        _http_session = aiohttp.ClientSession()
    return _http_session


class Colors:
    """Фиолетовая палитра проекта (оттенки одной гаммы для узнаваемого стиля)."""
    PRIMARY = discord.Color(0x8B5CF6)   # основной фиолетовый — панели, инфо
    LIGHT = discord.Color(0xA78BFA)     # светлый акцент — успех/подтверждение
    DEEP = discord.Color(0x6D28D9)      # глубокий фиолетовый — логи, закрытие
    ACCENT = discord.Color(0xC026D3)    # фуксия — предупреждения/автомод
    DANGER = discord.Color(0xE11D48)    # тревожный — баны/серьёзные наказания


def brand(embed: discord.Embed, guild: discord.Guild = None) -> discord.Embed:
    """Единое оформление эмбеда: подпись бренда и иконка сервера как миниатюра."""
    if not embed.footer or not embed.footer.text:
        embed.set_footer(text=f"{BRAND_NAME} • тикет-система")
    if guild is not None and guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    return embed


# ---------------------------------------------------------------------------
# Хранилище настроек (JSON-файл data.json)
# ---------------------------------------------------------------------------
DEFAULT_CATEGORIES = {
    "custom_role": {
        "label": "Заявка на кастом. роль",
        "emoji": "🎭",
        "description": "Запросить индивидуальную (кастомную) роль",
    },
    "promocode": {
        "label": "Заявка на раздачу промокода",
        "emoji": "🎁",
        "description": "Организовать раздачу промокода",
    },
    "moderation": {
        "label": "Заявка на модерацию",
        "emoji": "🛡️",
        "description": "Подать заявку в модерацию",
    },
    "purchase": {
        "label": "Покупка товара",
        "emoji": "🛒",
        "description": "Оформить покупку товара",
    },
    "giveaway": {
        "label": "Организовать розыгрыш",
        "emoji": "🎉",
        "description": "Запросить организацию розыгрыша",
    },
    "other": {
        "label": "Другое",
        "emoji": "❓",
        "description": "Другой вопрос или обращение",
    },
}

# Настройки автомода по умолчанию
DEFAULT_AUTOMOD = {
    "enabled": True,             # общий выключатель автомода
    "spam_enabled": True,        # анти-спам
    "spam_count": 5,             # столько сообщений...
    "spam_interval": 5,          # ...за столько секунд = спам
    "spam_mute_minutes": 10,     # мут за спам (минут)
    "invite_enabled": True,      # анти-приглашения Discord
    "invite_mute_minutes": 30,   # мут за приглашение (минут)
    "exempt_roles": [],          # роли с иммунитетом к автомоду
}

# Настройки ИИ-собеседника по умолчанию (на каждый сервер)
DEFAULT_AI = {
    "enabled": True,     # отвечает ли бот на упоминания как ИИ
    "persona": None,     # имя/описание персонажа для role play (None = по умолчанию)
    # Модели под конкретные цели. None = использовать значение из окружения
    # (AI_MODEL для чата/кода, AI_IMAGE_MODEL для картинок).
    "models": {
        "chat": None,       # ответы на упоминания (болтовня, role play)
        "coding": None,     # команда !код — помощь с программированием
        "image": None,      # команда !картинка — генерация изображений
    },
}

# Настройки приватных (временных) голосовых комнат по умолчанию (на каждый сервер).
# Идея «join-to-create»: пользователь заходит в канал-создатель, бот делает ему
# личную голосовую комнату, а панель в текстовом канале-интерфейсе позволяет
# владельцу управлять ей (лимит, замок, приватность, кик, право говорить и т.п.).
DEFAULT_PRIVATE_ROOMS = {
    "enabled": True,
    "creator_channel_id": None,    # голосовой канал «Создать комнату» (join-to-create)
    "category_id": None,           # категория Discord, куда создаются комнаты
    "interface_channel_id": None,  # текстовый канал с панелью управления
    "default_limit": 0,            # лимит участников по умолчанию (0 = без лимита)
    "name_template": "Комната {user}",  # шаблон имени комнаты ({user} = ник владельца)
    "active_rooms": {},            # {channel_id: {"owner_id", "locked", "hidden", "created_at"}}
}


# Настройки уровней и валюты по умолчанию (на каждый сервер)
DEFAULT_LEVELS = {
    "enabled": True,                # включена ли система уровней
    "currency_emoji": CURRENCY_EMOJI,  # эмодзи валюты
    "currency_name": CURRENCY_NAME,    # название валюты
    "currency_plural": CURRENCY_PLURAL,  # название во множественном числе
    "xp_per_message": 5,            # XP за сообщение
    "xp_per_image": 10,             # XP за сообщение с картинкой
    "xp_per_reaction": 2,           # XP за реакцию
    "cooldown_seconds": 60,         # антифлуд XP (не начислять чаще)
    "level_up_messages": True,      # показывать сообщение о повышении уровня
    "leaderboard_channel_id": None, # канал для лидерборда
    "user_xp": {},                  # {user_id: {"xp": int, "level": int, "last_xp_at": ts}}
    "custom_emojis": {},            # {name: emoji_string}
}

# Настройки розыгрышей по умолчанию (на каждый сервер)
DEFAULT_GIVEAWAYS = {
    "enabled": True,
    "giveaways": {},  # {msg_id: {prize, end_time, conditions, description, participants, ended}}
    "stats": {"created": 0, "ended": 0},
}

# Роль "лучшие раздатели" (ID: 1531958288109797456)
BEST_DISTRIBUTOR_ROLE_ID = 1531958288109797456


def _default_guild():
    return {
        "admin_roles": [],            # роли администрации
        "support_roles": [],          # роли поддержки (видят тикеты)
        "ticket_category_id": None,   # категория Discord для каналов тикетов
        "log_channel_id": None,       # канал логов
        "panel_channel_id": None,     # канал, где размещается панель создания тикетов
        "modlog_channel_id": None,    # канал логов модерации (по умолчанию = канал логов)
        "panel_title": "🎫 Создание тикета",
        "panel_description": (
            "Выберите нужную категорию в меню ниже, чтобы открыть тикет.\n"
            "Сотрудники ответят вам в созданном канале."
        ),
        "categories": copy.deepcopy(DEFAULT_CATEGORIES),
        # Какие категории по умолчанию уже «подсеяны» на сервер. Нужно, чтобы
        # новые дефолтные категории появлялись при обновлении бота, но при этом
        # удалённые вручную категории не воскресали.
        "category_seed": sorted(DEFAULT_CATEGORIES.keys()),
        "ticket_counter": 0,
        "stats": {"created": 0, "closed": 0, "accepted": 0, "by_category": {}},
        "open_tickets": {},           # {channel_id: {...}}
        "automod": copy.deepcopy(DEFAULT_AUTOMOD),
        "ai": copy.deepcopy(DEFAULT_AI),
        "private_rooms": copy.deepcopy(DEFAULT_PRIVATE_ROOMS),  # приватные голосовые комнаты
        "warns": {},                  # предупреждения: {user_id: [{mod_id, reason, ts}, ...]}
        "levels": copy.deepcopy(DEFAULT_LEVELS),  # система уровней и валюты
        "giveaways": copy.deepcopy(DEFAULT_GIVEAWAYS),  # система розыгрышей
    }


class Storage:
    def __init__(self, path):
        self.path = path
        self.data = {"guilds": {}}
        self.load()

    def load(self):
        if os.path.exists(self.path):
            with open(self.path, "r", encoding="utf-8") as fp:
                self.data = json.load(fp)
        if "guilds" not in self.data:
            self.data["guilds"] = {}

    def save(self):
        with open(self.path, "w", encoding="utf-8") as fp:
            json.dump(self.data, fp, ensure_ascii=False, indent=2)

    def guild(self, guild_id):
        gid = str(guild_id)
        g = self.data["guilds"].get(gid)
        if g is None:
            g = _default_guild()
            self.data["guilds"][gid] = g
            self.save()
            return g
        # мягкая миграция — добавляем недостающие ключи.
        # "category_seed" обрабатывается отдельно ниже: его нельзя копировать из
        # дефолта напрямую, иначе новые категории посчитаются уже подсеянными.
        base = _default_guild()
        for k, v in base.items():
            if k == "category_seed":
                continue
            if k not in g:
                g[k] = v
        for k, v in base["stats"].items():
            if k not in g["stats"]:
                g["stats"][k] = v
        for k, v in base["automod"].items():
            if k not in g["automod"]:
                g["automod"][k] = v
        for k, v in base["ai"].items():
            if k not in g["ai"]:
                g["ai"][k] = v
        # приватные комнаты — добавляем секцию и недостающие ключи при обновлении
        if not isinstance(g.get("private_rooms"), dict):
            g["private_rooms"] = copy.deepcopy(base["private_rooms"])
        else:
            for k, v in base["private_rooms"].items():
                g["private_rooms"].setdefault(k, copy.deepcopy(v) if isinstance(v, (dict, list)) else v)
        # вложенный словарь моделей ИИ — добавляем недостающие цели
        if not isinstance(g["ai"].get("models"), dict):
            g["ai"]["models"] = copy.deepcopy(base["ai"]["models"])
        else:
            for mk, mv in base["ai"]["models"].items():
                g["ai"]["models"].setdefault(mk, mv)
        # подсев новых категорий по умолчанию (без воскрешения удалённых вручную)
        seeded = g.setdefault("category_seed", list(g.get("categories", {}).keys()))
        for key, cat in DEFAULT_CATEGORIES.items():
            if key not in seeded:
                g.setdefault("categories", {}).setdefault(key, copy.deepcopy(cat))
                seeded.append(key)
        return g


storage = Storage(DATA_FILE)


# Модерация: регулярки и разбор длительности
# ---------------------------------------------------------------------------
# Ссылки-приглашения в Discord (discord.gg/..., discord.com/invite/..., и т.п.)
INVITE_RE = re.compile(
    r"(?:https?://)?(?:www\.)?"
    r"(?:discord(?:app)?\.com/invite|discord\.gg|discord\.me|dsc\.gg)/\S+",
    re.IGNORECASE,
)

# Единицы времени для мута: латиница и кириллица
_DURATION_UNITS = {
    "s": 1, "с": 1,
    "m": 60, "м": 60,
    "h": 3600, "ч": 3600,
    "d": 86400, "д": 86400,
}
MAX_TIMEOUT_SECONDS = 28 * 24 * 3600  # ограничение Discord на тайм-аут — 28 дней


def parse_duration(text: str):
    """'10m' / '2ч' / '30с' / '1d' -> количество секунд. Без единицы = минуты."""
    if not text:
        return None
    m = re.fullmatch(r"\s*(\d+)\s*([a-zA-Zа-яА-Я]*)\s*", text)
    if not m:
        return None
    value = int(m.group(1))
    unit = (m.group(2) or "m")[:1].lower()
    factor = _DURATION_UNITS.get(unit)
    if factor is None:
        return None
    return value * factor


def format_duration(seconds: int) -> str:
    """Секунды -> человекочитаемая строка на русском."""
    seconds = int(seconds)
    parts = []
    for unit, label in ((86400, "д"), (3600, "ч"), (60, "мин"), (1, "сек")):
        if seconds >= unit:
            qty, seconds = divmod(seconds, unit)
            parts.append(f"{qty} {label}")
    return " ".join(parts) if parts else "0 сек"


# ---------------------------------------------------------------------------
# Система уровней и XP
# ---------------------------------------------------------------------------
async def grant_xp(message: discord.Message):
    """Начислить XP за сообщение."""
    if message.author.bot or message.guild is None:
        return

    gdata = storage.guild(message.guild.id)
    levels = gdata.get("levels", {})

    if not levels.get("enabled", True):
        return

    # Проверяем иммунитет (админы, поддержка)
    if is_admin_member(message.author, gdata) or is_support_member(message.author, gdata):
        return

    # Проверяем кулдаун
    user_xp = levels.setdefault("user_xp", {})
    user_id = str(message.author.id)
    user_data = user_xp.setdefault(user_id, {"xp": 0, "level": 1, "last_xp_at": 0, "messages": 0})

    now = time.time()
    cooldown = levels.get("cooldown_seconds", 60)
    if now - user_data.get("last_xp_at", 0) < cooldown:
        return

    # Начисляем XP
    xp_per_message = levels.get("xp_per_message", 5)
    if message.attachments:
        xp_per_message += levels.get("xp_per_image", 10)

    user_data["xp"] = user_data.get("xp", 0) + xp_per_message
    user_data["last_xp_at"] = now
    user_data["messages"] = user_data.get("messages", 0) + 1

    # Проверяем повышение уровня
    current_level = user_data.get("level", 1)
    xp_needed = current_level * 100

    if user_data["xp"] >= xp_needed:
        user_data["level"] = current_level + 1
        user_data["xp"] = user_data["xp"] - xp_needed  # перенос остатка XP

        # Уведомление о повышении уровня
        if levels.get("level_up_messages", True):
            emoji = levels.get("currency_emoji", CURRENCY_EMOJI)
            embed = discord.Embed(
                title="🎉 Новый уровень!",
                description=f"{message.author.mention} достиг **уровня {user_data['level']}**!",
                color=Colors.LIGHT,
                timestamp=datetime.now(timezone.utc),
            )
            embed.add_field(name="Всего XP", value=str(user_data["xp"] + xp_needed), inline=True)
            embed.add_field(name="Валюта", value=f"{emoji} {levels.get('currency_name', CURRENCY_NAME)}", inline=True)
            embed.set_thumbnail(url=message.author.display_avatar.url)
            brand(embed, message.guild)

            # Отправляем в канал лидерборда если есть, иначе в общий лог
            lb_channel_id = levels.get("leaderboard_channel_id")
            if lb_channel_id:
                channel = message.guild.get_channel(lb_channel_id)
                if channel:
                    try:
                        await channel.send(embed=embed)
                    except discord.HTTPException:
                        pass

    storage.save()


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------
def is_admin_member(member: discord.Member, gdata: dict) -> bool:
    """Является ли участник администратором бота (права сервера или роль админа)."""
    if member.guild_permissions.administrator or member.guild_permissions.manage_guild:
        return True
    return any(r.id in gdata["admin_roles"] for r in member.roles)


def is_support_member(member: discord.Member, gdata: dict) -> bool:
    """Является ли участник сотрудником поддержки (или администратором)."""
    if is_admin_member(member, gdata):
        return True
    return any(r.id in gdata["support_roles"] for r in member.roles)


def parse_emoji(value):
    if not value:
        return None
    try:
        return discord.PartialEmoji.from_str(value)
    except Exception:
        return None


def _clip(text, length):
    if not text:
        return None
    text = str(text)
    return text[:length] if len(text) > length else text


def settings_embed(gdata: dict, guild: discord.Guild) -> discord.Embed:
    embed = discord.Embed(title="⚙️ Настройки тикет-системы", color=Colors.PRIMARY)

    def roles(ids):
        return ", ".join(f"<@&{r}>" for r in ids) if ids else "не заданы"

    def channel(cid):
        return f"<#{cid}>" if cid else "не задан"

    embed.add_field(name="Роли администрации", value=roles(gdata["admin_roles"]), inline=False)
    embed.add_field(name="Роли поддержки", value=roles(gdata["support_roles"]), inline=False)

    cat = guild.get_channel(gdata["ticket_category_id"]) if gdata["ticket_category_id"] else None
    embed.add_field(name="Категория для тикетов", value=(cat.name if cat else "не задана"), inline=True)
    embed.add_field(name="Канал логов", value=channel(gdata["log_channel_id"]), inline=True)
    embed.add_field(name="Канал панели", value=channel(gdata["panel_channel_id"]), inline=True)

    lines = []
    for key, c in gdata["categories"].items():
        lines.append(f"{c.get('emoji') or '•'} **{c['label']}** (`{key}`)")
    embed.add_field(name="Категории тикетов", value="\n".join(lines) or "—", inline=False)
    embed.set_footer(text=f"{BRAND_NAME} • команда !помощь — список всех команд")
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    return embed


def stats_embed(gdata: dict) -> discord.Embed:
    s = gdata["stats"]
    embed = discord.Embed(title="📊 Статистика тикетов", color=Colors.LIGHT)
    embed.add_field(name="Создано всего", value=str(s["created"]))
    embed.add_field(name="Закрыто", value=str(s["closed"]))
    embed.add_field(name="Принято", value=str(s["accepted"]))
    embed.add_field(name="Открыто сейчас", value=str(len(gdata["open_tickets"])))
    lines = []
    for key, c in gdata["categories"].items():
        lines.append(f"{c.get('emoji') or '•'} {c['label']}: **{s['by_category'].get(key, 0)}**")
    embed.add_field(name="По категориям", value="\n".join(lines) or "—", inline=False)
    brand(embed)
    return embed


async def log_event(guild: discord.Guild, gdata: dict, description: str, file: discord.File = None):
    cid = gdata.get("log_channel_id")
    if not cid:
        return
    ch = guild.get_channel(cid)
    if not ch:
        return
    embed = discord.Embed(description=description, color=Colors.DEEP,
                          timestamp=datetime.now(timezone.utc))
    embed.set_author(name="Журнал тикетов")
    try:
        if file:
            await ch.send(embed=embed, file=file)
        else:
            await ch.send(embed=embed)
    except discord.HTTPException:
        pass


async def mod_log(guild: discord.Guild, gdata: dict, embed: discord.Embed):
    """Отправить запись в канал модерации (или в общий канал логов)."""
    cid = gdata.get("modlog_channel_id") or gdata.get("log_channel_id")
    if not cid:
        return
    ch = guild.get_channel(cid)
    if not ch:
        return
    try:
        await ch.send(embed=embed)
    except discord.HTTPException:
        pass


def automod_embed(gdata: dict) -> discord.Embed:
    am = gdata["automod"]
    on = "🟢 включён"
    off = "🔴 выключен"
    embed = discord.Embed(
        title="🤖 Настройки автомода",
        description=f"Состояние: **{on if am['enabled'] else off}**",
        color=Colors.PRIMARY if am["enabled"] else Colors.DEEP,
    )
    embed.add_field(
        name="🚫 Анти-спам",
        value=(f"{'🟢' if am['spam_enabled'] else '🔴'} "
               f"{am['spam_count']} сообщ. за {am['spam_interval']} сек → "
               f"мут {format_duration(am['spam_mute_minutes'] * 60)}"),
        inline=False,
    )
    embed.add_field(
        name="🔗 Анти-приглашения Discord",
        value=(f"{'🟢' if am['invite_enabled'] else '🔴'} "
               f"ссылки discord.gg → мут {format_duration(am['invite_mute_minutes'] * 60)}"),
        inline=False,
    )
    roles = ", ".join(f"<@&{r}>" for r in am["exempt_roles"]) if am["exempt_roles"] else "нет"
    embed.add_field(name="🛡️ Иммунитет (роли)", value=roles, inline=False)
    embed.set_footer(text="Иммунитет также есть у поддержки/администрации и у ролей с правом «Управление сообщениями».")
    return embed


async def build_transcript(channel: discord.TextChannel) -> str:
    lines = []
    async for msg in channel.history(limit=500, oldest_first=True):
        ts = msg.created_at.strftime("%Y-%m-%d %H:%M:%S")
        content = msg.content or ""
        if msg.attachments:
            content += " " + " ".join(a.url for a in msg.attachments)
        if not content and msg.embeds:
            content = "[встроенное сообщение]"
        lines.append(f"[{ts}] {msg.author}: {content}")
    return "\n".join(lines) if lines else "В тикете не было сообщений."


# ---------------------------------------------------------------------------
# Панель создания тикетов (выпадающее меню категорий)
# ---------------------------------------------------------------------------
class TicketSelect(discord.ui.Select):
    def __init__(self, gdata: dict = None):
        options = []
        if gdata:
            for key, cat in gdata["categories"].items():
                options.append(
                    discord.SelectOption(
                        label=_clip(cat["label"], 100),
                        value=key,
                        emoji=parse_emoji(cat.get("emoji")),
                        description=_clip(cat.get("description"), 100),
                    )
                )
        if not options:
            options = [discord.SelectOption(label="—", value="__none__")]
        super().__init__(
            placeholder="Выберите категорию тикета...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="ticket_panel_select",
        )

    async def callback(self, interaction: discord.Interaction):
        await handle_ticket_create(interaction, self.values[0])


class TicketPanelView(discord.ui.View):
    def __init__(self, gdata: dict = None):
        super().__init__(timeout=None)
        self.add_item(TicketSelect(gdata))


async def post_ticket_panel(channel: discord.TextChannel, gdata: dict):
    embed = discord.Embed(
        title=gdata["panel_title"],
        description=gdata["panel_description"],
        color=Colors.PRIMARY,
    )
    brand(embed, channel.guild)
    await channel.send(embed=embed, view=TicketPanelView(gdata))


# ---------------------------------------------------------------------------
# Создание тикета
# ---------------------------------------------------------------------------
async def handle_ticket_create(interaction: discord.Interaction, key: str):
    guild = interaction.guild
    gdata = storage.guild(guild.id)

    if key not in gdata["categories"]:
        await interaction.response.send_message("❌ Эта категория недоступна.", ephemeral=True)
        return
    cat = gdata["categories"][key]

    if not gdata["ticket_category_id"]:
        await interaction.response.send_message(
            "⚠️ Категория для тикетов ещё не настроена. Администратор должен выполнить "
            "команду `!категория_тикетов <ID категории>`.",
            ephemeral=True,
        )
        return

    discord_category = guild.get_channel(gdata["ticket_category_id"])
    if not isinstance(discord_category, discord.CategoryChannel):
        await interaction.response.send_message(
            "⚠️ Настроенная категория Discord не найдена. Обратитесь к администратору.",
            ephemeral=True,
        )
        return

    # Запрет на дубли одной категории от одного пользователя
    for cid, info in gdata["open_tickets"].items():
        if info["user_id"] == interaction.user.id and info["category"] == key:
            await interaction.response.send_message(
                f"У вас уже открыт тикет этой категории: <#{cid}>", ephemeral=True
            )
            return

    await interaction.response.defer(ephemeral=True)

    gdata["ticket_counter"] += 1
    number = gdata["ticket_counter"]

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me: discord.PermissionOverwrite(
            view_channel=True, send_messages=True, manage_channels=True, read_message_history=True
        ),
        interaction.user: discord.PermissionOverwrite(
            view_channel=True, send_messages=True, read_message_history=True,
            attach_files=True, embed_links=True,
        ),
    }
    for rid in set(gdata["support_roles"]) | set(gdata["admin_roles"]):
        role = guild.get_role(rid)
        if role:
            overwrites[role] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True, manage_messages=True
            )

    try:
        channel = await guild.create_text_channel(
            name=f"тикет-{number:04d}",
            category=discord_category,
            overwrites=overwrites,
            topic=f"Тикет #{number} • {cat['label']} • автор: {interaction.user}",
            reason=f"Создан тикет пользователем {interaction.user}",
        )
    except discord.Forbidden:
        await interaction.followup.send(
            "❌ У бота нет прав на создание каналов. Проверьте права роли бота.", ephemeral=True
        )
        return

    gdata["open_tickets"][str(channel.id)] = {
        "user_id": interaction.user.id,
        "category": key,
        "claimed_by": None,
        "number": number,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    gdata["stats"]["created"] += 1
    gdata["stats"]["by_category"][key] = gdata["stats"]["by_category"].get(key, 0) + 1
    storage.save()

    embed = discord.Embed(
        title=f"🎫 Тикет #{number:04d} — {cat['label']}",
        description=(
            "Опишите ваш вопрос как можно подробнее — сотрудник скоро ответит.\n\n"
            "Управляйте тикетом с помощью кнопок ниже."
        ),
        color=Colors.LIGHT,
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="Автор", value=interaction.user.mention)
    embed.add_field(name="Категория", value=cat["label"])
    brand(embed, guild)

    support_mentions = " ".join(f"<@&{rid}>" for rid in gdata["support_roles"])
    content = f"{interaction.user.mention} {support_mentions}".strip()

    await channel.send(content=content, embed=embed, view=TicketControlView())
    await log_event(
        guild, gdata,
        f"📥 Создан тикет **#{number:04d}** ({cat['label']}) — {interaction.user.mention} — {channel.mention}",
    )
    await interaction.followup.send(f"✅ Ваш тикет создан: {channel.mention}", ephemeral=True)


# ---------------------------------------------------------------------------
# Закрытие / принятие тикета
# ---------------------------------------------------------------------------
def can_manage_ticket(member: discord.Member, gdata: dict, channel_id: int) -> bool:
    if is_support_member(member, gdata):
        return True
    info = gdata["open_tickets"].get(str(channel_id))
    return bool(info and info["user_id"] == member.id)


async def claim_ticket(interaction: discord.Interaction):
    gdata = storage.guild(interaction.guild.id)
    if not is_support_member(interaction.user, gdata):
        await interaction.response.send_message("⛔ Принимать тикеты может только поддержка.", ephemeral=True)
        return
    info = gdata["open_tickets"].get(str(interaction.channel.id))
    if info is None:
        await interaction.response.send_message("Это не активный тикет.", ephemeral=True)
        return
    if info.get("claimed_by"):
        await interaction.response.send_message(
            f"Тикет уже принят пользователем <@{info['claimed_by']}>.", ephemeral=True
        )
        return
    info["claimed_by"] = interaction.user.id
    gdata["stats"]["accepted"] += 1
    storage.save()
    await interaction.response.send_message(f"✅ {interaction.user.mention} принял этот тикет.")
    await log_event(
        interaction.guild, gdata,
        f"🙋 Тикет **#{info['number']:04d}** принят — {interaction.user.mention} — {interaction.channel.mention}",
    )


async def close_ticket(channel: discord.TextChannel, closed_by: discord.Member, reason: str = None):
    gdata = storage.guild(channel.guild.id)
    info = gdata["open_tickets"].get(str(channel.id))

    transcript = await build_transcript(channel)
    file = discord.File(io.BytesIO(transcript.encode("utf-8")), filename=f"{channel.name}.txt")

    number = info["number"] if info else "?"
    category_label = "—"
    opener = "—"
    if info:
        category_label = gdata["categories"].get(info["category"], {}).get("label", info["category"])
        opener = f"<@{info['user_id']}>"

    embed = discord.Embed(title=f"🔒 Тикет #{number if number=='?' else f'{number:04d}'} закрыт",
                          color=Colors.DEEP, timestamp=datetime.now(timezone.utc))
    embed.add_field(name="Категория", value=category_label)
    embed.add_field(name="Автор", value=opener)
    embed.add_field(name="Закрыл", value=closed_by.mention)
    if info and info.get("claimed_by"):
        embed.add_field(name="Принимал", value=f"<@{info['claimed_by']}>")
    if reason:
        embed.add_field(name="Причина", value=reason, inline=False)

    log_ch = channel.guild.get_channel(gdata["log_channel_id"]) if gdata["log_channel_id"] else None
    if log_ch:
        try:
            await log_ch.send(embed=embed, file=file)
        except discord.HTTPException:
            pass

    gdata["stats"]["closed"] += 1
    gdata["open_tickets"].pop(str(channel.id), None)
    storage.save()

    try:
        await channel.send("🔒 Тикет закрывается через 5 секунд...")
        await asyncio.sleep(5)
        await channel.delete(reason=f"Тикет закрыт: {closed_by}")
    except discord.HTTPException:
        pass


class CloseReasonModal(discord.ui.Modal, title="Закрытие тикета"):
    reason = discord.ui.TextInput(
        label="Причина закрытия",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=500,
        placeholder="Необязательно: укажите причину закрытия тикета",
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.send_message("🔒 Закрываю тикет...", ephemeral=True)
        await close_ticket(interaction.channel, interaction.user, str(self.reason.value) or None)


# ---------------------------------------------------------------------------
# Панель управления внутри тикета (кнопки в канале тикета)
# ---------------------------------------------------------------------------
class TicketControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Принять тикет", style=discord.ButtonStyle.success,
                       emoji="✅", custom_id="ticket_claim")
    async def claim(self, interaction: discord.Interaction, button: discord.ui.Button):
        await claim_ticket(interaction)

    @discord.ui.button(label="Закрыть тикет", style=discord.ButtonStyle.danger,
                       emoji="🔒", custom_id="ticket_close")
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        gdata = storage.guild(interaction.guild.id)
        if not can_manage_ticket(interaction.user, gdata, interaction.channel.id):
            await interaction.response.send_message("⛔ У вас нет прав закрывать этот тикет.", ephemeral=True)
            return
        await interaction.response.send_message("🔒 Закрываю тикет...", ephemeral=True)
        await close_ticket(interaction.channel, interaction.user)

    @discord.ui.button(label="Закрыть с причиной", style=discord.ButtonStyle.secondary,
                       emoji="📝", custom_id="ticket_close_reason")
    async def close_reason(self, interaction: discord.Interaction, button: discord.ui.Button):
        gdata = storage.guild(interaction.guild.id)
        if not can_manage_ticket(interaction.user, gdata, interaction.channel.id):
            await interaction.response.send_message("⛔ У вас нет прав закрывать этот тикет.", ephemeral=True)
            return
        await interaction.response.send_modal(CloseReasonModal())


# ---------------------------------------------------------------------------
# Панель администрации (настройки + статистика)
# ---------------------------------------------------------------------------
class PanelTextModal(discord.ui.Modal, title="Настройка панели тикетов"):
    def __init__(self, gdata: dict):
        super().__init__()
        self.gdata = gdata
        self.title_input = discord.ui.TextInput(
            label="Заголовок панели", default=gdata["panel_title"], max_length=256
        )
        self.desc_input = discord.ui.TextInput(
            label="Описание панели", style=discord.TextStyle.paragraph,
            default=gdata["panel_description"], max_length=2000,
        )
        self.add_item(self.title_input)
        self.add_item(self.desc_input)

    async def on_submit(self, interaction: discord.Interaction):
        self.gdata["panel_title"] = str(self.title_input.value)
        self.gdata["panel_description"] = str(self.desc_input.value)
        storage.save()
        await interaction.response.send_message(
            "✅ Текст панели обновлён. Нажмите «Отправить панель тикетов», чтобы обновить сообщение.",
            ephemeral=True,
        )


class CategoryEditModal(discord.ui.Modal, title="Изменение категории тикета"):
    def __init__(self, key: str, gdata: dict):
        super().__init__()
        self.key = key
        self.gdata = gdata
        cat = gdata["categories"][key]
        self.label_input = discord.ui.TextInput(label="Название", default=cat["label"], max_length=100)
        self.emoji_input = discord.ui.TextInput(
            label="Эмодзи", default=cat.get("emoji") or "", required=False, max_length=100
        )
        self.desc_input = discord.ui.TextInput(
            label="Описание", default=cat.get("description") or "", required=False,
            style=discord.TextStyle.paragraph, max_length=100,
        )
        self.add_item(self.label_input)
        self.add_item(self.emoji_input)
        self.add_item(self.desc_input)

    async def on_submit(self, interaction: discord.Interaction):
        cat = self.gdata["categories"][self.key]
        cat["label"] = str(self.label_input.value)
        cat["emoji"] = str(self.emoji_input.value) or None
        cat["description"] = str(self.desc_input.value) or None
        storage.save()
        await interaction.response.send_message(
            "✅ Категория обновлена. Нажмите «Отправить панель тикетов», чтобы применить изменения.",
            ephemeral=True,
        )


class CategoryManageSelect(discord.ui.Select):
    def __init__(self, gdata: dict):
        self.gdata = gdata
        options = [
            discord.SelectOption(
                label=_clip(cat["label"], 100), value=key, emoji=parse_emoji(cat.get("emoji"))
            )
            for key, cat in gdata["categories"].items()
        ]
        super().__init__(placeholder="Выберите категорию для изменения...", options=options)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(CategoryEditModal(self.values[0], self.gdata))


class CategoryManageView(discord.ui.View):
    def __init__(self, gdata: dict):
        super().__init__(timeout=180)
        self.add_item(CategoryManageSelect(gdata))


class AdminPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def _guard(self, interaction: discord.Interaction) -> bool:
        gdata = storage.guild(interaction.guild.id)
        if not is_admin_member(interaction.user, gdata):
            await interaction.response.send_message("⛔ Доступ только для администрации.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Статистика", style=discord.ButtonStyle.success,
                       emoji="📊", custom_id="admin_stats")
    async def stats_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._guard(interaction):
            return
        gdata = storage.guild(interaction.guild.id)
        await interaction.response.send_message(embed=stats_embed(gdata), ephemeral=True)

    @discord.ui.button(label="Настройки", style=discord.ButtonStyle.primary,
                       emoji="⚙️", custom_id="admin_settings")
    async def settings_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._guard(interaction):
            return
        gdata = storage.guild(interaction.guild.id)
        await interaction.response.send_message(
            embed=settings_embed(gdata, interaction.guild), ephemeral=True
        )

    @discord.ui.button(label="Текст панели", style=discord.ButtonStyle.secondary,
                       emoji="✏️", custom_id="admin_panel_text")
    async def panel_text_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._guard(interaction):
            return
        gdata = storage.guild(interaction.guild.id)
        await interaction.response.send_modal(PanelTextModal(gdata))

    @discord.ui.button(label="Категории", style=discord.ButtonStyle.secondary,
                       emoji="🗂️", custom_id="admin_categories")
    async def categories_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._guard(interaction):
            return
        gdata = storage.guild(interaction.guild.id)
        await interaction.response.send_message(
            "Выберите категорию, которую хотите изменить:",
            view=CategoryManageView(gdata), ephemeral=True,
        )

    @discord.ui.button(label="Автомод", style=discord.ButtonStyle.secondary,
                       emoji="🤖", custom_id="admin_automod")
    async def automod_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._guard(interaction):
            return
        gdata = storage.guild(interaction.guild.id)
        await interaction.response.send_message(
            embed=automod_embed(gdata), view=AutomodPanelView(gdata), ephemeral=True
        )

    @discord.ui.button(label="Отправить панель тикетов", style=discord.ButtonStyle.secondary,
                       emoji="📮", custom_id="admin_send_panel", row=1)
    async def send_panel_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._guard(interaction):
            return
        gdata = storage.guild(interaction.guild.id)
        target_id = gdata.get("panel_channel_id")
        channel = interaction.guild.get_channel(target_id) if target_id else interaction.channel
        await post_ticket_panel(channel, gdata)
        await interaction.response.send_message(f"✅ Панель тикетов отправлена в {channel.mention}.", ephemeral=True)

    @discord.ui.button(label="Логи модерации", style=discord.ButtonStyle.secondary,
                       emoji="📝", custom_id="admin_modlog", row=1)
    async def modlog_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._guard(interaction):
            return
        gdata = storage.guild(interaction.guild.id)
        cid = gdata.get("modlog_channel_id") or gdata.get("log_channel_id")
        channel = interaction.guild.get_channel(cid) if cid else None
        embed = discord.Embed(
            title="📝 Логи модерации",
            description=(
                f"**Канал:** {channel.mention if channel else 'не задан'}\n\n"
                "Команды модерации:\n"
                "`!бан @участник [причина]` — забанить\n"
                "`!кик @участник [причина]` — кикнуть\n"
                "`!мут @участник <время> [причина]` — мут\n"
                "`!размут @участник [причина]` — размут\n"
                "`!варн @участник [причина]` — предупреждение\n"
                "`!список_банов` — список забаненных\n"
                "`!список_мутов` — список мутов"
            ),
            color=Colors.PRIMARY,
        )
        brand(embed, interaction.guild)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="Система уровней", style=discord.ButtonStyle.secondary,
                       emoji="📊", custom_id="admin_levels", row=1)
    async def levels_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._guard(interaction):
            return
        gdata = storage.guild(interaction.guild.id)
        levels = gdata.get("levels", {})
        emoji = levels.get("currency_emoji", CURRENCY_EMOJI)
        name = levels.get("currency_name", CURRENCY_NAME)
        enabled = levels.get("enabled", True)

        embed = discord.Embed(
            title=f"💰 Система уровней",
            description=f"**Статус:** {'🟢 включена' if enabled else '🔴 выключена'}",
            color=Colors.PRIMARY,
        )
        embed.add_field(name="Эмодзи", value=emoji, inline=True)
        embed.add_field(name="Название", value=name, inline=True)
        embed.add_field(name="XP за сообщение", value=levels.get("xp_per_message", 5), inline=True)
        embed.add_field(name="Кулдаун", value=f"{levels.get('cooldown_seconds', 60)} сек", inline=True)
        embed.add_field(name="Участников с XP", value=len(levels.get("user_xp", {})), inline=True)
        embed.add_field(name="Канал лидерборда", value=f"<#{levels.get('leaderboard_channel_id', 0)}>" if levels.get("leaderboard_channel_id") else "не задан", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)


# ---------------------------------------------------------------------------
# Панель настроек автомода (интерактивная, с переключателями)
# ---------------------------------------------------------------------------
class AutomodThresholdModal(discord.ui.Modal, title="Пороги автомода"):
    def __init__(self, gdata: dict, panel: "AutomodPanelView" = None):
        super().__init__()
        self.gdata = gdata
        self.panel = panel
        am = gdata["automod"]
        self.spam_count = discord.ui.TextInput(
            label="Сообщений для спама", default=str(am["spam_count"]), max_length=3
        )
        self.spam_interval = discord.ui.TextInput(
            label="За сколько секунд", default=str(am["spam_interval"]), max_length=4
        )
        self.spam_mute = discord.ui.TextInput(
            label="Мут за спам (минут)", default=str(am["spam_mute_minutes"]), max_length=5
        )
        self.invite_mute = discord.ui.TextInput(
            label="Мут за ссылки (минут)", default=str(am["invite_mute_minutes"]), max_length=5
        )
        self.add_item(self.spam_count)
        self.add_item(self.spam_interval)
        self.add_item(self.spam_mute)
        self.add_item(self.invite_mute)

    async def on_submit(self, interaction: discord.Interaction):
        am = self.gdata["automod"]
        try:
            spam_count = max(2, int(str(self.spam_count.value)))
            spam_interval = max(1, int(str(self.spam_interval.value)))
            spam_mute = min(40320, max(1, int(str(self.spam_mute.value))))
            invite_mute = min(40320, max(1, int(str(self.invite_mute.value))))
        except ValueError:
            await interaction.response.send_message(
                "❌ Значения должны быть целыми числами.", ephemeral=True
            )
            return
        am["spam_count"] = spam_count
        am["spam_interval"] = spam_interval
        am["spam_mute_minutes"] = spam_mute
        am["invite_mute_minutes"] = invite_mute
        storage.save()
        view = self.panel or AutomodPanelView(self.gdata)
        view.refresh_styles()
        await interaction.response.edit_message(embed=automod_embed(self.gdata), view=view)


class AutomodPanelView(discord.ui.View):
    def __init__(self, gdata: dict):
        super().__init__(timeout=300)
        self.gdata = gdata
        self.refresh_styles()

    def refresh_styles(self):
        am = self.gdata["automod"]

        def style(state):
            return discord.ButtonStyle.success if state else discord.ButtonStyle.danger

        self.toggle_all.label = f"Автомод: {'ВКЛ' if am['enabled'] else 'ВЫКЛ'}"
        self.toggle_all.style = style(am["enabled"])
        self.toggle_spam.label = f"Анти-спам: {'ВКЛ' if am['spam_enabled'] else 'ВЫКЛ'}"
        self.toggle_spam.style = style(am["spam_enabled"])
        self.toggle_invite.label = f"Анти-ссылки: {'ВКЛ' if am['invite_enabled'] else 'ВЫКЛ'}"
        self.toggle_invite.style = style(am["invite_enabled"])

    async def _guard(self, interaction: discord.Interaction) -> bool:
        gdata = storage.guild(interaction.guild.id)
        if not is_admin_member(interaction.user, gdata):
            await interaction.response.send_message("⛔ Доступ только для администрации.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Автомод", row=0)
    async def toggle_all(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._guard(interaction):
            return
        self.gdata["automod"]["enabled"] = not self.gdata["automod"]["enabled"]
        storage.save()
        self.refresh_styles()
        await interaction.response.edit_message(embed=automod_embed(self.gdata), view=self)

    @discord.ui.button(label="Анти-спам", row=0)
    async def toggle_spam(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._guard(interaction):
            return
        self.gdata["automod"]["spam_enabled"] = not self.gdata["automod"]["spam_enabled"]
        storage.save()
        self.refresh_styles()
        await interaction.response.edit_message(embed=automod_embed(self.gdata), view=self)

    @discord.ui.button(label="Анти-ссылки", row=0)
    async def toggle_invite(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._guard(interaction):
            return
        self.gdata["automod"]["invite_enabled"] = not self.gdata["automod"]["invite_enabled"]
        storage.save()
        self.refresh_styles()
        await interaction.response.edit_message(embed=automod_embed(self.gdata), view=self)

    @discord.ui.button(label="Настроить пороги", emoji="🎚️",
                       style=discord.ButtonStyle.primary, row=1)
    async def thresholds(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._guard(interaction):
            return
        await interaction.response.send_modal(AutomodThresholdModal(self.gdata, self))


# ---------------------------------------------------------------------------
# Приватные (временные) голосовые комнаты — «join-to-create»
# ---------------------------------------------------------------------------
# Список популярных встроенных активностей Discord (Watch Together, игры и т.п.).
# {название: application_id}. ID могут меняться на стороне Discord — если запуск
# не удался, бот покажет понятную ошибку.
DISCORD_ACTIVITIES = {
    "🎬 Watch Together (YouTube)": 880218394199220334,
    "♟️ Chess in the Park": 832012774040141894,
    "🔴 Checkers in the Park": 832013003968348200,
    "🃏 Poker Night": 755827207812677713,
    "🎴 Blazing 8s": 832025144389533716,
    "✏️ Sketch Heads": 902271654783242291,
    "🔤 Word Snacks": 879863976006127627,
    "⛳ Putt Party": 945737671223947305,
    "🌍 Land-io": 903769130790969345,
    "😹 Know What I Meme": 950505761862189096,
}


def pr_room_name(template: str, member: discord.Member) -> str:
    """Имя комнаты по шаблону ({user} → отображаемое имя владельца)."""
    display = member.display_name
    try:
        name = (template or "Комната {user}").format(user=display)
    except (KeyError, IndexError, ValueError):
        name = f"Комната {display}"
    return _clip(name, 100) or f"Комната {display}"


def find_owned_room(guild: discord.Guild, gdata: dict, user_id: int):
    """Возвращает (канал, инфо) активной комнаты владельца или (None, None).

    Попутно вычищает «протухшие» записи о комнатах, которых уже нет на сервере.
    """
    pr = gdata["private_rooms"]
    rooms = pr.get("active_rooms", {})
    stale = []
    found = (None, None)
    for cid, info in list(rooms.items()):
        channel = guild.get_channel(int(cid))
        if channel is None:
            stale.append(cid)
            continue
        if info.get("owner_id") == user_id:
            found = (channel, info)
    if stale:
        for cid in stale:
            rooms.pop(cid, None)
        storage.save()
    return found


async def create_private_room(member: discord.Member, gdata: dict) -> Optional[discord.VoiceChannel]:
    """Создаёт личную голосовую комнату для участника и перемещает его в неё."""
    guild = member.guild
    pr = gdata["private_rooms"]

    category = guild.get_channel(pr.get("category_id")) if pr.get("category_id") else None
    if not isinstance(category, discord.CategoryChannel):
        creator = guild.get_channel(pr.get("creator_channel_id")) if pr.get("creator_channel_id") else None
        category = creator.category if isinstance(creator, discord.VoiceChannel) else None

    overwrites = {
        member: discord.PermissionOverwrite(
            view_channel=True, connect=True, speak=True,
            manage_channels=True, move_members=True,
        ),
        guild.me: discord.PermissionOverwrite(
            view_channel=True, connect=True, speak=True,
            manage_channels=True, move_members=True,
        ),
    }

    limit = int(pr.get("default_limit") or 0)
    channel = await guild.create_voice_channel(
        name=pr_room_name(pr.get("name_template"), member),
        category=category,
        user_limit=limit,
        overwrites=overwrites,
        reason=f"Приватная комната для {member}",
    )

    pr.setdefault("active_rooms", {})[str(channel.id)] = {
        "owner_id": member.id,
        "locked": False,
        "hidden": False,
        "speak_restricted": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    storage.save()

    try:
        await member.move_to(channel, reason="Перемещение в свою приватную комнату")
    except discord.HTTPException:
        pass
    return channel


async def delete_private_room(channel: discord.abc.GuildChannel, gdata: dict):
    """Удаляет пустую комнату и убирает её из хранилища."""
    gdata["private_rooms"].get("active_rooms", {}).pop(str(channel.id), None)
    storage.save()
    try:
        await channel.delete(reason="Приватная комната опустела")
    except discord.HTTPException:
        pass


def private_rooms_panel_embed(guild: discord.Guild) -> discord.Embed:
    embed = discord.Embed(
        title="⚙️ Приватные комнаты",
        description=(
            "Измените конфигурацию вашей комнаты с помощью панели управления.\n"
            "👑 назначить нового создателя комнаты\n"
            "🔐 управление доступом к комнате\n"
            "👥 задать новый лимит участников\n"
            "🔒 закрыть/открыть комнату\n"
            "✏️ изменить название комнаты\n"
            "👁️ скрыть/открыть комнату\n"
            "👢 выгнать участника из комнаты\n"
            "🎤 ограничить/выдать право говорить"
        ),
        color=Colors.PRIMARY,
    )
    brand(embed, guild)
    return embed


def private_rooms_settings_embed(gdata: dict, guild: discord.Guild) -> discord.Embed:
    pr = gdata["private_rooms"]
    embed = discord.Embed(title="🔊 Настройки приватных комнат", color=Colors.PRIMARY)

    def chan(cid, prefix="#"):
        if not cid:
            return "не задан"
        ch = guild.get_channel(cid)
        return ch.mention if ch else f"({cid} — не найден)"

    cat = guild.get_channel(pr.get("category_id")) if pr.get("category_id") else None
    embed.add_field(name="Статус", value=("🟢 включены" if pr.get("enabled") else "🔴 выключены"), inline=True)
    embed.add_field(name="Лимит по умолчанию",
                    value=("без лимита" if not pr.get("default_limit") else str(pr["default_limit"])), inline=True)
    embed.add_field(name="Активных комнат", value=str(len(pr.get("active_rooms", {}))), inline=True)
    embed.add_field(name="Канал-создатель", value=chan(pr.get("creator_channel_id")), inline=False)
    embed.add_field(name="Категория для комнат", value=(cat.name if cat else "не задана"), inline=True)
    embed.add_field(name="Канал-интерфейс", value=chan(pr.get("interface_channel_id")), inline=True)
    embed.add_field(name="Шаблон имени", value=f"`{pr.get('name_template') or 'Комната {user}'}`", inline=False)
    embed.set_footer(text=f"{BRAND_NAME} • !приватки_настройка — авто-создание всех каналов")
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    return embed


async def post_private_rooms_panel(channel: discord.abc.Messageable, guild: discord.Guild):
    await channel.send(embed=private_rooms_panel_embed(guild), view=PrivateRoomInterfaceView())


def _room_owner_channel(interaction: discord.Interaction):
    """Комната, которой владеет нажавший кнопку (или None)."""
    gdata = storage.guild(interaction.guild.id)
    channel, info = find_owned_room(interaction.guild, gdata, interaction.user.id)
    return gdata, channel, info


# ---- Модальные окна управления комнатой -----------------------------------
class RoomLimitModal(discord.ui.Modal, title="Лимит участников"):
    def __init__(self, channel: discord.VoiceChannel):
        super().__init__()
        self.channel = channel
        self.limit_input = discord.ui.TextInput(
            label="Лимит участников (0 = без лимита)",
            placeholder="например: 5",
            default=str(channel.user_limit or 0),
            max_length=2,
        )
        self.add_item(self.limit_input)

    async def on_submit(self, interaction: discord.Interaction):
        raw = str(self.limit_input.value).strip()
        if not raw.isdigit():
            await interaction.response.send_message("❌ Введите число от 0 до 99.", ephemeral=True)
            return
        limit = max(0, min(99, int(raw)))
        try:
            await self.channel.edit(user_limit=limit, reason="Изменение лимита приватной комнаты")
        except discord.HTTPException:
            await interaction.response.send_message("❌ Не удалось изменить лимит.", ephemeral=True)
            return
        text = "снят" if limit == 0 else f"установлен: **{limit}**"
        await interaction.response.send_message(f"✅ Лимит участников {text}.", ephemeral=True)


class RoomRenameModal(discord.ui.Modal, title="Название комнаты"):
    def __init__(self, channel: discord.VoiceChannel):
        super().__init__()
        self.channel = channel
        self.name_input = discord.ui.TextInput(
            label="Новое название комнаты",
            default=channel.name,
            max_length=100,
        )
        self.add_item(self.name_input)

    async def on_submit(self, interaction: discord.Interaction):
        new_name = str(self.name_input.value).strip() or self.channel.name
        try:
            await self.channel.edit(name=new_name[:100], reason="Переименование приватной комнаты")
        except discord.HTTPException:
            await interaction.response.send_message(
                "❌ Не удалось переименовать (возможно, слишком часто — лимит Discord).",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(f"✅ Название изменено на **{new_name[:100]}**.", ephemeral=True)


# ---- Вспомогательные вью с выбором участника ------------------------------
class _RoomUserSelect(discord.ui.UserSelect):
    def __init__(self, action, channel, placeholder):
        super().__init__(placeholder=placeholder, min_values=1, max_values=1)
        self._action = action
        self._channel = channel

    async def callback(self, interaction: discord.Interaction):
        member = interaction.guild.get_member(self.values[0].id)
        if member is None:
            await interaction.response.send_message("❌ Участник не найден на сервере.", ephemeral=True)
            return
        await self._action(interaction, self._channel, member)


class RoomUserActionView(discord.ui.View):
    def __init__(self, action, channel, placeholder):
        super().__init__(timeout=120)
        self.add_item(_RoomUserSelect(action, channel, placeholder))


async def _action_transfer(interaction: discord.Interaction, channel: discord.VoiceChannel, member: discord.Member):
    gdata = storage.guild(interaction.guild.id)
    info = gdata["private_rooms"].get("active_rooms", {}).get(str(channel.id))
    if info is None:
        await interaction.response.send_message("❌ Комната больше не активна.", ephemeral=True)
        return
    if member.bot:
        await interaction.response.send_message("❌ Нельзя назначить владельцем бота.", ephemeral=True)
        return
    if member.id == info.get("owner_id"):
        await interaction.response.send_message("ℹ️ Этот участник уже владелец комнаты.", ephemeral=True)
        return
    old_owner = interaction.guild.get_member(info.get("owner_id"))
    info["owner_id"] = member.id
    storage.save()
    try:
        await channel.set_permissions(
            member,
            overwrite=discord.PermissionOverwrite(
                view_channel=True, connect=True, speak=True,
                manage_channels=True, move_members=True,
            ),
            reason="Передача владения приватной комнатой",
        )
        if old_owner is not None:
            await channel.set_permissions(old_owner, overwrite=None,
                                          reason="Снятие прав прежнего владельца комнаты")
    except discord.HTTPException:
        pass
    await interaction.response.send_message(f"👑 Новый владелец комнаты — {member.mention}.", ephemeral=True)


async def _action_kick(interaction: discord.Interaction, channel: discord.VoiceChannel, member: discord.Member):
    if member.id == interaction.user.id:
        await interaction.response.send_message("❌ Нельзя выгнать самого себя.", ephemeral=True)
        return
    if member not in channel.members:
        await interaction.response.send_message("ℹ️ Этого участника нет в вашей комнате.", ephemeral=True)
        return
    try:
        await member.move_to(None, reason="Выгнан из приватной комнаты")
    except discord.HTTPException:
        await interaction.response.send_message("❌ Не удалось выгнать участника.", ephemeral=True)
        return
    await interaction.response.send_message(f"👢 {member.mention} выгнан из комнаты.", ephemeral=True)


async def _action_access(interaction: discord.Interaction, channel: discord.VoiceChannel, member: discord.Member):
    """Переключатель доступа: выдать или отозвать право входа участнику."""
    current = channel.overwrites_for(member)
    if current.connect:
        await channel.set_permissions(member, overwrite=None, reason="Отзыв доступа к приватной комнате")
        await interaction.response.send_message(f"🔒 Доступ для {member.mention} отозван.", ephemeral=True)
    else:
        await channel.set_permissions(
            member,
            overwrite=discord.PermissionOverwrite(view_channel=True, connect=True),
            reason="Выдача доступа к приватной комнате",
        )
        await interaction.response.send_message(f"🔓 {member.mention} получил доступ к комнате.", ephemeral=True)


# ---- Активности (Watch Together, игры) ------------------------------------
class RoomActivitySelect(discord.ui.Select):
    def __init__(self):
        options = [discord.SelectOption(label=name, value=str(app_id))
                   for name, app_id in DISCORD_ACTIVITIES.items()]
        super().__init__(
            placeholder="Выбрать активность",
            min_values=1, max_values=1,
            options=options,
            custom_id="pr_activity",
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        gdata, channel, info = _room_owner_channel(interaction)
        if channel is None:
            await interaction.response.send_message(
                "❌ Сначала создайте свою комнату (зайдите в канал-создатель).", ephemeral=True)
            return
        app_id = int(self.values[0])
        try:
            invite = await channel.create_invite(
                target_type=discord.InviteTarget.embedded_application,
                target_application_id=app_id,
                max_age=0,
                reason="Запуск активности в приватной комнате",
            )
        except discord.HTTPException:
            await interaction.response.send_message(
                "❌ Не удалось запустить активность. Убедитесь, что у бота есть право "
                "«Создавать приглашения», и попробуйте другую активность.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            f"🎮 Активность готова — присоединяйтесь: {invite.url}", ephemeral=True)


# ---- Основная панель управления комнатой (persistent) ---------------------
class PrivateRoomInterfaceView(discord.ui.View):
    """Единая панель в канале-интерфейсе. Кнопки действуют на комнату того,
    кто их нажал (владелец должен иметь активную комнату)."""

    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(RoomActivitySelect())

    async def _resolve(self, interaction: discord.Interaction):
        """Возвращает (gdata, channel, info) или None (с отправленной ошибкой)."""
        gdata, channel, info = _room_owner_channel(interaction)
        if channel is None:
            await interaction.response.send_message(
                "❌ У вас нет активной приватной комнаты. Зайдите в канал-создатель, "
                "чтобы создать её, затем управляйте ей отсюда.",
                ephemeral=True,
            )
            return None
        return gdata, channel, info

    @discord.ui.button(emoji="👑", style=discord.ButtonStyle.secondary,
                       custom_id="pr_transfer", row=1)
    async def transfer(self, interaction: discord.Interaction, button: discord.ui.Button):
        resolved = await self._resolve(interaction)
        if not resolved:
            return
        _, channel, _ = resolved
        await interaction.response.send_message(
            "Выберите нового владельца комнаты:",
            view=RoomUserActionView(_action_transfer, channel, "Новый владелец комнаты"),
            ephemeral=True,
        )

    @discord.ui.button(emoji="🔐", style=discord.ButtonStyle.secondary,
                       custom_id="pr_access", row=1)
    async def access(self, interaction: discord.Interaction, button: discord.ui.Button):
        resolved = await self._resolve(interaction)
        if not resolved:
            return
        _, channel, _ = resolved
        await interaction.response.send_message(
            "Выберите участника, чтобы выдать или отозвать доступ:",
            view=RoomUserActionView(_action_access, channel, "Управление доступом"),
            ephemeral=True,
        )

    @discord.ui.button(emoji="👥", style=discord.ButtonStyle.secondary,
                       custom_id="pr_limit", row=1)
    async def limit(self, interaction: discord.Interaction, button: discord.ui.Button):
        resolved = await self._resolve(interaction)
        if not resolved:
            return
        _, channel, _ = resolved
        await interaction.response.send_modal(RoomLimitModal(channel))

    @discord.ui.button(emoji="🔒", style=discord.ButtonStyle.secondary,
                       custom_id="pr_lock", row=1)
    async def lock(self, interaction: discord.Interaction, button: discord.ui.Button):
        resolved = await self._resolve(interaction)
        if not resolved:
            return
        gdata, channel, info = resolved
        locked = not info.get("locked", False)
        overwrite = channel.overwrites_for(interaction.guild.default_role)
        overwrite.connect = False if locked else None
        try:
            await channel.set_permissions(interaction.guild.default_role, overwrite=overwrite,
                                           reason="Закрытие/открытие приватной комнаты")
        except discord.HTTPException:
            await interaction.response.send_message("❌ Не удалось изменить доступ.", ephemeral=True)
            return
        info["locked"] = locked
        storage.save()
        msg = "🔒 Комната закрыта — новые участники не смогут войти." if locked \
            else "🔓 Комната открыта для входа."
        await interaction.response.send_message(msg, ephemeral=True)

    @discord.ui.button(emoji="✏️", style=discord.ButtonStyle.secondary,
                       custom_id="pr_rename", row=2)
    async def rename(self, interaction: discord.Interaction, button: discord.ui.Button):
        resolved = await self._resolve(interaction)
        if not resolved:
            return
        _, channel, _ = resolved
        await interaction.response.send_modal(RoomRenameModal(channel))

    @discord.ui.button(emoji="👁️", style=discord.ButtonStyle.secondary,
                       custom_id="pr_hide", row=2)
    async def hide(self, interaction: discord.Interaction, button: discord.ui.Button):
        resolved = await self._resolve(interaction)
        if not resolved:
            return
        gdata, channel, info = resolved
        hidden = not info.get("hidden", False)
        overwrite = channel.overwrites_for(interaction.guild.default_role)
        overwrite.view_channel = False if hidden else None
        try:
            await channel.set_permissions(interaction.guild.default_role, overwrite=overwrite,
                                           reason="Скрытие/открытие приватной комнаты")
        except discord.HTTPException:
            await interaction.response.send_message("❌ Не удалось изменить видимость.", ephemeral=True)
            return
        info["hidden"] = hidden
        storage.save()
        msg = "👁️ Комната скрыта — её не видно в списке каналов." if hidden \
            else "👁️ Комната снова видна всем."
        await interaction.response.send_message(msg, ephemeral=True)

    @discord.ui.button(emoji="👢", style=discord.ButtonStyle.secondary,
                       custom_id="pr_kick", row=2)
    async def kick(self, interaction: discord.Interaction, button: discord.ui.Button):
        resolved = await self._resolve(interaction)
        if not resolved:
            return
        _, channel, _ = resolved
        await interaction.response.send_message(
            "Выберите участника, которого выгнать из комнаты:",
            view=RoomUserActionView(_action_kick, channel, "Кого выгнать"),
            ephemeral=True,
        )

    @discord.ui.button(emoji="🎤", style=discord.ButtonStyle.secondary,
                       custom_id="pr_speak", row=2)
    async def speak(self, interaction: discord.Interaction, button: discord.ui.Button):
        resolved = await self._resolve(interaction)
        if not resolved:
            return
        gdata, channel, info = resolved
        restricted = not info.get("speak_restricted", False)
        default_ow = channel.overwrites_for(interaction.guild.default_role)
        default_ow.speak = False if restricted else None
        owner = interaction.guild.get_member(info.get("owner_id"))
        try:
            await channel.set_permissions(interaction.guild.default_role, overwrite=default_ow,
                                           reason="Ограничение/выдача права говорить")
            if owner is not None:
                owner_ow = channel.overwrites_for(owner)
                owner_ow.speak = True
                await channel.set_permissions(owner, overwrite=owner_ow,
                                              reason="Владелец сохраняет право говорить")
        except discord.HTTPException:
            await interaction.response.send_message("❌ Не удалось изменить право говорить.", ephemeral=True)
            return
        info["speak_restricted"] = restricted
        storage.save()
        msg = "🎤 Право говорить ограничено — говорить может только владелец." if restricted \
            else "🎤 Право говорить выдано всем участникам."
        await interaction.response.send_message(msg, ephemeral=True)


# ---------------------------------------------------------------------------
# Настройка бота и команды
# ---------------------------------------------------------------------------
intents = discord.Intents.default()
intents.message_content = True   # нужно включить в Developer Portal
intents.members = True           # нужно включить в Developer Portal
intents.voice_states = True      # нужно для приватных голосовых комнат (входит в default)


class TicketBot(commands.Bot):
    async def setup_hook(self):
        # регистрируем постоянные (persistent) панели, чтобы кнопки работали после перезапуска
        self.add_view(TicketPanelView())
        self.add_view(TicketControlView())
        self.add_view(AdminPanelView())
        self.add_view(PrivateRoomInterfaceView())

    async def close(self):
        # аккуратно закрываем общий HTTP-клиент (ИИ/погода/мемы)
        global _http_session
        if _http_session is not None and not _http_session.closed:
            await _http_session.close()
        _http_session = None
        await super().close()


bot = TicketBot(
    command_prefix=PREFIX,
    intents=intents,
    help_command=None,
    allowed_mentions=discord.AllowedMentions(everyone=False, roles=True, users=True),
)


@bot.event
async def on_ready():
    print(f"Бот запущен как {bot.user} (ID: {bot.user.id})")
    print(f"Серверов: {len(bot.guilds)}")
    # Запускаем фоновый цикл проверки розыгрышей
    bot.loop.create_task(giveaway_check_loop())
    # Очистка «протухших» приватных комнат
    for guild in bot.guilds:
        gdata = storage.guild(guild.id)
        rooms = gdata["private_rooms"].get("active_rooms", {})
        changed = False
        for cid in list(rooms.keys()):
            channel = guild.get_channel(int(cid))
            if channel is None:
                rooms.pop(cid, None)
                changed = True
            elif not channel.members:
                try:
                    await channel.delete(reason="Очистка пустой приватной комнаты при запуске")
                except discord.HTTPException:
                    pass
                rooms.pop(cid, None)
                changed = True
        if changed:
            storage.save()


def admin_only():
    async def predicate(ctx: commands.Context):
        if ctx.guild is None:
            await ctx.reply("Эта команда доступна только на сервере.")
            return False
        gdata = storage.guild(ctx.guild.id)
        if is_admin_member(ctx.author, gdata):
            return True
        await ctx.reply("⛔ У вас нет прав для этой команды.")
        return False
    return commands.check(predicate)


def staff_only():
    """Модерация: доступно поддержке и администрации."""
    async def predicate(ctx: commands.Context):
        if ctx.guild is None:
            await ctx.reply("Эта команда доступна только на сервере.")
            return False
        gdata = storage.guild(ctx.guild.id)
        if is_support_member(ctx.author, gdata):
            return True
        await ctx.reply("⛔ У вас нет прав для этой команды.")
        return False
    return commands.check(predicate)


def _moderation_block_reason(guild: discord.Guild, author: discord.Member,
                             member: discord.Member):
    """Причина, по которой нельзя наказать участника, либо None если можно.

    Вынесено отдельно, чтобы одну и ту же проверку иерархии использовали и
    текстовые команды (через can_moderate), и кнопки выбора наказания.
    """
    if member.id == author.id:
        return "❌ Нельзя применить это к самому себе."
    if member.id == guild.me.id:
        return "❌ Нельзя применить это ко мне 🙂"
    if member.id == guild.owner_id:
        return "❌ Нельзя наказать владельца сервера."
    if author.id != guild.owner_id and member.top_role >= author.top_role:
        return "❌ У этого участника роль выше или равна вашей."
    if member.top_role >= guild.me.top_role:
        return ("❌ Роль участника выше моей — не могу его наказать. "
                "Поднимите роль бота выше в настройках сервера.")
    return None


async def can_moderate(ctx, member: discord.Member) -> bool:
    """Проверка иерархии перед наказанием. Возвращает True, если можно."""
    reason = _moderation_block_reason(ctx.guild, ctx.author, member)
    if reason:
        await ctx.reply(reason)
        return False
    return True


@bot.event
async def on_ready():
    print(f"Бот запущен как {bot.user} (ID: {bot.user.id})")
    print(f"Серверов: {len(bot.guilds)}")
    # Очистка «протухших» приватных комнат: пока бот был офлайн, участники могли
    # выйти — такие пустые/удалённые комнаты убираем, чтобы не копить мусор.
    for guild in bot.guilds:
        gdata = storage.guild(guild.id)
        rooms = gdata["private_rooms"].get("active_rooms", {})
        changed = False
        for cid in list(rooms.keys()):
            channel = guild.get_channel(int(cid))
            if channel is None:
                rooms.pop(cid, None)
                changed = True
            elif not channel.members:
                try:
                    await channel.delete(reason="Очистка пустой приватной комнаты при запуске")
                except discord.HTTPException:
                    pass
                rooms.pop(cid, None)
                changed = True
        if changed:
            storage.save()


@bot.event
async def on_voice_state_update(member: discord.Member,
                                before: discord.VoiceState,
                                after: discord.VoiceState):
    """Логика «join-to-create»: вход в канал-создатель делает личную комнату,
    а выход последнего участника удаляет опустевшую комнату."""
    if member.guild is None:
        return
    gdata = storage.guild(member.guild.id)
    pr = gdata["private_rooms"]
    if not pr.get("enabled"):
        return

    # 1) Зашли в канал-создатель → создаём (или возвращаем в существующую) комнату
    creator_id = pr.get("creator_channel_id")
    if after.channel is not None and creator_id and after.channel.id == creator_id:
        existing, _ = find_owned_room(member.guild, gdata, member.id)
        try:
            if existing is not None:
                await member.move_to(existing, reason="Возврат в свою приватную комнату")
            else:
                await create_private_room(member, gdata)
        except discord.Forbidden:
            print("[Комнаты] Нет прав на создание/перемещение в голосовой канал.")
        except Exception as exc:  # noqa: BLE001 — не роняем обработчик голоса
            print(f"[Комнаты] Ошибка создания комнаты: {exc}")

    # 2) Вышли из отслеживаемой комнаты и она опустела → удаляем
    if before.channel is not None and str(before.channel.id) in pr.get("active_rooms", {}):
        if not before.channel.members:
            await delete_private_room(before.channel, gdata)


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.CheckFailure):
        return  # сообщение уже отправлено в предикате
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.reply(f"❌ Не хватает аргумента: `{error.param.name}`. Смотрите `!помощь`.")
        return
    if isinstance(error, commands.BadArgument):
        await ctx.reply("❌ Неверный аргумент. Проверьте упоминание/ID и попробуйте снова.")
        return
    if isinstance(error, commands.MemberNotFound):
        await ctx.reply("❌ Участник не найден.")
        return
    await ctx.reply(f"⚠️ Произошла ошибка: `{error}`")


# ---------------------------------------------------------------------------
# Автомод (анти-спам и анти-приглашения Discord)
# ---------------------------------------------------------------------------
# Трекер спама: (guild_id, user_id) -> список меток времени (секунды)
_spam_tracker = {}


def _is_automod_exempt(member: discord.Member, gdata: dict) -> bool:
    am = gdata["automod"]
    if member.guild_permissions.manage_messages or is_support_member(member, gdata):
        return True
    return any(r.id in am["exempt_roles"] for r in member.roles)


async def handle_automod_violation(message: discord.Message, gdata: dict,
                                   mute_minutes: int, reason: str, purge: bool):
    member = message.author
    channel = message.channel

    # 1) удаляем сообщение-нарушитель
    try:
        await message.delete()
    except discord.HTTPException:
        pass

    # 2) при спаме подчищаем последние сообщения нарушителя
    if purge:
        try:
            await channel.purge(
                limit=25, check=lambda m: m.author.id == member.id, reason="Автомод: спам"
            )
        except (discord.Forbidden, discord.HTTPException):
            pass

    # 3) выдаём мут (тайм-аут)
    seconds = min(int(mute_minutes) * 60, MAX_TIMEOUT_SECONDS)
    muted = False
    try:
        await member.timeout(timedelta(seconds=seconds), reason=f"Автомод: {reason}")
        muted = True
    except (discord.Forbidden, discord.HTTPException):
        pass

    # 4) уведомление в канале (само удалится)
    notice = discord.Embed(
        title="🤖 Сработал автомод",
        description=(f"{member.mention}, нарушение: **{reason}**.\n"
                     + (f"Выдан мут на **{format_duration(seconds)}**."
                        if muted else "⚠️ Не удалось выдать мут (проверьте права/роль бота).")),
        color=Colors.ACCENT,
    )
    try:
        await channel.send(embed=notice, delete_after=8)
    except discord.HTTPException:
        pass

    # 5) запись в лог модерации
    log = discord.Embed(
        title="🤖 Автомод",
        color=Colors.ACCENT,
        timestamp=datetime.now(timezone.utc),
    )
    log.add_field(name="Участник", value=f"{member.mention} (`{member.id}`)", inline=False)
    log.add_field(name="Нарушение", value=reason)
    log.add_field(name="Наказание", value=(f"Мут {format_duration(seconds)}" if muted else "Не выдан"))
    log.add_field(name="Канал", value=channel.mention)
    await mod_log(message.guild, gdata, log)


async def run_automod(message: discord.Message) -> bool:
    """Возвращает True, если сообщение было обработано автомодом (удалено)."""
    gdata = storage.guild(message.guild.id)
    am = gdata["automod"]
    if not am["enabled"]:
        return False
    member = message.author
    if not isinstance(member, discord.Member) or _is_automod_exempt(member, gdata):
        return False

    content = message.content or ""

    # Приглашения в Discord (ссылки discord.gg и т.п.) — обычные ссылки НЕ трогаем
    if am["invite_enabled"] and INVITE_RE.search(content):
        await handle_automod_violation(
            message, gdata, am["invite_mute_minutes"],
            "Приглашение в Discord (ссылка)", purge=False,
        )
        return True

    # Спам/флуд
    if am["spam_enabled"]:
        key = (message.guild.id, member.id)
        now = time.time()
        bucket = _spam_tracker.setdefault(key, [])
        bucket.append(now)
        cutoff = now - am["spam_interval"]
        while bucket and bucket[0] < cutoff:
            bucket.pop(0)
        if len(bucket) >= am["spam_count"]:
            _spam_tracker[key] = []
            await handle_automod_violation(
                message, gdata, am["spam_mute_minutes"], "Спам/флуд", purge=True,
            )
            return True

    return False


# ---------------------------------------------------------------------------
# ИИ-собеседник (role play + мемы). Отвечает, когда упомянули бота.
# ---------------------------------------------------------------------------
AI_HISTORY_TURNS = 8        # сколько последних реплик помнить в канале
AI_COOLDOWN_SECONDS = 4     # антифлуд: не чаще одного ответа игроку раз в N сек
AI_MAX_REPLY = 1900         # запас до лимита Discord в 2000 символов

# История разговора по каналам: channel_id -> [{"role","content"}, ...]
_ai_history: dict[int, list] = {}
# Метки последнего обращения: (guild_id, user_id) -> ts
_ai_cooldown: dict[tuple, float] = {}


def _ai_system_prompt(persona: str, guild_name: str) -> str:
    """Системный промпт: живой ролевой персонаж — дерзкий, матерится и шарит за русские мемы."""
    return (
        f"Ты — {persona}, свой в доску участник Discord-сервера «{guild_name}». "
        "Ты общаешься с людьми в чате и отыгрываешь роль своего персонажа (role play). "
        "Пиши на живом, разговорном русском — дерзко, с чёрным юмором и самоиронией, "
        "как обычный пацан в чате, а не как вежливый ассистент. "
        "Материться можно и нужно — используй русский мат естественно и к месту "
        "(бля, нахуй, пиздец, охуенно и т.п.), но без фанатизма: мат должен усиливать, "
        "а не забивать весь текст. "
        "Ты в теме русских мемов и интернет-культуры (Ждун, «это фиаско, братан», "
        "«ну и что, что», Даня Милохин, Хагги-Вагги, скуф, тралалело тралала, "
        "«я в моменте», рофлы с двача и телеги) — вворачивай их уместно и в тему разговора. "
        "Отвечай коротко и по делу — обычно 1–3 предложения, как реальный человек в чате, "
        "без официоза, без заголовков и списков. Эмодзи — умеренно. "
        "Мат и подколы — это стиль, а не травля: можно дерзить и шутить над собеседником по-доброму, "
        "но не устраивай реальную травлю, угрозы и не разжигай ненависть по признаку "
        "национальности, религии, пола и т.п. "
        "Не давай реально опасных, противоправных инструкций и жёсткого NSFW — "
        "если просят такое, отшутись матом и переведи тему. "
        "Ты можешь тегать (упоминать) участников: просто напиши @их_ник в тексте, "
        "и упоминание станет настоящим пингом. Обращаясь к человеку, зови его по нику через @. "
        "Не пиши @everyone и @here — массовые пинги запрещены. "
        "Отвечай на том же языке, на котором к тебе обратились. "
        "Если просят код — пиши его без лишних пояснений, в блоках ``` с указанием языка. "
        "Если просят картинку — просто скажи «Готово, смотри ниже» и генерируй изображение. "
        "Если чувствуешь, что тема уходит в банальность — подкинь мем, каламбур или отсылку. "
        "Умей поддерживать разговор: задавай уточняющие вопросы, реагируй на эмоции собеседника. "
        "Будь адаптивным — если человек в плохом настроении, смягчи тон. Если хорошему — подкинь шутки."
    )


class AIError(RuntimeError):
    """Ошибка запроса к ИИ-провайдеру. Несёт HTTP-статус, если он известен."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


# Цели ИИ и их русские синонимы (для команды !ии_модель).
AI_PURPOSES = {
    "chat": "chat", "чат": "chat", "общение": "chat", "болтовня": "chat", "диалог": "chat",
    "coding": "coding", "код": "coding", "кодинг": "coding", "code": "coding",
    "программирование": "coding", "прога": "coding",
    "image": "image", "картинка": "image", "картинки": "image", "изображение": "image",
    "рисунок": "image", "арт": "image", "img": "image",
}
# Понятные подписи целей для вывода в чат.
AI_PURPOSE_LABELS = {"chat": "💬 Чат", "coding": "💻 Кодинг", "image": "🎨 Картинки"}

# Системный промпт для «кодинг»-режима — используется и командой !код, и авто-роутингом.
AI_CODING_SYSTEM = (
    "Ты — опытный senior-программист и помощник по коду. Отвечай по делу и практично, "
    "на русском языке. Приводи рабочие примеры кода в блоках ``` с указанием языка, "
    "кратко поясняй решение и подводные камни. Без лишней воды."
)

# Ключевые слова для авто-определения цели по тексту упоминания бота. Если сработали —
# бот сам подставит модель под задачу (код/картинка), не заставляя звать !код/!картинка.
_AI_IMAGE_HINTS = (
    "нарисуй", "нарисовать", "нарисуешь", "картинк", "изображени", "рисунок", "срисуй",
    "сгенерируй фото", "сгенерируй картин", "сгенерируй изображени", "сгенерируй арт",
    "логотип", "обои", "wallpaper", "draw ", "picture of", "image of",
)
_AI_CODE_HINTS = (
    "код", "кодинг", "напиши функци", "напиши программ", "напиши скрипт", "скрипт",
    "функцию на", "ошибка в коде", "почини код", "отладь", "traceback", "стектрейс",
    "python", "питон", "javascript", "джаваскрипт", "typescript", "react", "html", "css",
    "sql-запрос", "регулярк", "regex", "алгоритм", "рефактор", "дебаг", "debug",
)


def detect_ai_purpose(text: str) -> str:
    """Определяет цель запроса (chat/coding/image), чтобы бот сам выбрал модель.

    Правила простые и предсказуемые: блок ``` или код-слова → coding,
    слова про рисование → image, иначе — обычный чат. Картинки проверяем раньше
    кода, чтобы «нарисуй схему кода» уходило в генерацию изображения.
    """
    if not text:
        return "chat"
    if "```" in text:
        return "coding"
    low = text.lower()
    if any(w in low for w in _AI_IMAGE_HINTS):
        return "image"
    if any(w in low for w in _AI_CODE_HINTS):
        return "coding"
    return "chat"


def get_ai_model(gdata: dict, purpose: str = "chat") -> str:
    """Модель под конкретную цель: сначала настройка сервера, затем окружение."""
    chosen = (gdata.get("ai", {}).get("models") or {}).get(purpose)
    if chosen:
        return chosen
    if purpose == "coding":
        return AI_MODEL_CODING or AI_MODEL
    if purpose == "image":
        return AI_IMAGE_MODEL or ""
    return AI_MODEL


async def _ai_request(url: str, payload: dict, *, timeout: int = 90) -> dict:
    """POST к OpenAI-совместимому API с понятными ошибками.

    Возвращает разобранный JSON при HTTP 200. Иначе бросает AIError с осмысленным
    текстом и (если известен) HTTP-статусом. Ключевая деталь: тело сначала читается
    как ТЕКСТ и только потом аккуратно парсится в JSON. Так пустой ответ или HTML
    (например, страница-заглушка Cloudflare/ngrok, когда локальный OmniRoute
    недоступен) больше не роняют парсер загадочным «Expecting value: line 1 column 1»,
    а превращаются в понятное сообщение с реальным статусом и куском тела.
    """
    session = await get_http_session()
    headers = {
        "Authorization": f"Bearer {AI_API_KEY}",
        "Content-Type": "application/json",
    }
    try:
        async with session.post(
            url, json=payload, headers=headers,
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            raw = await resp.text()
            status = resp.status
    except asyncio.TimeoutError:
        raise AIError(
            "провайдер не ответил вовремя (таймаут). Проверьте, что локальный "
            "OmniRoute запущен, а туннель из `AI_BASE_URL` доступен.",
            status=None,
        )
    except aiohttp.ClientError as exc:
        raise AIError(
            f"не удалось подключиться к `AI_BASE_URL` ({exc.__class__.__name__}). "
            "Обычно это значит, что локальный OmniRoute выключен или адрес туннеля "
            "устарел (ссылки trycloudflare.com меняются при каждом перезапуске).",
            status=None,
        )

    # Аккуратно пытаемся разобрать JSON — не роняя всё на HTML/пустом теле.
    body = (raw or "").strip()
    data = None
    if body:
        try:
            data = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            data = None

    snippet = re.sub(r"\s+", " ", body)[:200] or "пустой ответ"

    if status != 200:
        detail = ""
        if isinstance(data, dict):
            err = data.get("error")
            detail = err.get("message") if isinstance(err, dict) else str(err or "")
        raise AIError(detail or f"HTTP {status}, тело: {snippet}", status=status)

    if data is None:
        # Успешный по статусу, но не-JSON ответ — почти всегда заглушка туннеля.
        raise AIError(
            f"провайдер вернул не-JSON ответ (HTTP 200): {snippet}. Похоже на "
            "страницу-заглушку туннеля — проверьте `AI_BASE_URL` и что OmniRoute запущен.",
            status=200,
        )
    return data


async def ask_ai(messages: list, model: str = None, *,
                 temperature: float = 0.9, max_tokens: int = 500) -> str:
    """Запрос к OpenAI-совместимому Chat Completions API. Бросает AIError при ошибке."""
    payload = {
        "model": model or AI_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    data = await _ai_request(f"{AI_BASE_URL}/chat/completions", payload, timeout=90)
    try:
        return (data["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError):
        raise AIError("пустой или неожиданный ответ от ИИ-провайдера.")


async def ask_ai_image(prompt: str, model: str, *, size: str = "1024x1024"):
    """Генерация картинки через OpenAI-совместимый /images/generations.

    Возвращает кортеж (url, raw_bytes): один из элементов может быть None —
    провайдеры отдают либо ссылку, либо base64-данные картинки.
    """
    payload = {"model": model, "prompt": prompt, "n": 1, "size": size}
    data = await _ai_request(f"{AI_BASE_URL}/images/generations", payload, timeout=120)
    try:
        item = data["data"][0]
    except (KeyError, IndexError, TypeError):
        raise AIError("провайдер не вернул изображение.")
    img_url = item.get("url")
    b64 = item.get("b64_json")
    raw = base64.b64decode(b64) if b64 else None
    if not img_url and not raw:
        raise AIError("пустой ответ генератора изображений.")
    return img_url, raw


# Токен @ник в ответе ИИ: латиница/кириллица, цифры, _ . -
_MENTION_TOKEN_RE = re.compile(r"@([A-Za-zА-Яа-яЁё0-9_.\-]{2,32})")


def linkify_mentions(text: str, guild: discord.Guild) -> str:
    """Превращает @ник из текста ИИ в настоящие упоминания <@id> участников.

    Массовые пинги (@everyone/@here) намеренно НЕ трогаются, а благодаря
    allowed_mentions они всё равно не сработают.
    """
    if not text or guild is None:
        return text
    # Карта «ник в нижнем регистре -> id». Первый совпавший участник побеждает.
    lookup = {}
    for m in guild.members:
        for name in (m.display_name, m.name):
            if name:
                lookup.setdefault(name.lower(), m.id)

    def repl(match: "re.Match") -> str:
        token = match.group(1)
        if token.lower() in ("everyone", "here"):
            return match.group(0)
        uid = lookup.get(token.lower())
        return f"<@{uid}>" if uid else match.group(0)

    return _MENTION_TOKEN_RE.sub(repl, text)


# Упоминания, разрешённые в ответах ИИ: пингуем людей, но не @everyone и не роли.
AI_ALLOWED_MENTIONS = discord.AllowedMentions(everyone=False, roles=False, users=True)


def _split_message(text: str, size: int = 1990):
    """Режет длинный текст на куски под лимит Discord (2000 символов)."""
    return [text[i:i + size] for i in range(0, len(text), size)] or [""]


def _strip_bot_mention(message: discord.Message) -> str:
    """Убирает упоминание бота из текста, оставляя сам запрос."""
    content = message.content or ""
    for token in (f"<@{bot.user.id}>", f"<@!{bot.user.id}>"):
        content = content.replace(token, "")
    return content.strip()


async def _ai_reply_image(message: discord.Message, gdata: dict, prompt_text: str) -> bool:
    """Генерирует картинку в ответ на упоминание, когда авто-роутинг распознал такую цель."""
    model = get_ai_model(gdata, "image")
    if not model:
        await message.reply(
            "🎨 Вижу, ты просишь картинку, но модель для генерации изображений не задана. "
            "Админ, укажите её командой `!ии_модель картинка <модель>` или переменной "
            "`AI_IMAGE_MODEL`. Учтите: не у каждого провайдера есть модели генерации "
            "изображений — например, в OmniRoute/Kiro их может не быть вовсе.",
            mention_author=False,
        )
        return True
    try:
        async with message.channel.typing():
            img_url, raw = await ask_ai_image(prompt_text, model)
    except Exception as exc:
        status = getattr(exc, "status", None)
        print(f"[ИИ/картинка] Ошибка (status={status}): {exc}")
        await message.reply(_ai_error_text(status, model), mention_author=False)
        return True
    embed = discord.Embed(
        title="🎨 Готово",
        description=_clip(prompt_text, 400),
        color=Colors.LIGHT,
    )
    embed.set_footer(text=f"{BRAND_NAME} • модель: {model}")
    if raw is not None:
        file = discord.File(io.BytesIO(raw), filename="image.png")
        embed.set_image(url="attachment://image.png")
        await message.reply(embed=embed, file=file, mention_author=False)
    else:
        embed.set_image(url=img_url)
        await message.reply(embed=embed, mention_author=False)
    return True


async def maybe_ai_reply(message: discord.Message) -> bool:
    """Отвечает как ИИ, если это включено. Возвращает True, если сообщение обработано."""
    gdata = storage.guild(message.guild.id)
    ai = gdata["ai"]
    if not ai.get("enabled", True):
        return False

    if not AI_API_KEY:
        await message.reply(
            "🤖 Меня ещё не подключили к «мозгам». Админ, задайте переменную окружения "
            "`AI_API_KEY` (при желании также `AI_BASE_URL` и `AI_MODEL`), и я смогу базарить.",
            mention_author=False,
        )
        return True

    # Антифлуд: слишком частые упоминания одним человеком молча игнорируем.
    now = time.time()
    ckey = (message.guild.id, message.author.id)
    if now - _ai_cooldown.get(ckey, 0.0) < AI_COOLDOWN_SECONDS:
        return False
    _ai_cooldown[ckey] = now

    prompt_text = _strip_bot_mention(message) or "Привет!"
    persona = ai.get("persona") or AI_PERSONA_NAME

    # Бот сам выбирает модель под задачу: просят картинку — рисуем, код — кодовая
    # модель (например kr/qwen3-coder-next), иначе — обычная болтовня.
    purpose = detect_ai_purpose(prompt_text)
    if purpose == "image":
        return await _ai_reply_image(message, gdata, prompt_text)

    history = _ai_history.setdefault(message.channel.id, [])
    history.append({"role": "user", "content": f"{message.author.display_name}: {prompt_text}"})
    # Держим только последние реплики, чтобы не раздувать контекст.
    if len(history) > AI_HISTORY_TURNS * 2:
        del history[: len(history) - AI_HISTORY_TURNS * 2]

    # Под кодинг — деловой системный промпт и модель для кода; иначе ролевой персонаж.
    if purpose == "coding":
        system_prompt = AI_CODING_SYSTEM
        gen_kwargs = {"temperature": 0.3, "max_tokens": 1500}
    else:
        system_prompt = _ai_system_prompt(persona, message.guild.name)
        gen_kwargs = {}
    conversation = [{"role": "system", "content": system_prompt}] + history

    try:
        async with message.channel.typing():
            reply = await ask_ai(conversation, model=get_ai_model(gdata, purpose), **gen_kwargs)
    except Exception as exc:
        status = getattr(exc, "status", None)
        # Печатаем в лог реальную причину (видно в Railway → View logs).
        print(f"[ИИ] Ошибка запроса (status={status}): {exc}")
        if history and history[-1]["role"] == "user":
            history.pop()  # откатываем незавершённую реплику
        # Постоянные ошибки (ключ/баланс/доступ) повтором не лечатся — говорим прямо.
        permanent = {
            401: "ключ ИИ неверный или просрочен",
            402: "на балансе ИИ-провайдера закончились средства",
            403: "провайдер отклонил запрос (проверьте ключ, модель и адрес API)",
            404: "модель или адрес API не найдены (проверьте `AI_MODEL` и `AI_BASE_URL`)",
        }
        if status in permanent:
            await message.reply(
                f"🤖 ИИ сейчас недоступен: {permanent[status]}. "
                "Админу нужно проверить переменные окружения `AI_API_KEY`, "
                "`AI_BASE_URL`, `AI_MODEL` и баланс провайдера.",
                mention_author=False,
            )
        elif status is None:
            await message.reply(
                "🤖 Не могу достучаться до своих «мозгов» — похоже, локальный ИИ (OmniRoute) "
                "выключен или адрес туннеля `AI_BASE_URL` устарел. Админ, глянь `!ии_тест`.",
                mention_author=False,
            )
        else:
            await message.reply(
                "🤖 Что-то я подвис... попробуй тегнуть меня ещё раз чуть позже.",
                mention_author=False,
            )
        return True

    if not reply:
        reply = "🤖 ...(потерял мысль). Спроси ещё раз!"
    history.append({"role": "assistant", "content": reply})
    # Тегаем автора в начале ответа и превращаем @ник в настоящие упоминания.
    out = f"{message.author.mention}, {linkify_mentions(reply, message.guild)}"
    await message.reply(
        out[:AI_MAX_REPLY], mention_author=False, allowed_mentions=AI_ALLOWED_MENTIONS,
    )
    return True


@bot.event
async def on_message(message: discord.Message):
    # ботов и личные сообщения пропускаем
    if message.author.bot or message.guild is None:
        return
    try:
        handled = await run_automod(message)
    except Exception as exc:  # автомод не должен ронять обработку команд
        handled = False
        print(f"[Автомод] Ошибка: {exc}")
    if handled:
        return

    # Если упомянули бота (и это не команда) — отвечает ИИ-собеседник.
    content = (message.content or "").strip()
    is_command = isinstance(PREFIX, str) and bool(PREFIX) and content.startswith(PREFIX)
    if bot.user in message.mentions and not message.mention_everyone and not is_command:
        try:
            if await maybe_ai_reply(message):
                return
        except Exception as exc:  # ИИ не должен ронять обработку команд
            print(f"[ИИ] Непредвиденная ошибка: {exc}")

    # Начисление XP за сообщения (если включено)
    try:
        await grant_xp(message)
    except Exception as exc:
        print(f"[XP] Ошибка начисления: {exc}")

    await bot.process_commands(message)


# ---- Команды настройки ----------------------------------------------------
@bot.command(name="роль_админ")
@admin_only()
async def set_admin_role(ctx, role: discord.Role):
    gdata = storage.guild(ctx.guild.id)
    if role.id in gdata["admin_roles"]:
        gdata["admin_roles"].remove(role.id)
        storage.save()
        await ctx.reply(f"➖ Роль {role.mention} убрана из администрации.")
    else:
        gdata["admin_roles"].append(role.id)
        storage.save()
        await ctx.reply(f"➕ Роль {role.mention} назначена администрацией.")


@bot.command(name="роль_поддержки")
@admin_only()
async def set_support_role(ctx, role: discord.Role):
    gdata = storage.guild(ctx.guild.id)
    if role.id in gdata["support_roles"]:
        gdata["support_roles"].remove(role.id)
        storage.save()
        await ctx.reply(f"➖ Роль {role.mention} убрана из поддержки.")
    else:
        gdata["support_roles"].append(role.id)
        storage.save()
        await ctx.reply(f"➕ Роль {role.mention} добавлена в поддержку (будет видеть тикеты).")


@bot.command(name="категория_тикетов")
@admin_only()
async def set_ticket_category(ctx, category: discord.CategoryChannel):
    gdata = storage.guild(ctx.guild.id)
    gdata["ticket_category_id"] = category.id
    storage.save()
    await ctx.reply(f"✅ Каналы тикетов будут создаваться в категории **{category.name}**.")


@bot.command(name="канал_логов")
@admin_only()
async def set_log_channel(ctx, channel: discord.TextChannel):
    gdata = storage.guild(ctx.guild.id)
    gdata["log_channel_id"] = channel.id
    storage.save()
    await ctx.reply(f"✅ Логи тикетов будут отправляться в {channel.mention}.")


@bot.command(name="канал_панели")
@admin_only()
async def set_panel_channel(ctx, channel: discord.TextChannel):
    gdata = storage.guild(ctx.guild.id)
    gdata["panel_channel_id"] = channel.id
    storage.save()
    await ctx.reply(f"✅ Панель создания тикетов будет размещаться в {channel.mention}.")


@bot.command(name="текст_панели")
@admin_only()
async def set_panel_text(ctx, *, text: str):
    """Использование: !текст_панели Заголовок | Описание"""
    gdata = storage.guild(ctx.guild.id)
    if "|" in text:
        title, desc = text.split("|", 1)
        gdata["panel_title"] = title.strip()
        gdata["panel_description"] = desc.strip()
    else:
        gdata["panel_description"] = text.strip()
    storage.save()
    await ctx.reply("✅ Текст панели обновлён. Используйте `!панель_тикетов`, чтобы отправить её заново.")


@bot.command(name="панель_тикетов")
@admin_only()
async def send_ticket_panel(ctx):
    gdata = storage.guild(ctx.guild.id)
    target_id = gdata.get("panel_channel_id")
    channel = ctx.guild.get_channel(target_id) if target_id else ctx.channel
    await post_ticket_panel(channel, gdata)
    await ctx.reply(f"✅ Панель тикетов отправлена в {channel.mention}.")


@bot.command(name="панель_админ")
@admin_only()
async def send_admin_panel(ctx):
    embed = discord.Embed(
        title="🛠️ Панель администрации",
        description=(
            "Управление тикет-системой:\n"
            "📊 **Статистика** — показатели тикетов\n"
            "⚙️ **Настройки** — текущая конфигурация\n"
            "✏️ **Текст панели** — изменить заголовок/описание\n"
            "🗂️ **Категории** — изменить название, эмодзи, описание\n"
            "🤖 **Автомод** — анти-спам и анти-приглашения\n"
            "📮 **Отправить панель тикетов** — опубликовать панель создания тикетов"
        ),
        color=Colors.PRIMARY,
    )
    brand(embed, ctx.guild)
    await ctx.send(embed=embed, view=AdminPanelView())


@bot.command(name="настройки")
@admin_only()
async def show_settings(ctx):
    gdata = storage.guild(ctx.guild.id)
    await ctx.reply(embed=settings_embed(gdata, ctx.guild))


@bot.command(name="статистика")
@admin_only()
async def show_stats(ctx):
    gdata = storage.guild(ctx.guild.id)
    await ctx.reply(embed=stats_embed(gdata))


@bot.command(name="категории")
@admin_only()
async def manage_categories(ctx):
    gdata = storage.guild(ctx.guild.id)
    await ctx.reply("Выберите категорию для изменения:", view=CategoryManageView(gdata))


# ---- Команды приватных голосовых комнат -----------------------------------
@bot.command(name="приватки", aliases=["комнаты", "приватные_комнаты"])
@admin_only()
async def private_rooms_cmd(ctx, mode: str = None):
    """Показать настройки приватных комнат или включить/выключить их."""
    gdata = storage.guild(ctx.guild.id)
    pr = gdata["private_rooms"]
    if mode is not None:
        m = mode.strip().lower()
        if m in ("вкл", "on", "включить", "1", "да"):
            pr["enabled"] = True
        elif m in ("выкл", "off", "выключить", "0", "нет"):
            pr["enabled"] = False
        else:
            await ctx.reply("Использование: `!приватки [вкл|выкл]`")
            return
        storage.save()
    await ctx.reply(embed=private_rooms_settings_embed(gdata, ctx.guild))


@bot.command(name="приватки_настройка", aliases=["приватки_setup", "комнаты_настройка"])
@admin_only()
async def private_rooms_autosetup(ctx):
    """Авто-создание категории, канала-создателя, интерфейса и публикация панели."""
    guild = ctx.guild
    pr = storage.guild(guild.id)["private_rooms"]
    try:
        category = await guild.create_category(
            "🔊 Приватные комнаты", reason="Автонастройка приватных комнат")
        creator = await guild.create_voice_channel(
            "➕ Создать комнату", category=category, reason="Канал-создатель приватных комнат")
        interface = await guild.create_text_channel(
            "настройка-комнат", category=category, reason="Интерфейс приватных комнат")
    except discord.Forbidden:
        await ctx.reply("❌ У бота нет прав «Управление каналами». Выдайте право и повторите.")
        return
    except discord.HTTPException as exc:
        await ctx.reply(f"❌ Не удалось создать каналы: `{exc}`")
        return

    pr["category_id"] = category.id
    pr["creator_channel_id"] = creator.id
    pr["interface_channel_id"] = interface.id
    pr["enabled"] = True
    storage.save()

    try:
        await post_private_rooms_panel(interface, guild)
    except discord.HTTPException:
        pass

    embed = discord.Embed(
        title="✅ Приватные комнаты настроены",
        description=(
            f"Канал-создатель: {creator.mention}\n"
            f"Категория: **{category.name}**\n"
            f"Панель управления: {interface.mention}\n\n"
            "Теперь зайдите в канал-создатель — бот сделает вам личную комнату, "
            "а управлять ей можно кнопками на панели."
        ),
        color=Colors.LIGHT,
    )
    brand(embed, guild)
    await ctx.reply(embed=embed)


@bot.command(name="приватки_создатель", aliases=["комнаты_создатель"])
@admin_only()
async def private_rooms_set_creator(ctx, channel: discord.VoiceChannel):
    """Назначить голосовой канал-создатель (join-to-create)."""
    pr = storage.guild(ctx.guild.id)["private_rooms"]
    pr["creator_channel_id"] = channel.id
    if not pr.get("category_id") and channel.category:
        pr["category_id"] = channel.category.id
    storage.save()
    await ctx.reply(f"✅ Канал-создатель приватных комнат: {channel.mention}")


@bot.command(name="приватки_категория", aliases=["комнаты_категория"])
@admin_only()
async def private_rooms_set_category(ctx, category: discord.CategoryChannel):
    """Категория Discord, в которой будут появляться приватные комнаты."""
    pr = storage.guild(ctx.guild.id)["private_rooms"]
    pr["category_id"] = category.id
    storage.save()
    await ctx.reply(f"✅ Категория для приватных комнат: **{category.name}**")


@bot.command(name="приватки_интерфейс", aliases=["комнаты_интерфейс", "приватки_канал"])
@admin_only()
async def private_rooms_set_interface(ctx, channel: discord.TextChannel):
    """Текстовый канал, где размещается панель управления комнатами."""
    pr = storage.guild(ctx.guild.id)["private_rooms"]
    pr["interface_channel_id"] = channel.id
    storage.save()
    await ctx.reply(f"✅ Канал-интерфейс приватных комнат: {channel.mention}")


@bot.command(name="приватки_лимит", aliases=["комнаты_лимит"])
@admin_only()
async def private_rooms_set_limit(ctx, limit: int):
    """Лимит участников по умолчанию для новых комнат (0 = без лимита)."""
    pr = storage.guild(ctx.guild.id)["private_rooms"]
    pr["default_limit"] = max(0, min(99, limit))
    storage.save()
    text = "без лимита" if pr["default_limit"] == 0 else str(pr["default_limit"])
    await ctx.reply(f"✅ Лимит участников по умолчанию: **{text}**")


@bot.command(name="приватки_имя", aliases=["комнаты_имя"])
@admin_only()
async def private_rooms_set_name(ctx, *, template: str):
    """Шаблон имени комнаты. Используйте {user} для ника владельца."""
    pr = storage.guild(ctx.guild.id)["private_rooms"]
    pr["name_template"] = template[:100]
    storage.save()
    await ctx.reply(f"✅ Шаблон имени комнаты: `{pr['name_template']}`")


@bot.command(name="приватки_панель", aliases=["комнаты_панель"])
@admin_only()
async def private_rooms_send_panel(ctx):
    """Опубликовать панель управления в канале-интерфейсе (или в текущем канале)."""
    gdata = storage.guild(ctx.guild.id)
    pr = gdata["private_rooms"]
    target_id = pr.get("interface_channel_id")
    channel = ctx.guild.get_channel(target_id) if target_id else ctx.channel
    if channel is None:
        channel = ctx.channel
    await post_private_rooms_panel(channel, ctx.guild)
    await ctx.reply(f"✅ Панель управления комнатами отправлена в {channel.mention}.")


# ---- Команды уровней и валюты ---------------------------------------------
@bot.command(name="уровни", aliases=["lvl", "xp", "уровень"])
@commands.guild_only()
async def level_cmd(ctx, member: discord.Member = None):
    """!уровни [@участник] — показать уровень и XP участника."""
    member = member or ctx.author
    gdata = storage.guild(ctx.guild.id)
    levels = gdata.get("levels", {})
    user_xp = levels.get("user_xp", {}).get(str(member.id))

    if not levels.get("enabled", True):
        await ctx.reply("ℹ️ Система уровней на этом сервере выключена.")
        return

    if not user_xp:
        await ctx.reply(f"ℹ️ У {member.mention} пока нет XP. Отправляй сообщения и получай уровни!")
        return

    xp = user_xp.get("xp", 0)
    level = user_xp.get("level", 1)
    emoji = levels.get("currency_emoji", CURRENCY_EMOJI)

    # Формула: XP для следующего уровня = level * 100
    xp_needed = level * 100
    xp_progress = xp % xp_needed

    # Построим bar
    bar_length = 20
    filled = int(xp_progress / xp_needed * bar_length)
    bar = "█" * filled + "░" * (bar_length - filled)

    embed = discord.Embed(
        title=f"📊 Уровень {member.display_name}",
        description=(
            f"**Уровень:** {level}\n"
            f"**XP:** {xp} / {xp_needed}\n\n"
            f"`{bar}` {int(xp_progress / xp_needed * 100)}%"
        ),
        color=Colors.PRIMARY,
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Валюта", value=f"{emoji} {CURRENCY_NAME}", inline=True)
    embed.add_field(name="Всего сообщений", value=user_xp.get("messages", 0), inline=True)
    brand(embed, ctx.guild)
    await ctx.reply(embed=embed)


@bot.command(name="топ", aliases=["лидерборд", "лидеры", "leaderboard"])
@commands.guild_only()
async def leaderboard_cmd(ctx):
    """!топ — показать топ 10 участников по уровням."""
    gdata = storage.guild(ctx.guild.id)
    levels = gdata.get("levels", {})

    if not levels.get("enabled", True):
        await ctx.reply("ℹ️ Система уровней на этом сервере выключена.")
        return

    user_xp = levels.get("user_xp", {})
    if not user_xp:
        await ctx.reply("ℹ️ Пока никто не получил XP. Начни первым!")
        return

    # Сортируем по XP
    sorted_users = sorted(
        user_xp.items(),
        key=lambda x: x[1].get("xp", 0),
        reverse=True
    )[:10]

    lines = []
    for i, (user_id, data) in enumerate(sorted_users, 1):
        user = ctx.guild.get_member(int(user_id))
        name = user.display_name if user else f"ID {user_id}"
        xp = data.get("xp", 0)
        level = data.get("level", 1)
        emoji = levels.get("currency_emoji", CURRENCY_EMOJI)
        lines.append(f"**{i}.** {name} — 📊 **{level}** | 💰 **{xp}**")

    embed = discord.Embed(
        title=f"🏆 Топ участников по уровням",
        description="\n".join(lines),
        color=Colors.LIGHT,
        timestamp=datetime.now(timezone.utc),
    )
    brand(embed, ctx.guild)
    await ctx.reply(embed=embed)


@bot.command(name="валюта", aliases=["currency", "моя_валюта", "бабки"])
@commands.guild_only()
async def currency_cmd(ctx):
    """!валюта — показать настройки валюты на сервере."""
    gdata = storage.guild(ctx.guild.id)
    levels = gdata.get("levels", {})

    emoji = levels.get("currency_emoji", CURRENCY_EMOJI)
    name = levels.get("currency_name", CURRENCY_NAME)
    plural = levels.get("currency_plural", CURRENCY_PLURAL)
    enabled = levels.get("enabled", True)

    status = "🟢 включена" if enabled else "🔴 выключена"

    embed = discord.Embed(
        title=f"💰 Настройки валюты",
        description=f"**Статус:** {status}",
        color=Colors.PRIMARY,
    )
    embed.add_field(name="Эмодзи", value=emoji, inline=True)
    embed.add_field(name="Название", value=name, inline=True)
    embed.add_field(name="Мн. число", value=plural, inline=True)
    embed.add_field(name="XP за сообщение", value=levels.get("xp_per_message", 5), inline=True)
    embed.add_field(name="XP за картинку", value=levels.get("xp_per_image", 10), inline=True)
    embed.add_field(name="Кулдаун (сек)", value=levels.get("cooldown_seconds", 60), inline=True)
    brand(embed, ctx.guild)
    await ctx.reply(embed=embed)


@bot.command(name="настройки_уровней", aliases=["lvl_config", "уровни_настройка"])
@admin_only()
async def levels_config_cmd(ctx, *, options: str = None):
    """!настройки_уровней [вкл|выкл] [xp] [эмоции] — настройка системы уровней."""
    gdata = storage.guild(ctx.guild.id)
    levels = gdata.setdefault("levels", copy.deepcopy(DEFAULT_LEVELS))

    if not options or not options.strip():
        embed = discord.Embed(
            title="⚙️ Настройки уровней",
            description=(
                "Использование:\n"
                "`!настройки_уровней вкл|выкл` — включить/выключить систему\n"
                "`!настройки_уровней эмоции <эмодзи>` — сменить эмодзи валюты\n"
                "`!настройки_уровней название <имя>` — сменить название валюты\n"
                "`!настройки_уровней xp <число>` — XP за сообщение\n"
                "`!настройки_уровней кулдаун <сек>` — задержка между XP\n"
                "`!настройки_уровней канал <#канал>` — канал для лидерборда"
            ),
            color=Colors.PRIMARY,
        )
        embed.add_field(name="Статус", value="🟢 включена" if levels.get("enabled", True) else "🔴 выключена", inline=True)
        embed.add_field(name="Эмодзи", value=levels.get("currency_emoji", CURRENCY_EMOJI), inline=True)
        embed.add_field(name="Название", value=levels.get("currency_name", CURRENCY_NAME), inline=True)
        embed.add_field(name="XP за сообщение", value=levels.get("xp_per_message", 5), inline=True)
        await ctx.reply(embed=embed)
        return

    args = options.lower().strip().split()
    if not args:
        return

    action = args[0]

    if action in ("вкл", "включить", "on", "1", "да"):
        levels["enabled"] = True
        storage.save()
        await ctx.reply("✅ Система уровней включена.")

    elif action in ("выкл", "выключить", "off", "0", "нет"):
        levels["enabled"] = False
        storage.save()
        await ctx.reply("❌ Система уровней выключена.")

    elif action == "эмоции" and len(args) > 1:
        emoji = " ".join(args[1:]).strip()
        levels["currency_emoji"] = emoji[:10]
        storage.save()
        await ctx.reply(f"✅ Эмодзи валюты изменён на: {emoji}")

    elif action == "название" and len(args) > 1:
        name = " ".join(args[1:]).strip()
        levels["currency_name"] = name[:50]
        levels["currency_plural"] = name[:50]  # упрощено
        storage.save()
        await ctx.reply(f"✅ Название валюты изменено на: {name}")

    elif action == "xp" and len(args) > 1:
        try:
            xp = int(args[1])
            levels["xp_per_message"] = max(1, min(100, xp))
            storage.save()
            await ctx.reply(f"✅ XP за сообщение установлен: {xp}")
        except ValueError:
            await ctx.reply("❌ XP должно быть числом.")

    elif action == "кулдаун" and len(args) > 1:
        try:
            sec = int(args[1])
            levels["cooldown_seconds"] = max(10, min(300, sec))
            storage.save()
            await ctx.reply(f"✅ Кулдаун между XP установлен: {sec} сек")
        except ValueError:
            await ctx.reply("❌ Кулдаун должен быть числом.")

    elif action == "канал" and len(args) > 1:
        channel = ctx.channel
        if ctx.message.mentions:
            await ctx.reply("❌ Укажите канал (#канал), а не пользователя.")
            return
        # Ищем упоминание канала через парсинг
        channel_match = re.search(r'<#(\d+)>', options)
        if channel_match:
            channel_id = int(channel_match.group(1))
            channel = ctx.guild.get_channel(channel_id)
        if channel and isinstance(channel, discord.TextChannel):
            levels["leaderboard_channel_id"] = channel.id
            storage.save()
            await ctx.reply(f"✅ Канал лидерборда установлен: {channel.mention}")
        else:
            await ctx.reply("❌ Канал не найден. Используйте #канал.")

    else:
        await ctx.reply("❌ Неизвестная команда. Используйте `!настройки_уровней` без аргументов для справки.")


# ---- Команды розыгрышей ----------------------------------------------------
def can_create_giveaway(member: discord.Member, gdata: dict) -> bool:
    """Проверка прав на создание розыгрыша: админ или роль "лучшие раздатели"."""
    if is_admin_member(member, gdata):
        return True
    if BEST_DISTRIBUTOR_ROLE_ID in (r.id for r in member.roles):
        return True
    return False


def _parse_giveaway_date(date_str: str) -> Optional[datetime]:
    """Разбор даты розыгрыша: форматы '10.08.2025 18:00', '10.08.2025', '1ч', '30м'."""
    date_str = date_str.strip()
    now = datetime.now(timezone.utc)

    # Формат: 10.08.2025 18:00 или 10.08.2025
    try:
        if ' ' in date_str:
            dt = datetime.strptime(date_str, "%d.%m.%Y %H:%M")
        else:
            dt = datetime.strptime(date_str, "%d.%m.%Y")
        return dt.replace(tzinfo=timezone.utc)
    except ValueError:
        pass

    # Формат: 1ч, 30м, 5д (от текущего момента)
    m = re.match(r'^(\d+)([ччммддсs])$', date_str, re.IGNORECASE)
    if m:
        value = int(m.group(1))
        unit = m.group(2).lower()
        if unit in ('ч', 'h'):
            return now + timedelta(hours=value)
        elif unit in ('м', 'm', 'min'):
            return now + timedelta(minutes=value)
        elif unit in ('д', 'd'):
            return now + timedelta(days=value)
        elif unit in ('с', 's'):
            return now + timedelta(seconds=value)

    return None


def _format_time_left(end_time: datetime) -> str:
    """Форматирование оставшегося времени до окончания розыгрыша."""
    now = datetime.now(timezone.utc)
    diff = end_time - now
    seconds = int(diff.total_seconds())

    if seconds <= 0:
        return "⏳ Завершено"

    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    minutes = (seconds % 3600) // 60

    parts = []
    if days:
        parts.append(f"{days} д.")
    if hours:
        parts.append(f"{hours} ч.")
    if minutes:
        parts.append(f"{minutes} мин.")

    return "⏳ " + " ".join(parts) if parts else "⏳ Мало времени"


def _create_giveaway_embed(giveaway: dict, guild: discord.Guild, msg_id: int) -> discord.Embed:
    """Создание красивого фиолетового эмбеда для розыгрыша."""
    prize = giveaway.get("prize", "Приз")
    description = giveaway.get("description", "")
    end_time = datetime.fromisoformat(giveaway.get("end_time"))
    conditions = giveaway.get("conditions", "")
    participants = giveaway.get("participants", [])
    created_by_id = giveaway.get("created_by")

    creator = guild.get_member(created_by_id) if created_by_id else None

    embed = discord.Embed(
        title=f"🎉 Розыгрыш: {prize}",
        description=description or "—",
        color=Colors.PRIMARY,
        timestamp=datetime.now(timezone.utc),
    )

    embed.add_field(name="📅 Окончание", value=end_time.strftime("%d.%m.%Y %H:%M"), inline=True)
    embed.add_field(name="⏳ Осталось", value=_format_time_left(end_time), inline=True)
    embed.add_field(name="👥 Участники", value=len(participants), inline=True)

    if conditions:
        embed.add_field(name="⚠️ Условия", value=conditions, inline=False)
    else:
        embed.add_field(name="✅ Условия", value="Нет условий участия", inline=False)

    if creator:
        embed.set_footer(text=f"Организатор: {creator.display_name} • {BRAND_NAME}")

    return embed


class GiveawayView(discord.ui.View):
    """Постоянная панель розыгрыша с кнопками участия и завершения."""

    def __init__(self, msg_id: int, gdata: dict, bot: commands.Bot = None):
        super().__init__(timeout=None)
        self.msg_id = msg_id
        self.gdata = gdata
        self.bot = bot

    async def _check_conditions(self, member: discord.Member, conditions: str) -> Tuple[bool, str]:
        """Проверка условий участия. Возвращает (успешно, сообщение)."""
        if not conditions or conditions.strip() == "Нет условий участия":
            return True, "Участие без условий"

        conditions = conditions.strip().lower()

        # Условие: "зайти на сервак [invite]"
        if "сервак" in conditions or "сервер" in conditions:
            invite_match = re.search(r'(?:discord\.gg/|discord\.com/invite/)(\w+)', conditions)
            if invite_match:
                invite_code = invite_match.group(1)
                try:
                    # Пытаемся найти сервер по инвайту
                    for guild in self.bot.guilds if self.bot else []:
                        try:
                            invites = await guild.invites()
                            for invite in invites:
                                if invite.code == invite_code:
                                    return True, "Вы на сервере спонсора"
                        except:
                            pass
                    return False, "⚠️ Вы не зашли на сервер спонсора"
                except:
                    return False, "⚠️ Не удалось проверить сервер (нужны права)"

            # Если приглашение не найдено, просто сообщаем о необходимости заходить
            if "спонсор" in conditions:
                return False, "⚠️ Нужно зайти на сервер спонсора"

        # Условие: роль
        role_match = re.search(r'роль[@\s]*(\d+)', conditions)
        if role_match:
            role_id = int(role_match.group(1))
            if any(r.id == role_id for r in member.roles):
                return True, "У вас нужная роль"
            return False, f"⚠️ У вас нет роли с ID {role_id}"

        # Если условия есть, но не распознаны
        if conditions:
            return False, f"⚠️ Условие: {conditions}"

        return True, "Участие без условий"

    @discord.ui.button(label="🎁 Участвовать!", style=discord.ButtonStyle.success)
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Обработка нажатия кнопки "Участвовать"."""
        msg_id = str(self.msg_id)
        giveaway = self.gdata["giveaways"].get(msg_id)

        if not giveaway:
            await interaction.response.send_message("❌ Этот розыгрыш больше не активен.", ephemeral=True)
            return

        end_time = datetime.fromisoformat(giveaway.get("end_time"))
        if datetime.now(timezone.utc) >= end_time:
            await interaction.response.send_message("❌ Розыгрыш уже завершен.", ephemeral=True)
            return

        if interaction.user.id in giveaway.get("participants", []):
            await interaction.response.send_message("ℹ️ Вы уже участвуете в этом розыгрыше!", ephemeral=True)
            return

        conditions = giveaway.get("conditions", "")
        success, message = await self._check_conditions(interaction.user, conditions)

        if not success:
            embed = discord.Embed(
                title="❌ Условия не выполнены",
                description=message,
                color=Colors.ACCENT,
            )
            brand(embed, interaction.guild)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # Добавляем участника
        if "participants" not in giveaway:
            giveaway["participants"] = []
        giveaway["participants"].append(interaction.user.id)
        storage.save()

        embed = discord.Embed(
            title="✅ Вы участвуете!",
            description=f"Вы добавлены в участники розыгрыша **{giveaway.get('prize')}**",
            color=Colors.LIGHT,
        )
        brand(embed, interaction.guild)
        await interaction.response.send_message(embed=embed, ephemeral=True)

        # Обновляем сообщение розыгрыша
        ch = interaction.channel
        if ch:
            try:
                msg = await ch.fetch_message(self.msg_id)
                embed = _create_giveaway_embed(giveaway, interaction.guild, self.msg_id)
                await msg.edit(embed=embed)
            except:
                pass

    @discord.ui.button(label="🏁 Завершить", style=discord.ButtonStyle.danger, row=1)
    async def end(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Обработка нажатия кнопки "Завершить" - только для организатора."""
        msg_id = str(self.msg_id)
        giveaway = self.gdata["giveaways"].get(msg_id)

        if not giveaway:
            await interaction.response.send_message("❌ Этот розыгрыш больше не активен.", ephemeral=True)
            return

        if interaction.user.id != giveaway.get("created_by"):
            await interaction.response.send_message("⛔ Завершить может только организатор розыгрыша.", ephemeral=True)
            return

        # Завершаем розыгрыш
        giveaway["ended"] = True
        storage.save()

        # Выбираем победителя
        participants = giveaway.get("participants", [])
        winner_id = random.choice(participants) if participants else None

        if winner_id:
            winner = interaction.guild.get_member(winner_id)
            winner_text = winner.mention if winner else f"<@{winner_id}>"
        else:
            winner_text = "—"

        # Об��овляем сообщение
        embed = discord.Embed(
            title=f"🎉 Розыгрыш завершен!",
            description=f"Приз: **{giveaway.get('prize')}**",
            color=Colors.LIGHT,
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="Победитель", value=winner_text, inline=False)
        if participants:
            embed.add_field(name="Всего участников", value=len(participants), inline=True)
        brand(embed, interaction.guild)

        try:
            ch = interaction.channel
            if ch:
                msg = await ch.fetch_message(self.msg_id)
                await msg.edit(embed=embed, view=None)
        except:
            pass

        # Отправляем сообщение победителю если есть
        if winner_id and winner:
            embed_win = discord.Embed(
                title="🎉 Поздравляем!",
                description=f"{winner.mention}\nВы выиграли розыгрыш: **{giveaway.get('prize')}**",
                color=Colors.LIGHT,
                timestamp=datetime.now(timezone.utc),
            )
            brand(embed_win, interaction.guild)
            try:
                await winner.send(embed=embed_win)
            except:
                pass

        # Обновляем статистику
        self.gdata["giveaways"]["stats"]["ended"] = self.gdata["giveaways"].get("stats", {}).get("ended", 0) + 1
        storage.save()

        await interaction.response.send_message(f"✅ Розыгрыш завершен! Победитель: {winner_text}", ephemeral=True)


async def _finish_giveaway(guild: discord.Guild, gdata: dict, msg_id: int, giveaway: dict):
    """Автоматическое завершение розыгрыша по истечении времени."""
    participants = giveaway.get("participants", [])
    winner_id = random.choice(participants) if participants else None

    # Обновляем данные
    giveaway["ended"] = True
    storage.save()

    # Обновляем сообщение
    try:
        ch = guild.get_channel(giveaway.get("channel_id"))
        if ch:
            msg = await ch.fetch_message(int(msg_id))
            if winner_id:
                winner = guild.get_member(winner_id)
                winner_text = winner.mention if winner else f"<@{winner_id}>"
            else:
                winner_text = "—"

            embed = discord.Embed(
                title=f"🎉 Розыгрыш завершен!",
                description=f"Приз: **{giveaway.get('prize')}**",
                color=Colors.LIGHT,
                timestamp=datetime.now(timezone.utc),
            )
            embed.add_field(name="Победитель", value=winner_text, inline=False)
            if participants:
                embed.add_field(name="Всего участников", value=len(participants), inline=True)
            brand(embed, guild)

            await msg.edit(embed=embed, view=None)

            # Уведомляем победителя
            if winner_id and winner:
                embed_win = discord.Embed(
                    title="🎉 Поздравляем!",
                    description=f"{winner.mention}\nВы выиграли розыгрыш: **{giveaway.get('prize')}**",
                    color=Colors.LIGHT,
                    timestamp=datetime.now(timezone.utc),
                )
                brand(embed_win, guild)
                try:
                    await winner.send(embed=embed_win)
                except:
                    pass
    except discord.HTTPException:
        pass

    # Обновляем статистику
    gdata["giveaways"]["stats"]["ended"] = gdata["giveaways"].get("stats", {}).get("ended", 0) + 1
    storage.save()


async def giveaway_check_loop():
    """Фоновый цикл проверки окончания розыгрышей."""
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            for guild in bot.guilds:
                gdata = storage.guild(guild.id)
                if not gdata.get("giveaways", {}).get("enabled", True):
                    continue

                giveaways = gdata.get("giveaways", {}).get("giveaways", {})
                now = datetime.now(timezone.utc)

                for msg_id, giveaway in list(giveaways.items()):
                    if giveaway.get("ended"):
                        continue

                    end_time = datetime.fromisoformat(giveaway.get("end_time"))
                    if now >= end_time:
                        await _finish_giveaway(guild, gdata, msg_id, giveaway)
        except Exception as exc:
            print(f"[Giveaway] Ошибка проверки: {exc}")

        await asyncio.sleep(60)  # Проверка каждую минуту


@bot.command(name="розыгрыш")
@admin_only()
async def giveaway_cmd(ctx, *, args: str = None):
    """Создание розыгрыша.

    Использование: !розыгрыш <приз> | <дата> | <условия> | <описание>
    Формат даты: DD.MM.YYYY HH:MM или DD.MM.YYYY или 1ч 30м 5д
    Пример: !розыгрыш 100 монет | 20.08.2025 18:00 | зайди на сервер | классный приз!
    """
    if not args:
        await ctx.reply(
            "❌ Использование: `!розыгрыш <приз> | <дата> | <условия> | <описание>`\n"
            "Пример: `!розыгрыш 100 монет | 20.08.2025 18:00 | зайди на сервер | классный приз!`"
        )
        return

    # Разбор аргументов
    parts = [p.strip() for p in args.split("|")]
    if len(parts) < 2:
        await ctx.reply("❌ Неверный формат. Используйте `|` как разделитель.")
        return

    prize = parts[0].strip()
    end_time = _parse_giveaway_date(parts[1].strip())

    if not end_time:
        await ctx.reply(
            "❌ Неверный формат даты.\n"
            "Используйте: DD.MM.YYYY HH:MM, DD.MM.YYYY, 1ч, 30м, 5д"
        )
        return

    conditions = parts[2].strip() if len(parts) > 2 else ""
    description = parts[3].strip() if len(parts) > 3 else ""

    # Создание эмбеда
    embed = discord.Embed(
        title=f"🎉 Розыгрыш: {prize}",
        description=description or "—",
        color=Colors.PRIMARY,
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="📅 Окончание", value=end_time.strftime("%d.%m.%Y %H:%M"), inline=True)
    embed.add_field(name="⏳ Осталось", value=_format_time_left(end_time), inline=True)
    embed.add_field(name="👥 Участники", value="0", inline=True)
    embed.add_field(name="⚠️ Условия", value=conditions if conditions else "Нет условий участия", inline=False)
    embed.set_footer(text=f"Организатор: {ctx.author.display_name} • {BRAND_NAME}")
    brand(embed, ctx.guild)

    # Отправляем сообщение
    msg = await ctx.send(embed=embed, view=GiveawayView(msg_id=0, gdata={}, bot=bot))

    # Сохраняем данные (msg_id пока 0, обновим после сохранения)
    gdata = storage.guild(ctx.guild.id)
    giveaways = gdata.get("giveaways", {})
    if "giveaways" not in giveaways:
        giveaways["giveaways"] = {}

    new_msg_id = str(msg.id)
    giveaways["giveaways"][new_msg_id] = {
        "prize": prize,
        "end_time": end_time.isoformat(),
        "conditions": conditions,
        "description": description,
        "participants": [],
        "ended": False,
        "created_by": ctx.author.id,
        "channel_id": ctx.channel.id,
    }
    giveaways["stats"]["created"] = giveaways.get("stats", {}).get("created", 0) + 1
    storage.save()

    # Обновляем view с правильным msg_id
    view = GiveawayView(msg_id=int(new_msg_id), gdata=gdata, bot=bot)
    await msg.edit(view=view)

    await ctx.reply(f"✅ Розыгрыш создан: {msg.jump_url}", delete_after=5)


@bot.command(name="розыгрыши")
@admin_only()
async def giveaways_list_cmd(ctx):
    """Список активных и завершенных розыгрышей."""
    gdata = storage.guild(ctx.guild.id)
    giveaways = gdata.get("giveaways", {}).get("giveaways", {})
    stats = gdata.get("giveaways", {}).get("stats", {"created": 0, "ended": 0})

    if not giveaways:
        await ctx.reply("ℹ️ Нет активных розыгрышей.")
        return

    active = []
    ended = []
    now = datetime.now(timezone.utc)

    for msg_id, g in giveaways.items():
        end_time = datetime.fromisoformat(g.get("end_time"))
        if g.get("ended"):
            ended.append((msg_id, g))
        else:
            active.append((msg_id, g))

    embed = discord.Embed(
        title="🎁 Розыгрыши",
        color=Colors.PRIMARY,
    )
    embed.add_field(name="Всего создано", value=str(stats.get("created", 0)), inline=True)
    embed.add_field(name="Завершено", value=str(stats.get("ended", 0)), inline=True)
    embed.add_field(name="Активных", value=str(len(active)), inline=True)

    if active:
        lines = []
        for msg_id, g in active[:5]:
            prize = g.get("prize", "—")
            end = datetime.fromisoformat(g.get("end_time"))
            participants = len(g.get("participants", []))
            lines.append(f"**{prize}** | {end.strftime('%d.%m %H:%M')} | 👥 {participants}")
        embed.add_field(name="Активные", value="\n".join(lines) or "—", inline=False)

    if ended:
        lines = []
        for msg_id, g in ended[:5]:
            prize = g.get("prize", "—")
            end = datetime.fromisoformat(g.get("end_time"))
            lines.append(f"**{prize}** | {end.strftime('%d.%m %H:%M')}")
        embed.add_field(name="Завершенные", value="\n".join(lines) or "—", inline=False)

    brand(embed, ctx.guild)
    await ctx.reply(embed=embed)


@bot.command(name="розыгрыши_настройка")
@admin_only()
async def giveaways_config_cmd(ctx, *, options: str = None):
    """Настройка системы розыгрышей."""
    gdata = storage.guild(ctx.guild.id)
    giveaways = gdata.setdefault("giveaways", copy.deepcopy(DEFAULT_GIVEAWAYS))

    stats = gdata.get("giveaways", {}).get("stats", {"created": 0, "ended": 0})

    if not options or not options.strip():
        enabled = giveaways.get("enabled", True)
        embed = discord.Embed(
            title="⚙️ Настройки розыгрышей",
            description=(
                "Использование:\n"
                "`!розыгрыши_настройка вкл|выкл` — включить/выключить систему\n"
                "`!розыгрыши_настройка статистика` — показать статистику\n"
                "`!розыгрыши_настройка сброс` — сбросить статистику"
            ),
            color=Colors.PRIMARY,
        )
        embed.add_field(name="Статус", value="🟢 включена" if enabled else "🔴 выключена", inline=True)
        embed.add_field(name="Всего создано", value=str(stats.get("created", 0)), inline=True)
        embed.add_field(name="Завершено", value=str(stats.get("ended", 0)), inline=True)
        embed.add_field(name="Активных", value=str(len(giveaways.get("giveaways", {}))), inline=True)
        brand(embed, ctx.guild)
        await ctx.reply(embed=embed)
        return

    args = options.lower().strip().split()
    if not args:
        return

    action = args[0]

    if action in ("вкл", "включить", "on", "1", "да"):
        giveaways["enabled"] = True
        storage.save()
        await ctx.reply("✅ Система розыгрышей включена.")

    elif action in ("выкл", "выключить", "off", "0", "нет"):
        giveaways["enabled"] = False
        storage.save()
        await ctx.reply("❌ Система розыгрышей выключена.")

    elif action == "статистика":
        embed = discord.Embed(
            title="📊 Статистика розыгрышей",
            color=Colors.PRIMARY,
        )
        embed.add_field(name="Всего создано", value=str(stats.get("created", 0)), inline=True)
        embed.add_field(name="Завершено", value=str(stats.get("ended", 0)), inline=True)
        embed.add_field(name="Активных", value=str(len(giveaways.get("giveaways", {}))), inline=True)
        brand(embed, ctx.guild)
        await ctx.reply(embed=embed)

    elif action == "сброс":
        giveaways["stats"] = {"created": 0, "ended": 0}
        storage.save()
        await ctx.reply("✅ Статистика розыгрышей сброшена.")

    else:
        await ctx.reply("❌ Неизвестная команда. Используйте `!розыгрыши_настройка` без аргументов для справки.")


# ---- Команды модерации -----------------------------------------------------
def _mod_embed(title, color, member, moderator, reason, extra=None):
    embed = discord.Embed(title=title, color=color, timestamp=datetime.now(timezone.utc))
    embed.add_field(name="Участник", value=f"{member.mention} (`{member.id}`)", inline=False)
    embed.add_field(name="Модератор", value=moderator.mention)
    if extra:
        embed.add_field(name=extra[0], value=extra[1])
    embed.add_field(name="Причина", value=reason, inline=False)
    return embed


@bot.command(name="бан")
@admin_only()
async def ban_cmd(ctx, member: discord.Member, *, reason: str = "Причина не указана"):
    if not await can_moderate(ctx, member):
        return
    try:
        await member.ban(reason=f"{ctx.author}: {reason}", delete_message_seconds=0)
    except discord.Forbidden:
        await ctx.reply("❌ У меня нет права «Банить участников» или роль бота слишком низкая.")
        return
    embed = _mod_embed("🔨 Участник забанен", Colors.DANGER, member, ctx.author, reason)
    await ctx.reply(embed=embed)
    await mod_log(ctx.guild, storage.guild(ctx.guild.id), embed)


@bot.command(name="разбан")
@admin_only()
async def unban_cmd(ctx, user_id: int, *, reason: str = "Причина не указана"):
    try:
        user = await bot.fetch_user(user_id)
        await ctx.guild.unban(user, reason=f"{ctx.author}: {reason}")
    except discord.NotFound:
        await ctx.reply("❌ Пользователь не найден в списке банов.")
        return
    except discord.Forbidden:
        await ctx.reply("❌ У меня нет права «Банить участников».")
        return
    embed = discord.Embed(
        title="♻️ Участник разбанен", color=Colors.LIGHT,
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="Пользователь", value=f"{user} (`{user.id}`)", inline=False)
    embed.add_field(name="Модератор", value=ctx.author.mention)
    embed.add_field(name="Причина", value=reason, inline=False)
    await ctx.reply(embed=embed)
    await mod_log(ctx.guild, storage.guild(ctx.guild.id), embed)


@bot.command(name="кик")
@admin_only()
async def kick_cmd(ctx, member: discord.Member, *, reason: str = "Причина не указана"):
    if not await can_moderate(ctx, member):
        return
    try:
        await member.kick(reason=f"{ctx.author}: {reason}")
    except discord.Forbidden:
        await ctx.reply("❌ У меня нет права «Выгонять участников» или роль бота слишком низкая.")
        return
    embed = _mod_embed("👢 Участник кикнут", Colors.ACCENT, member, ctx.author, reason)
    await ctx.reply(embed=embed)
    await mod_log(ctx.guild, storage.guild(ctx.guild.id), embed)


@bot.command(name="мут")
@staff_only()
async def mute_cmd(ctx, member: discord.Member, duration: str, *, reason: str = "Причина не указана"):
    """!мут @участник 10м причина  (единицы: с/м/ч/д или s/m/h/d)"""
    if not await can_moderate(ctx, member):
        return
    seconds = parse_duration(duration)
    if seconds is None or seconds <= 0:
        await ctx.reply("❌ Неверная длительность. Примеры: `30с`, `10м`, `2ч`, `1д`.")
        return
    seconds = min(seconds, MAX_TIMEOUT_SECONDS)
    try:
        await member.timeout(timedelta(seconds=seconds), reason=f"{ctx.author}: {reason}")
    except discord.Forbidden:
        await ctx.reply("❌ У меня нет права «Тайм-аут участникам» или роль бота слишком низкая.")
        return
    embed = _mod_embed("🔇 Участник в муте", Colors.ACCENT, member, ctx.author,
                       reason, extra=("Длительность", format_duration(seconds)))
    await ctx.reply(embed=embed)
    await mod_log(ctx.guild, storage.guild(ctx.guild.id), embed)


@bot.command(name="размут")
@staff_only()
async def unmute_cmd(ctx, member: discord.Member, *, reason: str = "Причина не указана"):
    try:
        await member.timeout(None, reason=f"{ctx.author}: {reason}")
    except discord.Forbidden:
        await ctx.reply("❌ У меня нет права «Тайм-аут участникам» или роль бота слишком низкая.")
        return
    embed = _mod_embed("🔊 Мут снят", Colors.LIGHT, member, ctx.author, reason)
    await ctx.reply(embed=embed)
    await mod_log(ctx.guild, storage.guild(ctx.guild.id), embed)


# ---- Список банов и мутов --------------------------------------------------
@bot.command(name="список_банов", aliases=["баны", "bans"])
@admin_only()
async def list_bans_cmd(ctx):
    """!список_банов — показать список всех забаненных участников."""
    try:
        bans = await ctx.guild.bans()
    except discord.Forbidden:
        await ctx.reply("❌ У меня нет права «Банить участников».")
        return

    if not bans:
        await ctx.reply("✅ На сервере нет забаненных участников.")
        return

    lines = []
    for i, ban_entry in enumerate(bans[:10], 1):  # Лимит 10 строк
        user = ban_entry.user
        reason = ban_entry.reason or "причина не указана"
        lines.append(f"**{i}.** {user.mention} (`{user}`)\n   ID: `{user.id}`\n   Причина: {reason}")

    embed = discord.Embed(
        title=f"🔨 Забаненные участники ({len(bans)})",
        description="\n\n".join(lines),
        color=Colors.DANGER,
        timestamp=datetime.now(timezone.utc),
    )
    brand(embed, ctx.guild)
    await ctx.reply(embed=embed)


@bot.command(name="список_мутов", aliases=["муты", "timeouts", "муты_сейчас"])
@staff_only()
async def list_timeouts_cmd(ctx):
    """!список_мутов — показать список участников с тайм-аутами."""
    guild = ctx.guild
    muted_members = []

    for member in guild.members:
        if member.is_timed_out():
            muted_members.append(member)

    if not muted_members:
        await ctx.reply("✅ На сервере нет участников в муте.")
        return

    lines = []
    for i, member in enumerate(muted_members[:10], 1):  # Лимит 10 строк
        remaining = member.timed_out_until - datetime.now(timezone.utc)
        remaining_sec = int(remaining.total_seconds())
        remaining_str = format_duration(remaining_sec) if remaining_sec > 0 else "до снятия"
        reason = member.active_mod_log() if hasattr(member, 'active_mod_log') and member.active_mod_log() else "причина не указана"
        lines.append(f"**{i}.** {member.mention} (`{member.display_name}`)\n   Снят: **{remaining_str}**\n   Причина: {reason}")

    embed = discord.Embed(
        title=f"🔇 Участники в муте ({len(muted_members)})",
        description="\n\n".join(lines),
        color=Colors.ACCENT,
        timestamp=datetime.now(timezone.utc),
    )
    brand(embed, ctx.guild)
    await ctx.reply(embed=embed)


# ---- Предупреждения (варны) ------------------------------------------------
WARN_LIMIT = 3  # столько предупреждений = бот предлагает выдать наказание


def _warns_list(gdata: dict, user_id: int) -> list:
    """Список предупреждений участника (создаётся при первом обращении)."""
    return gdata.setdefault("warns", {}).setdefault(str(user_id), [])


class WarnMuteModal(discord.ui.Modal, title="Мут за предупреждения"):
    """Спрашивает длительность мута, когда модератор выбрал «Мут» как наказание."""

    def __init__(self, parent: "PunishmentView"):
        super().__init__()
        self.parent = parent
        self.duration = discord.ui.TextInput(
            label="Длительность (напр. 30м, 2ч, 1д)",
            default="1ч", max_length=10,
        )
        self.reason = discord.ui.TextInput(
            label="Причина", required=False,
            default=f"{WARN_LIMIT} предупреждения", max_length=200,
        )
        self.add_item(self.duration)
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction):
        seconds = parse_duration(str(self.duration.value))
        if not seconds or seconds <= 0:
            await interaction.response.send_message(
                "❌ Неверная длительность. Примеры: `30с`, `10м`, `2ч`, `1д`.",
                ephemeral=True,
            )
            return
        seconds = min(seconds, MAX_TIMEOUT_SECONDS)
        reason = str(self.reason.value) or f"{WARN_LIMIT} предупреждения"
        try:
            await self.parent.member.timeout(
                timedelta(seconds=seconds),
                reason=f"{self.parent.moderator}: {reason}",
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ У меня нет права «Тайм-аут участникам» или роль бота слишком низкая.",
                ephemeral=True,
            )
            return
        embed = _mod_embed(
            f"🔇 Мут за {WARN_LIMIT} предупреждения", Colors.ACCENT,
            self.parent.member, self.parent.moderator, reason,
            extra=("Длительность", format_duration(seconds)),
        )
        await interaction.response.send_message(embed=embed)
        await self.parent.finish(interaction.guild, embed)


class PunishmentView(discord.ui.View):
    """Предлагает модератору выбрать наказание, когда набрано WARN_LIMIT варнов."""

    def __init__(self, member: discord.Member, moderator: discord.Member, gdata: dict):
        super().__init__(timeout=180)
        self.member = member
        self.moderator = moderator
        self.gdata = gdata
        self.message: discord.Message | None = None

    async def _guard(self, interaction: discord.Interaction) -> bool:
        # Наказание выбирает только тот модератор, что выдал третий варн.
        if interaction.user.id != self.moderator.id:
            await interaction.response.send_message(
                "⛔ Наказание выбирает модератор, выдавший предупреждение.",
                ephemeral=True,
            )
            return False
        block = _moderation_block_reason(interaction.guild, self.moderator, self.member)
        if block:
            await interaction.response.send_message(block, ephemeral=True)
            return False
        return True

    def _disable(self):
        for item in self.children:
            item.disabled = True

    async def finish(self, guild: discord.Guild, embed: discord.Embed):
        """Сбрасываем варны, гасим кнопки и пишем в модлог после наказания."""
        self.gdata.setdefault("warns", {}).pop(str(self.member.id), None)
        storage.save()
        self._disable()
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass
        self.stop()
        await mod_log(guild, self.gdata, embed)

    async def on_timeout(self):
        self._disable()
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

    @discord.ui.button(label="Мут", emoji="🔇", style=discord.ButtonStyle.secondary)
    async def mute_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._guard(interaction):
            return
        await interaction.response.send_modal(WarnMuteModal(self))

    @discord.ui.button(label="Бан", emoji="🔨", style=discord.ButtonStyle.danger)
    async def ban_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._guard(interaction):
            return
        reason = f"{WARN_LIMIT} предупреждения (модератор {self.moderator})"
        try:
            await self.member.ban(reason=reason, delete_message_seconds=0)
        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ У меня нет права «Банить участников» или роль бота слишком низкая.",
                ephemeral=True,
            )
            return
        embed = _mod_embed(f"🔨 Бан за {WARN_LIMIT} предупреждения", Colors.DANGER,
                           self.member, self.moderator, reason)
        await interaction.response.send_message(embed=embed)
        await self.finish(interaction.guild, embed)

    @discord.ui.button(label="Кик", emoji="👢", style=discord.ButtonStyle.primary)
    async def kick_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._guard(interaction):
            return
        reason = f"{WARN_LIMIT} предупреждения (модератор {self.moderator})"
        try:
            await self.member.kick(reason=reason)
        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ У меня нет права «Выгонять участников» или роль бота слишком низкая.",
                ephemeral=True,
            )
            return
        embed = _mod_embed(f"👢 Кик за {WARN_LIMIT} предупреждения", Colors.ACCENT,
                           self.member, self.moderator, reason)
        await interaction.response.send_message(embed=embed)
        await self.finish(interaction.guild, embed)


@bot.command(name="варн", aliases=["предупредить", "warn"])
@staff_only()
async def warn_cmd(ctx, member: discord.Member, *, reason: str = "Причина не указана"):
    """!варн @участник [причина] — выдать предупреждение (поддержка)."""
    if not await can_moderate(ctx, member):
        return
    gdata = storage.guild(ctx.guild.id)
    warns = _warns_list(gdata, member.id)
    warns.append({
        "mod_id": ctx.author.id,
        "reason": reason,
        "ts": datetime.now(timezone.utc).isoformat(),
    })
    count = len(warns)
    storage.save()

    # Пытаемся уведомить участника в ЛС (не критично, если закрыты).
    try:
        await member.send(
            f"⚠️ Вам выдали предупреждение на сервере **{ctx.guild.name}** "
            f"({count}/{WARN_LIMIT}). Причина: {reason}"
        )
    except discord.HTTPException:
        pass

    reached = count >= WARN_LIMIT
    embed = _mod_embed(
        "⚠️ Выдано предупреждение", Colors.DANGER if reached else Colors.ACCENT,
        member, ctx.author, reason, extra=("Предупреждений", f"{count}/{WARN_LIMIT}"),
    )
    if reached:
        embed.add_field(
            name="Порог достигнут",
            value=("Набрано максимум предупреждений. "
                   "Выберите наказание кнопкой ниже 👇"),
            inline=False,
        )
        view = PunishmentView(member, ctx.author, gdata)
        view.message = await ctx.reply(embed=embed, view=view)
    else:
        await ctx.reply(embed=embed)
    await mod_log(ctx.guild, gdata, embed)


@bot.command(name="варны", aliases=["предупреждения", "warns"])
@staff_only()
async def warns_cmd(ctx, member: discord.Member = None):
    """!варны [@участник] — показать предупреждения участника (поддержка)."""
    member = member or ctx.author
    gdata = storage.guild(ctx.guild.id)
    warns = gdata.get("warns", {}).get(str(member.id), [])
    embed = discord.Embed(
        title=f"⚠️ Предупреждения — {member.display_name}",
        color=Colors.ACCENT, timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="Всего", value=f"{len(warns)}/{WARN_LIMIT}", inline=False)
    if warns:
        lines = []
        for i, w in enumerate(warns, 1):
            mod = ctx.guild.get_member(w.get("mod_id"))
            mod_txt = mod.mention if mod else f"`{w.get('mod_id')}`"
            date = str(w.get("ts", ""))[:10]
            lines.append(f"**{i}.** {w.get('reason', '—')} — {mod_txt} ({date})")
        embed.add_field(name="Список", value="\n".join(lines)[:1024], inline=False)
    brand(embed, ctx.guild)
    await ctx.reply(embed=embed)


@bot.command(name="снятьварн", aliases=["снять_варн", "unwarn"])
@staff_only()
async def unwarn_cmd(ctx, member: discord.Member, amount: str = "1"):
    """!снятьварн @участник [кол-во|все] — снять предупреждения (поддержка)."""
    gdata = storage.guild(ctx.guild.id)
    warns = gdata.get("warns", {}).get(str(member.id), [])
    if not warns:
        await ctx.reply(f"ℹ️ У {member.mention} нет предупреждений.")
        return
    if amount.lower() in ("все", "всё", "all"):
        n = len(warns)
    else:
        try:
            n = max(1, min(len(warns), int(amount)))
        except ValueError:
            await ctx.reply("❌ Укажите число или «все». Пример: `!снятьварн @участник 2`.")
            return
    del warns[len(warns) - n:]
    if not warns:
        gdata["warns"].pop(str(member.id), None)
    storage.save()
    left = len(gdata.get("warns", {}).get(str(member.id), []))
    embed = _mod_embed("♻️ Предупреждения сняты", Colors.LIGHT, member, ctx.author,
                       f"Снято: {n}", extra=("Осталось", f"{left}/{WARN_LIMIT}"))
    await ctx.reply(embed=embed)
    await mod_log(ctx.guild, gdata, embed)


# ---- Команды автомода ------------------------------------------------------
@bot.command(name="автомод")
@admin_only()
async def automod_cmd(ctx):
    gdata = storage.guild(ctx.guild.id)
    await ctx.reply(embed=automod_embed(gdata), view=AutomodPanelView(gdata))


@bot.command(name="канал_модлогов")
@admin_only()
async def set_modlog_channel(ctx, channel: discord.TextChannel):
    gdata = storage.guild(ctx.guild.id)
    gdata["modlog_channel_id"] = channel.id
    storage.save()
    await ctx.reply(f"✅ Логи модерации и автомода будут отправляться в {channel.mention}.")


@bot.command(name="автомод_иммунитет")
@admin_only()
async def automod_exempt_role(ctx, role: discord.Role):
    gdata = storage.guild(ctx.guild.id)
    roles = gdata["automod"]["exempt_roles"]
    if role.id in roles:
        roles.remove(role.id)
        storage.save()
        await ctx.reply(f"➖ Роль {role.mention} больше не имеет иммунитета к автомоду.")
    else:
        roles.append(role.id)
        storage.save()
        await ctx.reply(f"➕ Роль {role.mention} получила иммунитет к автомоду.")


# ---- Команды музыкального плеера ------------------------------------------
@bot.command(name="в playing", aliases=["join", "подключись", "вступи"])
async def join_vc_cmd(ctx):
    """!в playing — подключиться к голосовому каналу."""
    # Проверка PyNaCl
    try:
        import nacl
    except ImportError:
        await ctx.reply("❌ Ошибка: PyNaCl не установлен. Установи `pip install PyNaCl`")
        return

    voice_client = ctx.guild.voice_client
    if voice_client and voice_client.is_connected():
        await ctx.reply(f"✅ Я уже в голосовом канале: {voice_client.channel.mention}")
        return

    if not ctx.author.voice or not ctx.author.voice.channel:
        await ctx.reply("❌ Сначала зайди в голосовой канал.")
        return

    try:
        voice_client = await ctx.author.voice.channel.connect(timeout=30.0)
        await ctx.reply(f"🎵 Подключился к каналу: {voice_client.channel.mention}")
    except discord.ClientException:
        await ctx.reply("❌ Я уже подключен к голосовому каналу.")
    except asyncio.TimeoutError:
        await ctx.reply("❌ Не удалось подключиться к голосовому каналу.")


@bot.command(name="выход", aliases=["leave", "выйди", "disconnected"])
async def leave_vc_cmd(ctx):
    """!выход — отключиться от голосового канала."""
    voice_client = ctx.guild.voice_client
    if not voice_client or not voice_client.is_connected():
        await ctx.reply("❌ Я не подключен к голосовому каналу.")
        return

    music_player.stop(ctx.guild)
    await voice_client.disconnect()
    await ctx.reply("👋 Отключился от голосового канала.")


@bot.command(name="играй", aliases=["play", "музыка", "загрузи"])
async def play_cmd(ctx, *, query: str = None):
    """!играй <название или ссылка> — воспроизвести музыку."""
    if not query or not query.strip():
        await ctx.reply("🎵 Укажи название трека или ссылку:\n`!играй название песни`")
        return

    # Проверяем источник
    if "music.yandex" in query or "yandex.ru/music" in query:
        await music_player.play_yandex_music(ctx, query)
    elif "vk.com" in query or "vkmusic" in query:
        await music_player.play_vk_music(ctx, query)
    else:
        # Пытаемся сначала Яндекс, потом ВК
        await music_player.play_yandex_music(ctx, query)


@bot.command(name="стоп", aliases=["stop", "отмена"])
async def stop_cmd(ctx):
    """!стоп — остановить воспроизведение."""
    music_player.stop(ctx.guild)
    await ctx.reply("⏹️ Воспроизведение остановлено.")


@bot.command(name="пропустить", aliases=["skip", "следующий"])
async def skip_cmd(ctx):
    """!пропустить — пропустить текущий трек."""
    music_player.skip(ctx.guild)
    await ctx.reply("⏭️ Пропущено.")


@bot.command(name="громкость", aliases=["volume", "глаза"])
async def volume_cmd(ctx, volume: int = None):
    """!громкость [1-100] — установить громкость."""
    if volume is None:
        player = music_player.players.get(ctx.guild.id)
        current = int((player.get("volume", 0.5) if player else 0.5) * 100)
        await ctx.reply(f"🔊 Текущая громкость: **{current}%**")
        return

    if not 1 <= volume <= 100:
        await ctx.reply("❌ Громкость должна быть от 1 до 100.")
        return

    music_player.set_volume(ctx.guild, volume / 100)
    await ctx.reply(f"🔊 Громкость установлена: **{volume}%**")


@bot.command(name="цикл", aliases=["loop", "повтор"])
async def loop_cmd(ctx):
    """!цикл — включить/выключить повтор очереди."""
    looped = music_player.toggle_loop(ctx.guild)
    await ctx.reply(f"🔄 Повтор очереди: **{'ВКЛ' if looped else 'ВЫКЛ'}**")


@bot.command(name="очередь", aliases=["queue", "список"])
async def queue_cmd(ctx):
    """!очередь — показать очередь воспроизведения."""
    queue = music_player.get_queue(ctx.guild)

    if not queue:
        await ctx.reply("🎵 Очередь пуста. Добавь треки командой `!играй`.")
        return

    lines = []
    for i, track in enumerate(queue[:10], 1):
        lines.append(f"**{i}.** {track['title']}")
        lines[-1] += f" — {track['requester']}"

    if len(queue) > 10:
        lines.append(f"... и ещё **{len(queue) - 10}** треков")

    embed = discord.Embed(
        title=f"🎵 Очередь воспроизведения ({len(queue)})",
        description="\n".join(lines),
        color=Colors.PRIMARY,
    )
    await ctx.reply(embed=embed)


@bot.command(name="очистить_очередь", aliases=["clear_queue", "очистить"])
async def clear_queue_cmd(ctx):
    """!очистить_очередь — очистить очередь."""
    music_player.clear_queue(ctx.guild)
    await ctx.reply("🗑️ Очередь очищена.")


# ---------------------------------------------------------------------------
# Команды ИИ-собеседника
# ---------------------------------------------------------------------------
_TRUE_WORDS = {"вкл", "включить", "on", "да", "true", "1", "включи"}
_FALSE_WORDS = {"выкл", "выключить", "off", "нет", "false", "0", "выключи"}


@bot.command(name="ии")
@admin_only()
async def ai_toggle(ctx, mode: str = None):
    """!ии [вкл|выкл] — включить/выключить ИИ-собеседника (без аргумента — переключить)."""
    gdata = storage.guild(ctx.guild.id)
    ai = gdata["ai"]
    if mode is None:
        ai["enabled"] = not ai["enabled"]
    elif mode.lower() in _TRUE_WORDS:
        ai["enabled"] = True
    elif mode.lower() in _FALSE_WORDS:
        ai["enabled"] = False
    else:
        await ctx.reply("❌ Используйте `!ии вкл` или `!ии выкл`.")
        return
    storage.save()
    state = "включён 🟢" if ai["enabled"] else "выключен 🔴"
    note = "" if AI_API_KEY else "\n⚠️ Не задан `AI_API_KEY` — отвечать пока не смогу."
    warn = (
        "\n\n⚠️ **Внимание:** персонаж общается **с матом** и в дерзкой манере — это контент **18+**. "
        "Включайте его только там, где это уместно, и предупредите участников. "
        "За соблюдение [правил Discord](https://discord.com/guidelines) и уместность контента "
        "отвечает администрация сервера."
        if ai["enabled"] else ""
    )
    await ctx.reply(
        f"🤖 ИИ-собеседник теперь **{state}**. Упомяните меня в чате, чтобы поболтать.{note}{warn}"
    )


@bot.command(name="ии_имя")
@admin_only()
async def ai_persona(ctx, *, name: str = None):
    """!ии_имя <имя> — задать имя/образ персонажа для role play (без аргумента — сброс)."""
    gdata = storage.guild(ctx.guild.id)
    if not name or not name.strip():
        gdata["ai"]["persona"] = None
        storage.save()
        await ctx.reply(f"🤖 Имя персонажа сброшено на «{AI_PERSONA_NAME}».")
        return
    persona = name.strip()[:100]
    gdata["ai"]["persona"] = persona
    storage.save()
    await ctx.reply(f"🤖 Теперь я отыгрываю персонажа: **{persona}**. Тегните меня — проверим!")


@bot.command(name="забудь")
async def ai_forget(ctx):
    """Очистить память ИИ-разговора в текущем канале."""
    _ai_history.pop(ctx.channel.id, None)
    await ctx.reply("🧠 Историю разговора в этом канале очистил — начинаем с чистого листа.")


def _ai_models_embed(gdata: dict) -> discord.Embed:
    """Показывает, какая модель используется под каждую цель."""
    embed = discord.Embed(
        title="🧩 Модели ИИ по целям",
        description=(
            "Модель под конкретную цель можно задать командой\n"
            "`!ии_модель <цель> <модель>` (цели: `чат`, `кодинг`, `картинка`).\n"
            "`!ии_модель <цель> сброс` — вернуть значение по умолчанию."
        ),
        color=Colors.PRIMARY,
    )
    models = gdata.get("ai", {}).get("models") or {}
    for purpose, label in AI_PURPOSE_LABELS.items():
        override = models.get(purpose)
        effective = get_ai_model(gdata, purpose) or "— не задана —"
        source = "настройка сервера" if override else "по умолчанию"
        embed.add_field(name=label, value=f"`{effective}`\n_({source})_", inline=True)
    brand(embed)
    return embed


@bot.command(name="ии_модель", aliases=["ии_модели", "ai_model"])
@admin_only()
async def ai_set_model(ctx, purpose: str = None, *, model: str = None):
    """!ии_модель <цель> <модель> — задать модель под цель (чат/кодинг/картинка)."""
    gdata = storage.guild(ctx.guild.id)
    if not purpose:
        await ctx.reply(embed=_ai_models_embed(gdata))
        return
    key = AI_PURPOSES.get(purpose.strip().lower())
    if not key:
        await ctx.reply(
            "❌ Неизвестная цель. Доступно: `чат`, `кодинг`, `картинка`.\n"
            "Пример: `!ии_модель кодинг kr/qwen3-coder-next`"
        )
        return
    gdata["ai"].setdefault("models", {})
    if not model or not model.strip() or model.strip().lower() in _FALSE_WORDS | {"сброс", "reset", "default", "по умолчанию"}:
        gdata["ai"]["models"][key] = None
        storage.save()
        await ctx.reply(
            f"♻️ Модель для «{AI_PURPOSE_LABELS[key]}» сброшена на значение по умолчанию: "
            f"`{get_ai_model(gdata, key) or '— не задана —'}`."
        )
        return
    gdata["ai"]["models"][key] = model.strip()[:100]
    storage.save()
    await ctx.reply(
        f"✅ Модель для «{AI_PURPOSE_LABELS[key]}» теперь: `{gdata['ai']['models'][key]}`."
    )


@bot.command(name="ии_тест", aliases=["ai_test", "ии_пинг"])
@admin_only()
async def ai_test(ctx):
    """!ии_тест — проверить связь с ИИ-провайдером и показать точную причину ошибок."""
    if not AI_API_KEY:
        await ctx.reply("🤖 `AI_API_KEY` не задан — задайте его в переменных окружения.")
        return
    gdata = storage.guild(ctx.guild.id)
    model = get_ai_model(gdata, "chat")
    masked = f"{AI_API_KEY[:6]}…{AI_API_KEY[-4:]}" if len(AI_API_KEY) > 12 else "задан"
    lines = [
        "🔌 **Проверка подключения к ИИ**",
        f"• Адрес: `{AI_BASE_URL}/chat/completions`",
        f"• Ключ: `{masked}`",
        f"• Модель (чат): `{model}`",
    ]
    try:
        async with ctx.typing():
            reply = await ask_ai(
                [{"role": "user", "content": "Ответь одним словом: работает"}],
                model=model, temperature=0, max_tokens=16,
            )
        lines.append(f"✅ Ответ получен: {_clip(reply or '(пусто)', 200)}")
    except Exception as exc:
        status = getattr(exc, "status", None)
        print(f"[ИИ/тест] Ошибка (status={status}): {exc}")
        lines.append(f"❌ Ошибка (status={status}): {_clip(str(exc), 400)}")
        lines.append(
            "Подсказка: `status=None` обычно значит, что локальный OmniRoute выключен "
            "или адрес туннеля `AI_BASE_URL` устарел (ссылки trycloudflare.com меняются "
            "при каждом перезапуске)."
        )
    await ctx.reply("\n".join(lines))


def _ai_error_text(status, model: str) -> str:
    """Понятное сообщение об ошибке запроса к ИИ (для команд !код/!картинка)."""
    permanent = {
        400: f"провайдер отклонил запрос (возможно, модель `{model}` не поддерживает эту операцию)",
        401: "ключ ИИ неверный или просрочен",
        402: "на балансе ИИ-провайдера закончились средства",
        403: "провайдер отклонил запрос (проверьте ключ, модель и адрес API)",
        404: f"модель `{model}` или адрес API не найдены (проверьте `AI_BASE_URL`)",
    }
    if status in permanent:
        return (
            f"🤖 ИИ сейчас недоступен: {permanent[status]}. "
            "Проверьте `AI_API_KEY`, `AI_BASE_URL` и модель."
        )
    if status is None:
        # Нет HTTP-статуса — не достучались до провайдера или пришёл не-JSON ответ.
        return (
            "🤖 Не удалось связаться с ИИ-провайдером. Обычно локальный OmniRoute выключен "
            "или адрес туннеля `AI_BASE_URL` устарел (ссылки trycloudflare.com меняются при "
            "каждом перезапуске). Проверьте туннель и команду `!ии_тест`."
        )
    return "🤖 Что-то пошло не так при запросе к ИИ. Попробуйте ещё раз чуть позже."


@bot.command(name="код", aliases=["code", "кодинг"])
async def ai_code_cmd(ctx, *, prompt: str = None):
    """!код <вопрос> — помощь с программированием отдельной моделью для кода."""
    if not AI_API_KEY:
        await ctx.reply(
            "🤖 ИИ не подключён. Админ, задайте `AI_API_KEY` (и при необходимости "
            "`AI_BASE_URL`, `AI_MODEL`)."
        )
        return
    if not prompt or not prompt.strip():
        await ctx.reply("💻 Опишите задачу: `!код напиши функцию быстрой сортировки на Python`")
        return
    gdata = storage.guild(ctx.guild.id)
    model = get_ai_model(gdata, "coding")
    messages = [
        {"role": "system", "content": AI_CODING_SYSTEM},
        {"role": "user", "content": prompt.strip()},
    ]
    try:
        async with ctx.typing():
            answer = await ask_ai(messages, model=model, temperature=0.3, max_tokens=1500)
    except Exception as exc:
        status = getattr(exc, "status", None)
        print(f"[ИИ/код] Ошибка (status={status}): {exc}")
        await ctx.reply(_ai_error_text(status, model))
        return
    if not answer:
        await ctx.reply("🤖 Пустой ответ модели, попробуйте переформулировать.")
        return
    chunks = _split_message(answer)
    await ctx.reply(chunks[0], allowed_mentions=AI_ALLOWED_MENTIONS)
    for extra in chunks[1:]:
        await ctx.send(extra, allowed_mentions=AI_ALLOWED_MENTIONS)


@bot.command(name="картинка", aliases=["нарисуй", "рисуй", "image", "имейдж"])
async def ai_image_cmd(ctx, *, prompt: str = None):
    """!картинка <описание> — сгенерировать изображение выбранной моделью."""
    if not AI_API_KEY:
        await ctx.reply("🤖 ИИ не подключён. Админ, задайте `AI_API_KEY` и `AI_BASE_URL`.")
        return
    if not prompt or not prompt.strip():
        await ctx.reply("🎨 Опишите картинку: `!картинка кот-программист в неоновом городе`")
        return
    gdata = storage.guild(ctx.guild.id)
    model = get_ai_model(gdata, "image")
    if not model:
        await ctx.reply(
            "🎨 Не задана модель для картинок. Админ, укажите её командой "
            "`!ии_модель картинка <модель>` или переменной окружения `AI_IMAGE_MODEL`."
        )
        return
    try:
        async with ctx.typing():
            img_url, raw = await ask_ai_image(prompt.strip(), model)
    except Exception as exc:
        status = getattr(exc, "status", None)
        print(f"[ИИ/картинка] Ошибка (status={status}): {exc}")
        await ctx.reply(_ai_error_text(status, model))
        return
    embed = discord.Embed(
        title="🎨 Готово",
        description=_clip(prompt.strip(), 400),
        color=Colors.LIGHT,
    )
    embed.set_footer(text=f"{BRAND_NAME} • модель: {model}")
    if raw is not None:
        file = discord.File(io.BytesIO(raw), filename="image.png")
        embed.set_image(url="attachment://image.png")
        await ctx.reply(embed=embed, file=file)
    else:
        embed.set_image(url=img_url)
        await ctx.reply(embed=embed)


# ---------------------------------------------------------------------------
# Погода (Open-Meteo — бесплатно, без API-ключа)
# ---------------------------------------------------------------------------
# Коды погоды WMO -> (описание, эмодзи)
WEATHER_CODES = {
    0: ("Ясно", "☀️"),
    1: ("Преимущественно ясно", "🌤️"),
    2: ("Переменная облачность", "⛅"),
    3: ("Пасмурно", "☁️"),
    45: ("Туман", "🌫️"),
    48: ("Изморозь", "🌫️"),
    51: ("Слабая морось", "🌦️"),
    53: ("Морось", "🌦️"),
    55: ("Сильная морось", "🌧️"),
    56: ("Ледяная морось", "🌧️"),
    57: ("Сильная ледяная морось", "🌧️"),
    61: ("Небольшой дождь", "🌦️"),
    63: ("Дождь", "🌧️"),
    65: ("Сильный дождь", "🌧️"),
    66: ("Ледяной дождь", "🌧️"),
    67: ("Сильный ледяной дождь", "🌧️"),
    71: ("Небольшой снег", "🌨️"),
    73: ("Снег", "🌨️"),
    75: ("Сильный снег", "❄️"),
    77: ("Снежная крупа", "🌨️"),
    80: ("Ливень", "🌦️"),
    81: ("Сильный ливень", "🌧️"),
    82: ("Очень сильный ливень", "⛈️"),
    85: ("Снегопад", "🌨️"),
    86: ("Сильный снегопад", "❄️"),
    95: ("Гроза", "⛈️"),
    96: ("Гроза с градом", "⛈️"),
    99: ("Сильная гроза с градом", "⛈️"),
}


@bot.command(name="погода")
async def weather_cmd(ctx, *, city: str = None):
    """!погода <город> — текущая погода и прогноз на день."""
    if not city or not city.strip():
        await ctx.reply("🌦️ Укажите город: `!погода Москва`")
        return
    city = city.strip()
    session = await get_http_session()
    try:
        async with session.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": city, "count": 1, "language": "ru", "format": "json"},
            timeout=aiohttp.ClientTimeout(total=20),
        ) as r:
            geo = await r.json(content_type=None)
        results = (geo or {}).get("results") or []
        if not results:
            await ctx.reply(f"❌ Город «{city}» не найден. Проверьте название и попробуйте снова.")
            return
        loc = results[0]
        lat, lon = loc["latitude"], loc["longitude"]
        async with session.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat,
                "longitude": lon,
                "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m",
                "daily": "temperature_2m_max,temperature_2m_min",
                "timezone": "auto",
                "forecast_days": 1,
            },
            timeout=aiohttp.ClientTimeout(total=20),
        ) as r:
            data = await r.json(content_type=None)
    except Exception as exc:
        print(f"[Погода] Ошибка: {exc}")
        await ctx.reply("⚠️ Сервис погоды сейчас недоступен, попробуйте позже.")
        return

    cur = (data or {}).get("current", {})
    daily = (data or {}).get("daily", {})
    code = int(cur.get("weather_code", 0) or 0)
    desc, emoji = WEATHER_CODES.get(code, ("Неизвестно", "🌡️"))
    place = ", ".join(p for p in (loc.get("name"), loc.get("admin1"), loc.get("country")) if p)

    embed = discord.Embed(
        title=f"{emoji} Погода: {place}",
        description=f"**{desc}**",
        color=Colors.PRIMARY,
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="🌡️ Температура", value=f"{cur.get('temperature_2m', '?')}°C")
    embed.add_field(name="🤔 Ощущается", value=f"{cur.get('apparent_temperature', '?')}°C")
    embed.add_field(name="💧 Влажность", value=f"{cur.get('relative_humidity_2m', '?')}%")
    embed.add_field(name="💨 Ветер", value=f"{cur.get('wind_speed_10m', '?')} км/ч")
    tmax = (daily.get("temperature_2m_max") or [None])[0]
    tmin = (daily.get("temperature_2m_min") or [None])[0]
    if tmax is not None and tmin is not None:
        embed.add_field(name="📈 Макс / 📉 Мин", value=f"{tmax}°C / {tmin}°C")
    brand(embed, ctx.guild)
    await ctx.reply(embed=embed)


# ---------------------------------------------------------------------------
# Развлечения и утилиты
# ---------------------------------------------------------------------------
@bot.command(name="мем")
async def meme_cmd(ctx, subreddit: str = None):
    """!мем [сабреддит] — случайный мем (по умолчанию из популярных)."""
    session = await get_http_session()
    url = "https://meme-api.com/gimme"
    if subreddit:
        url += "/" + re.sub(r"[^A-Za-z0-9_]", "", subreddit)[:50]
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as r:
            data = await r.json(content_type=None)
    except Exception as exc:
        print(f"[Мем] Ошибка: {exc}")
        await ctx.reply("⚠️ Не удалось раздобыть мем, попробуйте ещё раз.")
        return
    if not isinstance(data, dict) or data.get("code") or not data.get("url"):
        await ctx.reply("❌ Мемы не найдены (возможно, неверный сабреддит).")
        return
    if data.get("nsfw"):
        await ctx.reply("🔞 Попался NSFW-мем — пропустил. Попробуйте ещё раз.")
        return
    embed = discord.Embed(
        title=_clip(data.get("title", "Мем"), 256),
        url=data.get("postLink"),
        color=Colors.LIGHT,
    )
    embed.set_image(url=data["url"])
    embed.set_footer(text=f"r/{data.get('subreddit', 'memes')} • 👍 {data.get('ups', 0)}")
    await ctx.reply(embed=embed)


@bot.command(name="аватар", aliases=["ава"])
async def avatar_cmd(ctx, member: discord.Member = None):
    """!аватар [@участник] — показать аватар."""
    member = member or ctx.author
    embed = discord.Embed(title=f"🖼️ Аватар — {member.display_name}", color=Colors.PRIMARY)
    embed.set_image(url=member.display_avatar.url)
    brand(embed, ctx.guild)
    await ctx.reply(embed=embed)


@bot.command(name="юзер", aliases=["профиль"])
async def userinfo_cmd(ctx, member: discord.Member = None):
    """!юзер [@участник] — информация об участнике."""
    member = member or ctx.author
    color = member.color if member.color.value else Colors.PRIMARY
    embed = discord.Embed(title=f"👤 {member}", color=color)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="ID", value=str(member.id))
    embed.add_field(name="Ник", value=member.display_name)
    embed.add_field(name="Бот", value="да" if member.bot else "нет")
    if member.created_at:
        embed.add_field(name="Аккаунт создан", value=f"<t:{int(member.created_at.timestamp())}:R>")
    if member.joined_at:
        embed.add_field(name="Зашёл на сервер", value=f"<t:{int(member.joined_at.timestamp())}:R>")
    roles = [r.mention for r in reversed(member.roles) if r.name != "@everyone"]
    embed.add_field(
        name=f"Роли ({len(roles)})",
        value=(", ".join(roles[:15]) + (" …" if len(roles) > 15 else "")) or "нет",
        inline=False,
    )
    await ctx.reply(embed=embed)


@bot.command(name="сервер", aliases=["сервак"])
async def serverinfo_cmd(ctx):
    """!сервер — информация о сервере."""
    g = ctx.guild
    embed = discord.Embed(title=f"🏠 {g.name}", color=Colors.PRIMARY)
    if g.icon:
        embed.set_thumbnail(url=g.icon.url)
    embed.add_field(name="ID", value=str(g.id))
    embed.add_field(name="Владелец", value=f"<@{g.owner_id}>")
    embed.add_field(name="Участников", value=str(g.member_count))
    embed.add_field(name="Каналов", value=str(len(g.channels)))
    embed.add_field(name="Ролей", value=str(len(g.roles)))
    if g.created_at:
        embed.add_field(name="Создан", value=f"<t:{int(g.created_at.timestamp())}:R>")
    brand(embed, g)
    await ctx.reply(embed=embed)


@bot.command(name="кости", aliases=["ролл", "дайс"])
async def dice_cmd(ctx, spec: str = "1d6"):
    """!кости 2d6 — бросить кубики (кол-во d граней)."""
    m = re.fullmatch(r"\s*(\d{1,2})?\s*[dдк]\s*(\d{1,4})\s*", spec, re.IGNORECASE)
    if not m:
        await ctx.reply("🎲 Формат: `!кости 2d6` — два кубика по 6 граней.")
        return
    count = int(m.group(1) or 1)
    sides = int(m.group(2))
    if not (1 <= count <= 20) or not (2 <= sides <= 1000):
        await ctx.reply("🎲 От 1 до 20 кубиков и от 2 до 1000 граней.")
        return
    rolls = [random.randint(1, sides) for _ in range(count)]
    total = sum(rolls)
    detail = f" ({' + '.join(map(str, rolls))})" if count > 1 else ""
    await ctx.reply(f"🎲 Выпало: **{total}**{detail}")


_EIGHTBALL = [
    "Бесспорно ✅", "Мне кажется — да", "Точно да", "Можешь не сомневаться",
    "Скорее всего", "Хорошие перспективы", "Да", "Знаки говорят «да»",
    "Пока неясно, попробуй ещё раз", "Спроси позже", "Лучше не рассказывать сейчас",
    "Сконцентрируйся и спроси опять", "Даже не думай ❌", "Мой ответ — нет",
    "По моим данным — нет", "Перспективы не очень", "Весьма сомнительно",
]


@bot.command(name="шар", aliases=["8шар", "8ball"])
async def eightball_cmd(ctx, *, question: str = None):
    """!шар <вопрос> — магический шар предсказаний."""
    if not question or not question.strip():
        await ctx.reply("🎱 Задайте вопрос: `!шар Сегодня будет хороший день?`")
        return
    await ctx.reply(f"🎱 {random.choice(_EIGHTBALL)}")


@bot.command(name="выбери", aliases=["choose", "выбор"])
async def choose_cmd(ctx, *, options: str = None):
    """!выбери вариант1 | вариант2 | ... — бот выберет один из вариантов."""
    variants = [o.strip() for o in (options or "").split("|") if o.strip()]
    if len(variants) < 2:
        await ctx.reply("🤔 Дайте варианты через `|`: `!выбери пицца | суши | шаурма`")
        return
    await ctx.reply(f"🤔 Я выбираю: **{random.choice(variants)}**")


@bot.command(name="скажи", aliases=["say", "эхо", "оповещение"])
@staff_only()
async def say_cmd(ctx, channel: Optional[discord.TextChannel] = None, *, text: str = None):
    """!скажи [#канал] <текст> — отправить сообщение от лица бота.

    Массовые пинги (@everyone/@here) и пинги ролей отключены для защиты от злоупотреблений.
    """
    if not text or not text.strip():
        await ctx.reply(
            "🗣️ Использование: `!скажи [#канал] <текст>`\n"
            "Пример: `!скажи #новости Привет всем!`"
        )
        return
    target = channel or ctx.channel
    perms = target.permissions_for(ctx.guild.me)
    if not perms.send_messages:
        await ctx.reply(f"❌ У меня нет прав писать в {target.mention}.")
        return
    # удаляем команду, чтобы сообщение выглядело как от бота
    try:
        await ctx.message.delete()
    except discord.HTTPException:
        pass
    try:
        await target.send(
            text.strip(),
            allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=True),
        )
    except discord.HTTPException:
        await ctx.author.send(f"❌ Не удалось отправить сообщение в {target.mention}.")
        return
    if target.id != ctx.channel.id:
        await ctx.send(f"✅ Отправлено в {target.mention}.", delete_after=5)


@bot.command(name="помощь")
async def help_cmd(ctx):
    embed = discord.Embed(title="📖 Команды бота", color=Colors.PRIMARY)
    embed.add_field(
        name="🎫 Тикеты — настройка (администрация)",
        value=(
            "`!роль_админ <@роль|ID>` — назначить/убрать роль администрации\n"
            "`!роль_поддержки <@роль|ID>` — роль поддержки (видит тикеты)\n"
            "`!категория_тикетов <ID>` — категория Discord для каналов тикетов\n"
            "`!канал_логов <#канал>` — канал логов тикетов\n"
            "`!канал_панели <#канал>` — канал панели создания тикетов\n"
            "`!текст_панели Заголовок | Описание` — текст панели\n"
            "`!панель_тикетов` — опубликовать панель создания тикетов\n"
            "`!панель_админ` — панель администрации\n"
            "`!категории` — изменить категории (текст, эмодзи, описание)\n"
            "`!настройки` • `!статистика` — конфигурация и показатели"
        ),
        inline=False,
    )
    embed.add_field(
        name="🛡️ Модерация",
        value=(
            "`!бан @участник [причина]` — забанить (администрация)\n"
            "`!разбан <ID> [причина]` — разбанить (администрация)\n"
            "`!кик @участник [причина]` — кикнуть (администрация)\n"
            "`!мут @участник <время> [причина]` — мут (поддержка), напр. `10м`, `2ч`, `1д`\n"
            "`!размут @участник [причина]` — снять мут (поддержка)\n"
            "`!варн @участник [причина]` — предупреждение (поддержка); "
            f"на {WARN_LIMIT}-м бот предложит мут/бан/кик\n"
            "`!варны [@участник]` — показать предупреждения\n"
            "`!снятьварн @участник [кол-во|все]` — снять предупреждения"
        ),
        inline=False,
    )
    embed.add_field(
        name="🤖 Автомод",
        value=(
            "`!автомод` — панель настроек (вкл/выкл, пороги)\n"
            "`!канал_модлогов <#канал>` — канал логов модерации/автомода\n"
            "`!автомод_иммунитет <@роль>` — выдать/убрать иммунитет роли\n"
            "Авто-мут за спам и за приглашения Discord (ссылки discord.gg)"
        ),
        inline=False,
    )
    mention = bot.user.mention if bot.user else "@бот"
    embed.add_field(
        name="🧠 ИИ-собеседник (role play)",
        value=(
            f"Упомяните меня ({mention}) в чате — отвечу как ролевой персонаж, "
            "который шарит за мемы 😎 и может тегать участников. Если попросите код или "
            "картинку — сам подберу подходящую модель\n"
            "`!ии [вкл|выкл]` — вкл/выкл ИИ-собеседника (администрация)\n"
            "`!ии_имя <имя>` — сменить образ персонажа (администрация)\n"
            "`!ии_модель <цель> <модель>` — модель под цель: чат/кодинг/картинка (администрация)\n"
            "`!ии_тест` — проверить связь с ИИ-провайдером (администрация)\n"
            "`!код <вопрос>` — помощь с программированием\n"
            "`!картинка <описание>` — сгенерировать изображение\n"
            "`!забудь` — очистить память разговора в канале"
        ),
        inline=False,
    )
    embed.add_field(
        name="🔊 Приватные голосовые комнаты",
        value=(
            "`!приватки_настройка` — авто-создать канал-создатель, категорию и панель\n"
            "`!приватки` — статус и настройки (вкл/выкл: `!приватки вкл|выкл`)\n"
            "`!приватки_создатель <#голосовой>` — назначить канал «Создать комнату»\n"
            "`!приватки_интерфейс <#канал>` — канал с панелью управления\n"
            "`!приватки_лимит <n>` • `!приватки_имя <шаблон>` — лимит и имя комнат\n"
            "`!приватки_панель` — опубликовать панель управления\n"
            "Зашли в канал-создатель → появится личная комната; управляй кнопками "
            "(👑 владелец, 🔐 доступ, 👥 лимит, 🔒 замок, ✏️ имя, 👁️ скрыть, 👢 кик, 🎤 право говорить)"
        ),
        inline=False,
    )
    embed.add_field(
        name="🎁 Розыгрыши",
        value=(
            "`!розыгрыш <приз> | <дата> | <условия> | <описание>` — создать розыгрыш (админ/лучшие раздатели)\n"
            "`!розыгрыши` — список активных и завершенных розыгрышей (администрация)\n"
            "`!розыгрыши_настройка` — настройки системы (вкл/выкл, статистика)\n"
            "Красивый violet UI: участники жмут «🎁 Участвовать!», проверяются условия, "
            "победитель выбирается случайно. Победитель получает ЛС с поздравлением."
        ),
        inline=False,
    )
    embed.add_field(
        name="🎉 Развлечения и утилиты",
        value=(
            "`!скажи [#канал] <текст>` — написать от лица бота (поддержка/администрация)\n"
            "`!погода <город>` — прогноз погоды\n"
            "`!мем [сабреддит]` — случайный мем\n"
            "`!аватар [@]` • `!юзер [@]` • `!сервер` — информация\n"
            "`!кости 2d6` • `!шар <вопрос>` • `!выбери a | b` — веселье"
        ),
        inline=False,
    )
    embed.set_footer(text=f"{BRAND_NAME} • в тикете: Принять • Закрыть • Закрыть с причиной")
    if ctx.guild and ctx.guild.icon:
        embed.set_thumbnail(url=ctx.guild.icon.url)
    await ctx.reply(embed=embed)


# ---------------------------------------------------------------------------
# Запуск
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    bot.run(TOKEN)
