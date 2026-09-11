"""Rubika mod/video bot core using the Rubka Python library."""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from rubka import Robot

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "rubika_bot.db"

# =========================
# تنظیمات ربات
# =========================
# توکن را در GitHub Actions Secret با نام RUBIKA_BOT_TOKEN قرار بده.
BOT_TOKEN = ""

# بعد از اجرای /myid، Chat ID خودت را اینجا قرار بده.
ADMIN_CHAT_ID = ""

# کانال منبعی که مود/ویدیو در آن قرار می‌گیرد.
SOURCE_CHANNEL_ID = ""

# کانال‌های مقصد.
DESTINATION_CHANNELS = [
    # {
    #     "id": "",
    #     "name": "کانال یک",
    #     "link": "https://rubika.ir/your_channel",
    #     "mod_photo_text": "🔥 مود ویژه امروز",
    #     "mod_file_caption": "دانلود کنید 🎮",
    #     "video_text": "🎬 ویدیوی امروز",
    # },
]

START_HOUR = 11
END_HOUR = 23
VIDEO_EVERY_N_SLOTS = 4

MAX_ERRORS = 200
ERRORS_PER_PAGE = 5
TEHRAN_OFFSET = timedelta(hours=3, minutes=30)


# =========================
# دیتابیس داخلی
# =========================
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS items (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            data TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS errors (
            id TEXT PRIMARY KEY,
            time TEXT NOT NULL,
            category TEXT NOT NULL,
            message TEXT NOT NULL,
            notified INTEGER NOT NULL DEFAULT 0
        )
    """)
    conn.commit()
    return conn


def setting_get(key, default=None):
    with db() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return default if row is None else row["value"]


def setting_set(key, value):
    with db() as conn:
        conn.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )
        conn.commit()


def add_item(kind, data):
    item_id = data.get("id") or uuid.uuid4().hex[:10]
    data = dict(data)
    data["id"] = item_id
    with db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO items(id,kind,data) VALUES(?,?,?)",
            (item_id, kind, json.dumps(data, ensure_ascii=False)),
        )
        conn.commit()
    return item_id


def list_items(kind):
    with db() as conn:
        rows = conn.execute("SELECT data FROM items WHERE kind=?", (kind,)).fetchall()
    return [json.loads(row["data"]) for row in rows]


def clear_items(kind):
    with db() as conn:
        conn.execute("DELETE FROM items WHERE kind=?", (kind,))
        conn.commit()


def log_error(category, message):
    error_id = uuid.uuid4().hex[:8]
    with db() as conn:
        conn.execute(
            "INSERT INTO errors(id,time,category,message) VALUES(?,?,?,?)",
            (
                error_id,
                tehran_now().strftime("%Y-%m-%d %H:%M"),
                category,
                str(message)[:700],
            ),
        )
        conn.execute(
            "DELETE FROM errors WHERE id NOT IN "
            "(SELECT id FROM errors ORDER BY time DESC LIMIT ?)",
            (MAX_ERRORS,),
        )
        conn.commit()
    return error_id


def build_bugs_page():
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM errors ORDER BY time DESC LIMIT ?", (ERRORS_PER_PAGE,)
        ).fetchall()
        total = conn.execute("SELECT COUNT(*) AS n FROM errors").fetchone()["n"]

    if not rows:
        return "🎉 هیچ باگی ثبت نشده است."

    lines = [f"🐞 آخرین باگ‌ها (۵ مورد از {total})"]
    for row in rows:
        lines.append(
            f"\n📌 {row['category']}\n"
            f"🕐 {row['time']}\n"
            f"• {row['message']}"
        )
    return "\n".join(lines)


# =========================
# ابزارهای Rubka
# =========================
def make_bot():
    import os
    token = os.getenv("RUBIKA_BOT_TOKEN", "").strip() or BOT_TOKEN.strip()
    if not token:
        raise RuntimeError(
            "توکن ربات تنظیم نشده است. Secret با نام RUBIKA_BOT_TOKEN را تنظیم کن."
        )
    return Robot(token=token)


def tehran_now():
    return datetime.now(timezone.utc) + TEHRAN_OFFSET


def parse_caption(text):
    text = text or ""
    hashtags = re.findall(r"#\S+", text)
    desc = re.search(r"(?:توضیحات|توضیح)\s*[:：]\s*(.+)", text)
    version = re.search(r"(?:ورژن|نسخه)\s*[:：]\s*(\S+)", text)

    title = ""
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if re.match(r"(?:توضیحات|توضیح|ورژن|نسخه)\s*[:：]", line):
            continue
        title = line
        break

    return {
        "title": title,
        "hashtags": hashtags,
        "description": desc.group(1).strip() if desc else "",
        "version": version.group(1).strip() if version else "",
    }


def get_updates(bot, limit=50):
    """Rubka's get_updates() is the source of incoming bot updates."""
    offset = setting_get("offset_id")
    result = bot.get_updates(offset_id=offset, limit=limit)
    if not isinstance(result, dict):
        return [], None

    data = result.get("data") if isinstance(result.get("data"), dict) else result
    updates = data.get("updates") or []
    next_offset = data.get("next_offset_id")
    return updates if isinstance(updates, list) else [], next_offset


def update_chat_id(update):
    msg = update.get("new_message") or update.get("updated_message") or {}
    return msg.get("chat_id") or update.get("chat_id")


def update_message(update):
    msg = update.get("new_message") or update.get("updated_message")
    return msg if isinstance(msg, dict) else None


def message_text(message):
    return (message.get("text") or "").strip()


def message_file(message):
    info = message.get("file") or {}
    if not isinstance(info, dict):
        return None, None
    return info.get("file_id"), info.get("file_type")


def reply(bot, chat_id, text):
    return bot.send_message(chat_id=chat_id, text=text)


# =========================
# دستورها
# =========================
def handle_myid(bot, chat_id):
    # chat_id همان شناسه چتی است که پیام از آن دریافت شده.
    reply(
        bot,
        chat_id,
        "🆔 Chat ID شما:\n\n"
        f"{chat_id}\n\n"
        "این مقدار را برای بخش ADMIN_CHAT_ID استفاده کن.\n"
        "⚠️ User GUID با Chat ID یکی نیست؛ برای ارسال پیام به همین گفت‌وگو، Chat ID لازم است.",
    )


def handle_start(bot, chat_id):
    reply(
        bot,
        chat_id,
        "سلام 👋\n"
        "ربات فعال است.\n\n"
        "/myid — دریافت Chat ID همین گفتگو\n"
        "/bugs — آخرین خطاهای ربات\n"
        "/status — وضعیت ربات",
    )


def handle_status(bot, chat_id):
    mods = len(list_items("mod"))
    videos = len(list_items("video"))
    with db() as conn:
        errors = conn.execute("SELECT COUNT(*) AS n FROM errors").fetchone()["n"]
    reply(
        bot,
        chat_id,
        "✅ ربات فعال است.\n"
        f"🕐 تهران: {tehran_now():%Y-%m-%d %H:%M}\n"
        f"🎮 مودها: {mods}\n"
        f"🎬 ویدیوها: {videos}\n"
        f"🐞 خطاها: {errors}",
    )


def handle_owner_command(bot, chat_id, text):
    if text == "/myid":
        handle_myid(bot, chat_id)
        return True
    if text == "/start":
        handle_start(bot, chat_id)
        return True
    if text == "/bugs":
        reply(bot, chat_id, build_bugs_page())
        return True
    if text == "/status":
        handle_status(bot, chat_id)
        return True
    return False


# =========================
# دریافت مود/ویدیو از کانال منبع
# =========================
def handle_source_message(message):
    file_id, file_type = message_file(message)
    if not file_id:
        return

    parsed = parse_caption(message_text(message))

    if file_type == "Image":
        setting_set(
            "pending_photo",
            json.dumps({"file_id": file_id, **parsed}, ensure_ascii=False),
        )
        return

    if file_type == "Video":
        add_item(
            "video",
            {
                "video_file_id": file_id,
                "title": parsed["title"] or message_text(message) or "ویدیو جدید",
            },
        )
        setting_set("pending_photo", "")
        return

    if file_type in ("File", "Music", "Voice", "Gif"):
        pending_raw = setting_get("pending_photo", "")
        if not pending_raw:
            return
        pending = json.loads(pending_raw)
        add_item(
            "mod",
            {
                "photo_file_id": pending["file_id"],
                "file_file_id": file_id,
                "title": pending.get("title", ""),
                "hashtags": pending.get("hashtags", []),
                "description": pending.get("description", ""),
                "version": pending.get("version", ""),
            },
        )
        setting_set("pending_photo", "")


# =========================
# انتشار
# =========================
def pick_unused(kind, channel_id):
    items = list_items(kind)
    if not items:
        return None

    key = f"used:{kind}:{channel_id}"
    used = set(json.loads(setting_get(key, "[]")))
    available = [item for item in items if item["id"] not in used]
    if not available:
        used.clear()
        available = items

    # انتخاب پایدار و ساده بر اساس زمان فعلی.
    item = available[int(tehran_now().timestamp()) % len(available)]
    used.add(item["id"])
    setting_set(key, json.dumps(sorted(used)))
    return item


def send_mod(bot, channel, mod):
    lines = []
    if mod.get("title"):
        lines.append(mod["title"])
    if mod.get("hashtags"):
        lines.append(" ".join(mod["hashtags"]))
    if mod.get("description"):
        lines.append(f"📝 توضیحات: {mod['description']}")
    if mod.get("version"):
        lines.append(f"🔢 ورژن: {mod['version']}")
    if channel.get("link"):
        lines.append(f"🔗 کانال: {channel['link']}")
    if channel.get("mod_photo_text"):
        lines.append(channel["mod_photo_text"])

    bot.send_image(
        chat_id=channel["id"],
        file_id=mod["photo_file_id"],
        text="\n".join(lines),
    )
    bot.send_document(
        chat_id=channel["id"],
        file_id=mod["file_file_id"],
        text=channel.get("mod_file_caption", ""),
    )


def send_video(bot, channel, video):
    text = video.get("title", "ویدیو جدید")
    if channel.get("video_text"):
        text += "\n" + channel["video_text"]
    bot.send_document(
        chat_id=channel["id"],
        file_id=video["video_file_id"],
        text=text,
    )


def is_video_slot(hour):
    return (hour - START_HOUR) % VIDEO_EVERY_N_SLOTS == 0


def run_schedule(bot):
    now = tehran_now()
    if not (START_HOUR <= now.hour <= END_HOUR):
        return

    slot_key = now.strftime("%Y-%m-%d-%H")
    if setting_get("last_post_slot") == slot_key:
        return

    posted_any = False
    video_slot = is_video_slot(now.hour)

    for channel in DESTINATION_CHANNELS:
        channel_id = channel.get("id", "").strip()
        if not channel_id:
            continue
        try:
            kind = "video" if video_slot else "mod"
            item = pick_unused(kind, channel_id)
            if not item:
                continue
            if kind == "video":
                send_video(bot, channel, item)
            else:
                send_mod(bot, channel, item)
            posted_any = True
        except Exception as exc:
            log_error(f"ارسال به {channel.get('name', channel_id)}", exc)

    # حتی اگر چیزی برای ارسال نبود، همین slot دوباره در همان ساعت تکرار نشود.
    setting_set("last_post_slot", slot_key)
    if posted_any and ADMIN_CHAT_ID:
        try:
            reply(bot, ADMIN_CHAT_ID, f"📤 انتشار ساعت {now.hour}:00 انجام شد.")
        except Exception as exc:
            log_error("گزارش انتشار", exc)


# =========================
# اجرای اصلی
# =========================
def install_commands(bot):
    try:
        bot.set_commands([
            {"command": "start", "description": "شروع ربات"},
            {"command": "myid", "description": "دریافت Chat ID"},
            {"command": "status", "description": "وضعیت ربات"},
            {"command": "bugs", "description": "نمایش خطاها"},
        ])
    except Exception as exc:
        # نبودن set_commands نباید کل ربات را متوقف کند.
        log_error("set_commands", exc)


def process_updates(bot, updates):
    for update in updates:
        message = update_message(update)
        if not message:
            continue

        chat_id = update_chat_id(update)
        text = message_text(message)

        try:
            # /myid باید حتی قبل از تعیین ADMIN_CHAT_ID هم کار کند.
            if text == "/myid":
                handle_myid(bot, chat_id)
                continue

            if ADMIN_CHAT_ID and chat_id == ADMIN_CHAT_ID:
                if handle_owner_command(bot, chat_id, text):
                    continue

            if SOURCE_CHANNEL_ID and chat_id == SOURCE_CHANNEL_ID:
                handle_source_message(message)
        except Exception as exc:
            log_error("پردازش پیام", exc)


def main():
    bot = make_bot()

    # احراز هویت واقعی با متد Rubka.
    me = bot.get_me()
    print("Rubka getMe OK:", me)

    install_commands(bot)

    updates, next_offset = get_updates(bot)
    print("Updates received:", len(updates))
    if next_offset:
        setting_set("offset_id", next_offset)

    process_updates(bot, updates)
    run_schedule(bot)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log_error("خطای اصلی", exc)
        raise
