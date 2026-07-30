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
- Все команды через префикс "!" и на русском языке.
- Токен читается из переменной окружения DISCORD_TOKEN (или из config.json).

Требования: Python 3.9+, discord.py >= 2.3.2
"""

import io
import re
import json
import os
import copy
import time
import asyncio
from datetime import datetime, timezone, timedelta

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
        "ticket_counter": 0,
        "stats": {"created": 0, "closed": 0, "accepted": 0, "by_category": {}},
        "open_tickets": {},           # {channel_id: {...}}
        "automod": copy.deepcopy(DEFAULT_AUTOMOD),
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
        # мягкая миграция — добавляем недостающие ключи
        base = _default_guild()
        for k, v in base.items():
            if k not in g:
                g[k] = v
        for k, v in base["stats"].items():
            if k not in g["stats"]:
                g["stats"][k] = v
        for k, v in base["automod"].items():
            if k not in g["automod"]:
                g["automod"][k] = v
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


async def can_moderate(ctx, member: discord.Member) -> bool:
    """Проверка иерархии перед наказанием. Возвращает True, если можно."""
    if member.id == ctx.author.id:
        await ctx.reply("❌ Нельзя применить это к самому себе.")
        return False
    if member.id == ctx.guild.me.id:
        await ctx.reply("❌ Нельзя применить это ко мне 🙂")
        return False
    if member.id == ctx.guild.owner_id:
        await ctx.reply("❌ Нельзя наказать владельца сервера.")
        return False
    if ctx.author.id != ctx.guild.owner_id and member.top_role >= ctx.author.top_role:
        await ctx.reply("❌ У этого участника роль выше или равна вашей.")
        return False
    if member.top_role >= ctx.guild.me.top_role:
        await ctx.reply("❌ Роль участника выше моей — не могу его наказать. "
                        "Поднимите роль бота выше в настройках сервера.")
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
            "`!размут @участник [причина]` — снять мут (поддержка)"
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
    embed.set_footer(text=f"{BRAND_NAME} • в тикете: Принять • Закрыть • Закрыть с причиной")
    if ctx.guild and ctx.guild.icon:
        embed.set_thumbnail(url=ctx.guild.icon.url)
    await ctx.reply(embed=embed)


# ---------------------------------------------------------------------------
# Запуск
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    bot.run(TOKEN)
