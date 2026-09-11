"""
bot_core.py
-----------
هسته ربات با استفاده مستقیم از Rubika Bot API v3.
عمداً به کتابخانه rubka وابسته نیستیم تا نام/امضای متدهای کتابخانه
باعث ناسازگاری با مستندات رسمی API نشود.
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
API_BASE = "https://botapi.rubika.ir/v3"


class RubikaAPIError(RuntimeError):
    pass


class RubikaBot:
    """Wrapper کوچک و مستقیم برای متدهای رسمی Bot API v3."""

    def __init__(self, token: str):
        if not token:
            raise ValueError("RUBIKA_BOT_TOKEN تنظیم نشده است.")
        self.token = token.strip()
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})

    def _post(self, method: str, data=None):
        url = f"{API_BASE}/{self.token}/{method}"
        response = self.session.post(url, json=data or {}, timeout=60)
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError as exc:
            raise RubikaAPIError(
                f"{method}: پاسخ JSON معتبر نبود: {response.text[:300]}"
            ) from exc

        # در API روبیکا، خطای API را با status/data برمی‌گردانند؛
        # این بررسی متن خطا را برای Actions واضح‌تر می‌کند.
        if isinstance(payload, dict):
            status = str(payload.get("status", "")).lower()
            if status and status not in ("ok", "success"):
                raise RubikaAPIError(f"{method}: {payload}")

        return payload

    def get_me(self):
        return self._post("getMe")

    def send_message(self, chat_id, text, **kwargs):
        data = {"chat_id": chat_id, "text": text}
        for key in (
            "chat_keypad", "disable_notification", "inline_keypad",
            "reply_to_message_id", "chat_keypad_type", "metadata"
        ):
            if key in kwargs and kwargs[key] is not None:
                data[key] = kwargs[key]
        return self._post("sendMessage", data)

    def get_updates(self, offset_id=None, limit=50):
        data = {"limit": limit}
        if offset_id:
            data["offset_id"] = offset_id
        return self._post("getUpdates", data)

    def send_file(self, chat_id, file_id, text="", **kwargs):
        data = {"chat_id": chat_id, "file_id": file_id}
        if text:
            data["text"] = text
        for key in (
            "reply_to_message_id", "disable_notification",
            "chat_keypad", "inline_keypad", "chat_keypad_type"
        ):
            if key in kwargs and kwargs[key] is not None:
                data[key] = kwargs[key]
        return self._post("sendFile", data)

    def send_image(self, chat_id, file_id, text=""):
        # طبق API رسمی، ارسال فایل/مدیای موجود با sendFile انجام می‌شود.
        return self.send_file(chat_id, file_id, text)

    def send_document(self, chat_id, file_id, text=""):
        return self.send_file(chat_id, file_id, text)

    def get_file(self, file_id):
        return self._post("getFile", {"file_id": file_id})

    def edit_message_text(self, chat_id, message_id, text):
        return self._post(
            "editMessageText",
            {"chat_id": chat_id, "message_id": message_id, "text": text},
        )

    def _upload_url(self, file_type):
        result = self._post("requestSendFile", {"type": file_type})
        data = result.get("data", result) if isinstance(result, dict) else {}
        upload_url = data.get("upload_url")
        if not upload_url:
            raise RubikaAPIError(f"requestSendFile: upload_url پیدا نشد: {result}")
        return upload_url

    def upload_file(self, path, file_type="File"):
        upload_url = self._upload_url(file_type)
        p = Path(path)
        with p.open("rb") as f:
            response = self.session.post(
                upload_url,
                files={"file": (p.name, f)},
                timeout=300,
            )
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data", payload) if isinstance(payload, dict) else {}
        file_id = data.get("file_id")
        if not file_id:
            raise RubikaAPIError(f"آپلود فایل: file_id پیدا نشد: {payload}")
        return file_id


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


def parse_caption(caption: str):
    caption = caption or ""
    hashtags = re.findall(r"#\S+", caption)

    desc_match = re.search(r"(?:توضیحات|توضیح)\s*[:：]\s*(.+)", caption)
    description = desc_match.group(1).strip() if desc_match else ""

    version_match = re.search(r"(?:ورژن|نسخه)\s*[:：]\s*(\S+)", caption)
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

    return {
        "title": title,
        "hashtags": hashtags,
        "description": description,
        "version": version,
    }


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
    if channel.get("channel_link"):
        lines.append(f"🔗 کانال: {channel['channel_link']}")
    if channel.get("mod_photo_extra_text"):
        lines.append(channel["mod_photo_extra_text"])

    bot.send_image(
        chat_id=channel["guid"],
        file_id=mod["photo_file_id"],
        text="\n".join(lines),
    )
    bot.send_document(
        chat_id=channel["guid"],
        file_id=mod["file_file_id"],
        text=channel.get("mod_file_caption", ""),
    )


def send_video(bot, channel, video):
    lines = [video.get("title", "ویدیو جدید")]
    if channel.get("video_extra_text"):
        lines.append(channel["video_extra_text"])
    bot.send_file(
        chat_id=channel["guid"],
        file_id=video["video_file_id"],
        text="\n".join(lines),
    )


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


def notify_owner(bot, config, text):
    owner = config.get("owner_guid")
    if not owner or owner.startswith("c0xYOUR"):
        return
    try:
        bot.send_message(chat_id=owner, text=text)
    except Exception as exc:
        print(f"[WARN] ارسال پیام به owner شکست خورد: {exc}")


def log_error(state, category, message):
    err = {
        "id": str(uuid.uuid4())[:6],
        "time": tehran_now().strftime("%Y-%m-%d %H:%M"),
        "category": category,
        "message": str(message)[:500],
        "notified": False,
    }
    state.setdefault("errors", []).append(err)
    state["errors"] = state["errors"][-MAX_ERRORS_STORED:]
    return err


def maybe_notify_new_errors(bot, config, state):
    unnotified = [e for e in state.get("errors", []) if not e.get("notified")]
    if not unnotified:
        return

    last_time = state.get("last_error_notify_time")
    now = tehran_now()
    if last_time:
        try:
            last_dt = datetime.strptime(last_time, "%Y-%m-%d %H:%M")
            if (now.replace(tzinfo=None) - last_dt) < timedelta(
                minutes=ERROR_NOTIFY_COOLDOWN_MINUTES
            ):
                return
        except Exception:
            pass

    notify_owner(
        bot,
        config,
        f"⚠️ {len(unnotified)} خطای جدید ثبت شد.\n"
        f"برای جزئیات به من پیام بده: /bugs",
    )
    for e in unnotified:
        e["notified"] = True
    state["last_error_notify_time"] = now.strftime("%Y-%m-%d %H:%M")


def build_bugs_page(state, offset=0):
    errors = list(reversed(state.get("errors", [])))
    page = errors[offset: offset + ERRORS_PER_PAGE]
    has_more = len(errors) > offset + ERRORS_PER_PAGE

    if not page:
        return "🎉 هیچ باگی ثبت نشده.", False

    by_category = {}
    for e in page:
        by_category.setdefault(e["category"], []).append(e)

    lines = [f"🐞 گزارش باگ‌ها ({offset + 1}-{offset + len(page)} از {len(errors)})"]
    for cat, items in by_category.items():
        lines.append(f"\n📌 دسته: {cat}")
        for e in items:
            lines.append(f"• [{e['time']}] {e['message']}")

    return "\n".join(lines), has_more
