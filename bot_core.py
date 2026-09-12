"""
bot_core.py
------------
با متدهای رسمی روبیکا (فقط requests، بدون کتابخونهٔ واسط) کار می‌کند.

ویژگی‌های این نسخه:
- به‌جای پست‌کردن خودِ فایل توی کانال مقصد، فقط عکس+توضیحات پست می‌شه؛
  کاربرها با فرستادن #شماره یا /شماره به پیوی ربات، فایل رو می‌گیرن.
- تشخیص نوع فایل «سخت‌گیرانه» نیست: هر فایلی که عکس یا ویدیو نباشه،
  به‌عنوان «فایل قابل‌دریافت» در نظر گرفته می‌شه (رفع باگ قبلی).
- پنل مدیریت: با فرستادن کلمهٔ رمز پنل، لیست کانال‌ها میاد؛ با فرستادن
  عدد کانال، جزئیاتش (تعداد پست، تاریخ فعال‌سازی) نشون داده می‌شه.
- پخش همگانی: با فرستادن کلمهٔ رمز، ربات می‌گه پیامتو بفرست؛ پیام بعدی
  برای همهٔ کاربرهایی که تابه‌حال به ربات پیام دادن (به‌جز کانال‌ها)
  فوروارد می‌شه.
- شمارش کاربران یکتا که به ربات پیام داده‌اند.
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


# ---------------------------------------------------------------------------
# فایل‌های JSON
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# ارتباط خام با Rubika Bot API
# ---------------------------------------------------------------------------
def api_call(token, method, payload=None, retries=3, backoff_seconds=2):
    """
    فراخوانی متد API روبیکا.
    خطاهای موقت سرور (502/503/504 یا Timeout) را چند بار با فاصله دوباره
    امتحان می‌کند (باگ قبلی: هر 502 موقت بلافاصله به‌عنوان خطای دائمی ثبت
    می‌شد). اگر پاسخ status != OK باشد، پیام خطای خودِ روبیکا را در exception
    قرار می‌دهد تا در گزارش باگ‌ها قابل‌فهم باشد.
    """
    import time as _time

    url = f"https://botapi.rubika.ir/v3/{token}/{method}"
    last_exc = None

    for attempt in range(1, retries + 1):
        try:
            resp = requests.post(url, json=payload or {}, timeout=REQUEST_TIMEOUT)
            if resp.status_code in (502, 503, 504):
                last_exc = RuntimeError(f"{resp.status_code} موقت از سرور روبیکا (تلاش {attempt}/{retries})")
                _time.sleep(backoff_seconds * attempt)
                continue
            resp.raise_for_status()
            body = resp.json()
            if isinstance(body, dict) and body.get("status") not in (None, "OK"):
                raise RuntimeError(f"روبیکا خطا برگرداند [{method}]: {body}")
            return body.get("data", body) if isinstance(body, dict) else body
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            last_exc = e
            _time.sleep(backoff_seconds * attempt)
            continue

    raise last_exc or RuntimeError(f"فراخوانی {method} بدون دلیل مشخص شکست خورد")


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


# ---------------------------------------------------------------------------
# شناسایی کانال‌های شناخته‌شده (برای تفکیک «کاربر» از «کانال»)
# ---------------------------------------------------------------------------
def known_channel_guids(config):
    guids = {config.get("source_channel_guid")}
    for ch in config.get("destination_channels", []):
        guids.add(ch.get("guid"))
    guids.discard(None)
    return guids


def track_known_user(state, config, chat_id):
    if not chat_id or chat_id in known_channel_guids(config):
        return
    if chat_id == config.get("owner_guid"):
        return
    users = state.setdefault("known_users", [])
    if chat_id not in users:
        users.append(chat_id)


# ---------------------------------------------------------------------------
# پارس کپشن پست‌های کانال منبع
# ---------------------------------------------------------------------------
def parse_caption(caption: str):
    caption = caption or ""
    hashtags = re.findall(r"#\S+", caption)

    desc_match = re.search(r"(?:توضیحات|توضیح)\s*[:：]\s*(.+)", caption)
    description = desc_match.group(1).strip() if desc_match else ""

    version_match = re.search(r"(?:ورژن|نسخه)\s*[:：]\s*(.+)", caption)
    version = version_match.group(1).strip() if version_match else ""

    number_match = re.search(r"#(\d+)\b", caption)
    number = number_match.group(1) if number_match else None

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
        "title": title, "hashtags": hashtags, "description": description,
        "version": version, "number": number,
    }


# ---------------------------------------------------------------------------
# ارسال مود (فقط عکس) / ویدیو به یک کانال مقصد
# ---------------------------------------------------------------------------
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
    if mod.get("number"):
        lines.append(f"📥 برای دریافت فایل، عدد {mod['number']} یا #{mod['number']} رو به ربات در پیوی بفرستید.")
    lines.append(f"🔗 کانال: {channel['channel_link']}")
    if channel.get("mod_photo_extra_text"):
        lines.append(channel["mod_photo_extra_text"])

    send_file(token, channel["guid"], mod["photo_file_id"], "\n".join(lines))


def send_video(token, channel, video):
    lines = [video.get("title", "ویدیو جدید")]
    if channel.get("video_extra_text"):
        lines.append(channel["video_extra_text"])
    send_file(token, channel["guid"], video["video_file_id"], "\n".join(lines))


def pick_item(items, used_ids):
    if not items:
        return None, used_ids
    available = [i for i in items if i["id"] not in used_ids]
    if not available:
        used_ids = []
        available = items
    chosen = __import__("random").choice(available)
    return chosen, used_ids + [chosen["id"]]


# ---------------------------------------------------------------------------
# پیام به مالک ربات — با جلوگیری از سیل پیام
# ---------------------------------------------------------------------------
def notify_owner(token, config, text):
    owner = config.get("owner_guid")
    if not owner or owner.startswith("PUT_YOUR"):
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
        f"⚠️ {len(unnotified)} خطای جدید ثبت شد.\nبرای دیدن جزئیات: /bugs"
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


# ---------------------------------------------------------------------------
# پنل مدیریت
# ---------------------------------------------------------------------------
def build_panel_list(config):
    channels = config.get("destination_channels", [])
    if not channels:
        return "هیچ کانال مقصدی در config.json ثبت نشده."
    lines = ["🎛 پنل مدیریت کانال‌ها", ""]
    for i, ch in enumerate(channels, start=1):
        status = "✅ فعال" if ch.get("enabled", True) else "⛔ غیرفعال"
        lines.append(f"{i}. {ch.get('name', ch['guid'])} — {status}")
    lines.append("\nبرای دیدن جزئیات، عدد همون کانال رو بفرستید.")
    return "\n".join(lines)


def build_channel_detail(config, state, index):
    channels = config.get("destination_channels", [])
    if index < 1 or index > len(channels):
        return "همچین شماره‌ای در لیست نیست."
    ch = channels[index - 1]
    guid = ch["guid"]
    posts_count = len(state.get("used_mods_per_channel", {}).get(guid, []))
    activated = state.get("channel_activated", {}).get(guid, "هنوز فعالیتی ثبت نشده")
    status = "✅ فعال" if ch.get("enabled", True) else "⛔ غیرفعال"
    return (
        f"📊 {ch.get('name', guid)}\n"
        f"وضعیت: {status}\n"
        f"تعداد پست‌های ارسالی: {posts_count}\n"
        f"فعال از: {activated}"
    )


def track_channel_activation(state, config):
    activated = state.setdefault("channel_activated", {})
    for ch in config.get("destination_channels", []):
        guid = ch["guid"]
        if guid not in activated:
            activated[guid] = tehran_now().strftime("%Y-%m-%d %H:%M")


# ---------------------------------------------------------------------------
# ریست دستیِ صف (برای خلاص شدن فوری از انباشت پیام‌های قدیمی)
# ---------------------------------------------------------------------------
def fast_forward_offset(token, state):
    """
    همه‌ی آپدیت‌های در صف مانده را بدون پردازش محتوا رد می‌کند و فقط
    آخرین offset را ذخیره می‌کند. برای زمانی که به‌خاطر باگ قبلی، صف
    خیلی بزرگ شده و کاربر می‌خواهد فوراً از این لحظه به بعد تمیز شروع شود.
    """
    offset = state.get("last_offset_id")
    total_skipped = 0
    for _ in range(50):  # حداکثر ۵۰ صفحه (۵۰ در ۱۰۰ = ۵۰۰۰ پیام) در هر اجرا
        resp = get_updates(token, offset_id=offset, limit=100)
        updates = resp.get("updates", []) if isinstance(resp, dict) else []
        if not updates:
            break
        total_skipped += len(updates)
        next_offset = resp.get("next_offset_id") if isinstance(resp, dict) else None
        if not next_offset:
            last_update = updates[-1]
            last_msg = last_update.get("new_message") or last_update.get("updated_message") or {}
            next_offset = (
                last_update.get("update_id")
                or last_update.get("id")
                or last_msg.get("message_id")
            )
        if not next_offset or next_offset == offset:
            break
        offset = next_offset
        if len(updates) < 100:
            break
    if offset:
        state["last_offset_id"] = offset
    return total_skipped


# ---------------------------------------------------------------------------
# پخش همگانی
# ---------------------------------------------------------------------------
def broadcast_to_users(token, state, text):
    users = state.get("known_users", [])
    sent, failed = 0, 0
    for uid in users:
        try:
            send_message(token, uid, text)
            sent += 1
        except Exception as e:
            failed += 1
            print(f"DEBUG: broadcast failed for {uid}: {e}")
    return sent, failed
