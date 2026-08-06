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
from typing import Optional
from datetime import datetime, timezone, timedelta

import aiohttp
import discord
from discord.ext import commands

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
        "warns": {},                  # предупреждения: {user_id: [{mod_id, reason, ts}, ...]}
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


# ---------------------------------------------------------------------------
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
# Настройка бота и команды
# ---------------------------------------------------------------------------
intents = discord.Intents.default()
intents.message_content = True   # нужно включить в Developer Portal
intents.members = True           # нужно включить в Developer Portal


class TicketBot(commands.Bot):
    async def setup_hook(self):
        # регистрируем постоянные (persistent) панели, чтобы кнопки работали после перезапуска
        self.add_view(TicketPanelView())
        self.add_view(TicketControlView())
        self.add_view(AdminPanelView())

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
        "Отвечай на том же языке, на котором к тебе обратились."
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


async def ask_ai(messages: list, model: str = None, *,
                 temperature: float = 0.9, max_tokens: int = 500) -> str:
    """Запрос к OpenAI-совместимому Chat Completions API. Бросает AIError при ошибке."""
    session = await get_http_session()
    payload = {
        "model": model or AI_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    headers = {
        "Authorization": f"Bearer {AI_API_KEY}",
        "Content-Type": "application/json",
    }
    url = f"{AI_BASE_URL}/chat/completions"
    async with session.post(
        url, json=payload, headers=headers,
        timeout=aiohttp.ClientTimeout(total=90),
    ) as resp:
        data = await resp.json(content_type=None)
        if resp.status != 200:
            detail = ""
            if isinstance(data, dict):
                err = data.get("error")
                detail = err.get("message") if isinstance(err, dict) else str(err or "")
            raise AIError(detail or f"HTTP {resp.status}", status=resp.status)
        try:
            return (data["choices"][0]["message"]["content"] or "").strip()
        except (KeyError, IndexError, TypeError):
            raise AIError("Пустой или неожиданный ответ от ИИ-провайдера.")


async def ask_ai_image(prompt: str, model: str, *, size: str = "1024x1024"):
    """Генерация картинки через OpenAI-совместимый /images/generations.

    Возвращает кортеж (url, raw_bytes): один из элементов может быть None —
    провайдеры отдают либо ссылку, либо base64-данные картинки.
    """
    session = await get_http_session()
    payload = {"model": model, "prompt": prompt, "n": 1, "size": size}
    headers = {
        "Authorization": f"Bearer {AI_API_KEY}",
        "Content-Type": "application/json",
    }
    url = f"{AI_BASE_URL}/images/generations"
    async with session.post(
        url, json=payload, headers=headers,
        timeout=aiohttp.ClientTimeout(total=120),
    ) as resp:
        data = await resp.json(content_type=None)
        if resp.status != 200:
            detail = ""
            if isinstance(data, dict):
                err = data.get("error")
                detail = err.get("message") if isinstance(err, dict) else str(err or "")
            raise AIError(detail or f"HTTP {resp.status}", status=resp.status)
        try:
            item = data["data"][0]
        except (KeyError, IndexError, TypeError):
            raise AIError("Провайдер не вернул изображение.")
        img_url = item.get("url")
        b64 = item.get("b64_json")
        raw = base64.b64decode(b64) if b64 else None
        if not img_url and not raw:
            raise AIError("Пустой ответ генератора изображений.")
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

    history = _ai_history.setdefault(message.channel.id, [])
    history.append({"role": "user", "content": f"{message.author.display_name}: {prompt_text}"})
    # Держим только последние реплики, чтобы не раздувать контекст.
    if len(history) > AI_HISTORY_TURNS * 2:
        del history[: len(history) - AI_HISTORY_TURNS * 2]

    conversation = [
        {"role": "system", "content": _ai_system_prompt(persona, message.guild.name)}
    ] + history

    try:
        async with message.channel.typing():
            reply = await ask_ai(conversation, model=get_ai_model(gdata, "chat"))
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
    system = (
        "Ты — опытный senior-программист и помощник по коду. Отвечай по делу и практично, "
        "на русском языке. Приводи рабочие примеры кода в блоках ``` с указанием языка, "
        "кратко поясняй решение и подводные камни. Без лишней воды."
    )
    messages = [
        {"role": "system", "content": system},
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
            "который шарит за мемы 😎 и может тегать участников\n"
            "`!ии [вкл|выкл]` — вкл/выкл ИИ-собеседника (администрация)\n"
            "`!ии_имя <имя>` — сменить образ персонажа (администрация)\n"
            "`!ии_модель <цель> <модель>` — модель под цель: чат/кодинг/картинка (администрация)\n"
            "`!код <вопрос>` — помощь с программированием\n"
            "`!картинка <описание>` — сгенерировать изображение\n"
            "`!забудь` — очистить память разговора в канале"
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
