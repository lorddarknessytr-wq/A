"""
bot_core.py
------------
این فایل مستقیماً و فقط با متدهای مستندشدهٔ رسمی روبیکا کار می‌کند
(https://botapi.rubika.ir/v3/{token}/...) — هیچ کتابخونهٔ واسط غیررسمی
استفاده نشده، فقط `requests`. دلیلش: کتابخونه‌های واسط (مثل rubka) مستندات
کامل و قابل‌اطمینانی ندارند و باعث چند باگ قبلی شدند.

توابع اینجا:
- ارتباط خام با API (api_call)
- پارس کپشن پست‌های کانال منبع
- ارسال مود/ویدیو
- گزارش وضعیت/خطا به پیوی مالک (بدون سیل پیام)
"""

import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / "config.json"
STATE_PATH = BASE_DIR / "state.json"

TEHRAN_OFFSET = timedelta(hours=3, minutes=30)
MAX_ERRORS_STORED = 200
ERRORS_PER_PAGE = 5
ERROR_NOTIFY_COOLDOWN_MINUTES = 10
REQUEST_TIMEOUT = 20


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_config():
    return load_json(CONFIG_PATH)


def load_state():
    return load_json(STATE_PATH)


def save_state(state):
    save_json(STATE_PATH, state)


def tehran_now():
    return datetime.now(timezone.utc) + TEHRAN_OFFSET


def api_call(token, method, payload=None):
    url = f"https://botapi.rubika.ir/v3/{token}/{method}"
    resp = requests.post(url, json=payload or {}, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    body = resp.json()
    return body.get("data", body) if isinstance(body, dict) else body


def get_me(token):
    return api_call(token, "getMe")


def send_message(token, chat_id, text):
    return api_call(token, "sendMessage", {"chat_id": chat_id, "text": text})


def send_file(token, chat_id, file_id, text=""):
    return api_call(token, "sendFile", {"chat_id": chat_id, "file_id": file_id, "text": text})


def get_updates(token, offset_id=None, limit=50):
    payload = {"limit": limit}
    if offset_id:
        payload["offset_id"] = offset_id
    return api_call(token, "getUpdates", payload)


def parse_caption(caption: str):
    caption = caption or ""
    hashtags = re.findall(r"#\S+", caption)

    desc_match = re.search(r"(?:توضیحات|توضیح)\s*[:：]\s*(.+)", caption)
    description = desc_match.group(1).strip() if desc_match else ""

    version_match = re.search(r"(?:ورژن|نسخه)\s*[:：]\s*(.+)", caption)
    version = version_match.group(1).strip() if version_match else ""

    title = ""
    for line in caption.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if re.match(r"(?:توضیحات|توضیح|ورژن|نسخه)\s*[:：]", line):
            continue
        title = line
        break

    return {"title": title, "hashtags": hashtags, "description": description, "version": version}


def send_mod(token, channel, mod):
    lines = []
    if mod.get("title"):
        lines.append(mod["title"])
    if mod.get("hashtags"):
        lines.append(" ".join(mod["hashtags"]))
    if mod.get("description"):
        lines.append(f"📝 توضیحات: {mod['description']}")
    if mod.get("version"):
        lines.append(f"🔢 ورژن: {mod['version']}")
    lines.append(f"🔗 کانال: {channel['channel_link']}")
    if channel.get("mod_photo_extra_text"):
        lines.append(channel["mod_photo_extra_text"])

    send_file(token, channel["guid"], mod["photo_file_id"], "\n".join(lines))
    send_file(token, channel["guid"], mod["file_file_id"], channel.get("mod_file_caption", ""))


def send_video(token, channel, video):
    lines = [video.get("title", "ویدیو جدید")]
    if channel.get("video_extra_text"):
        lines.append(channel["video_extra_text"])
    send_file(token, channel["guid"], video["video_file_id"], "\n".join(lines))


def is_video_slot(hour: int, config: dict) -> bool:
    start = config["schedule"]["start_hour_tehran"]
    every_n = config["schedule"]["video_every_n_slots"]
    return (hour - start) % every_n == 0


def pick_item(items, used_ids):
    if not items:
        return None, used_ids
    available = [i for i in items if i["id"] not in used_ids]
    if not available:
        used_ids = []
        available = items
    chosen = __import__("random").choice(available)
    return chosen, used_ids + [chosen["id"]]


def notify_owner(token, config, text):
    owner = config.get("owner_guid")
    if not owner or owner.startswith("c0xYOUR"):
        print("DEBUG: notify_owner skipped — owner_guid تنظیم نشده")
        return
    try:
        send_message(token, owner, text)
        print(f"DEBUG: notify_owner ارسال شد به {owner}")
    except Exception as e:
        print(f"DEBUG: notify_owner failed: {e}")


def log_error(state, category, message):
    err = {
        "id": str(uuid.uuid4())[:6],
        "time": tehran_now().strftime("%Y-%m-%d %H:%M"),
        "category": category,
        "message": str(message)[:300],
        "notified": False,
    }
    state.setdefault("errors", []).append(err)
    state["errors"] = state["errors"][-MAX_ERRORS_STORED:]
    print(f"DEBUG ERROR [{category}]: {message}")
    return err


def maybe_notify_new_errors(token, config, state):
    unnotified = [e for e in state.get("errors", []) if not e.get("notified")]
    if not unnotified:
        return

    last_time = state.get("last_error_notify_time")
    now = tehran_now()
    if last_time:
        try:
            last_dt = datetime.strptime(last_time, "%Y-%m-%d %H:%M")
            if (now.replace(tzinfo=None) - last_dt) < timedelta(minutes=ERROR_NOTIFY_COOLDOWN_MINUTES):
                return
        except Exception:
            pass

    notify_owner(
        token, config,
        f"⚠️ {len(unnotified)} خطای جدید ثبت شد.\n"
        f"برای دیدن جزئیات (دسته‌بندی‌شده، ۵ تا ۵ تا)، به من پیام بده: /bugs"
    )
    for e in unnotified:
        e["notified"] = True
    state["last_error_notify_time"] = now.strftime("%Y-%m-%d %H:%M")


def build_bugs_page(state):
    errors = list(reversed(state.get("errors", [])))
    if not errors:
        return "🎉 هیچ باگی ثبت نشده."

    offset = state.get("bug_page_offset", 0)
    if offset >= len(errors):
        offset = 0

    page = errors[offset: offset + ERRORS_PER_PAGE]
    next_offset = offset + ERRORS_PER_PAGE
    state["bug_page_offset"] = next_offset if next_offset < len(errors) else 0

    by_category = {}
    for e in page:
        by_category.setdefault(e["category"], []).append(e)

    lines = [f"🐞 گزارش باگ‌ها ({offset + 1}-{offset + len(page)} از {len(errors)})"]
    for cat, items in by_category.items():
        lines.append(f"\n📌 دسته: {cat}")
        for e in items:
            lines.append(f"• [{e['time']}] {e['message']}")

    if state["bug_page_offset"] == 0:
        lines.append("\n(به انتهای لیست رسیدید؛ دوباره /bugs بفرستید تا از اول شروع بشه)")
    else:
        lines.append("\nبرای دیدن بعدی، دوباره بنویسید: /bugs")

    return "\n".join(lines)
