"""
bot_core.py
------------
با متدهای رسمی روبیکا (فقط requests) کار می‌کند.

تغییرات این نسخه:
- پارس کپشن «موقعیتی» و دقیق: خط‌به‌خط، بدون نیاز به برچسب‌هایی مثل
  «توضیحات:» — فقط باید تگ #مود (برای عکس مود) یا #ویدئو (برای ویدیو)
  جایی در کپشن باشد.
- جلوگیری از سیل پیام با یک «دفترچهٔ ارسال» سبک (sent_log): قبل از هر
  پیام دوره‌ای (مثل پیام راه‌اندازی)، چک می‌شود که در N دقیقهٔ اخیر
  فرستاده نشده باشد؛ ورودی‌های قدیمی‌تر از ۱ ساعت خودکار پاک می‌شوند.
- سوییچ per-channel «send_file_directly»: روشن = فایل مستقیم در کانال
  پست شود؛ خاموش (پیش‌فرض) = فقط لینک دریافت از ربات نشان داده شود.
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


# ---------------------------------------------------------------------------
# شناسایی کاربر در برابر کانال
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


def prune_known_users(state, config):
    """ورودی‌هایی که در واقع کانال هستن (مثلاً چون قبلاً GUID اشتباه بوده)
    یا مالک ربات هستن رو از known_users پاک می‌کند — خودترمیم‌شونده."""
    channels = known_channel_guids(config)
    owner = config.get("owner_guid")
    users = state.get("known_users", [])
    state["known_users"] = [u for u in users if u not in channels and u != owner]


# ---------------------------------------------------------------------------
# حافظهٔ موقت فایل‌های اخیر — برای پیام‌هایی که بعداً ادیت می‌شوند و در
# آپدیتِ ادیت، اطلاعات فایل همراهش نیست (فقط متن جدید می‌آید)
# ---------------------------------------------------------------------------
RECENT_FILES_TTL_MINUTES = 120


def remember_recent_file(state, message_id, file_id, file_type):
    if not message_id:
        return
    cache = state.setdefault("recent_files", {})
    cache[str(message_id)] = {
        "file_id": file_id,
        "file_type": file_type,
        "seen": tehran_now().strftime("%Y-%m-%d %H:%M"),
    }
    # پاک‌سازی ورودی‌های قدیمی‌تر از RECENT_FILES_TTL_MINUTES
    cutoff = tehran_now().replace(tzinfo=None) - timedelta(minutes=RECENT_FILES_TTL_MINUTES)
    for k in list(cache.keys()):
        try:
            if datetime.strptime(cache[k]["seen"], "%Y-%m-%d %H:%M") < cutoff:
                del cache[k]
        except Exception:
            del cache[k]


def recall_recent_file(state, message_id):
    if not message_id:
        return None
    return state.get("recent_files", {}).get(str(message_id))


# ---------------------------------------------------------------------------
# پارس کپشن — موقعیتی و دقیق
# ---------------------------------------------------------------------------
def _content_lines(caption: str):
    lines = [l.strip() for l in (caption or "").splitlines() if l.strip()]
    return [l for l in lines if not l.startswith("#")]


_LABEL_RE = re.compile(r"^\s*(عنوان|توضیحات|توضیح|ورژن|نسخه)\s*[:：]\s*")


def _strip_label(line: str) -> str:
    """اگر خط با برچسبی مثل «عنوان:» شروع شده باشه، برچسب رو حذف می‌کنه؛
    اگر نه، خودِ خط رو بدون تغییر برمی‌گردونه. یعنی هم فرمت با برچسب و
    هم بدون برچسب پشتیبانی می‌شه."""
    return _LABEL_RE.sub("", line).strip()


def parse_mod_caption(caption: str):
    """فرمت مورد انتظار (هر خط جدا):
    عنوان
    توضیحات
    ورژن
    #مود #<شماره>
    اگر تگ #مود در کپشن نباشد، None برمی‌گردد (یعنی این عکس، عکسِ مود نیست)."""
    caption = caption or ""
    if "#مود" not in caption:
        return None
    lines = _content_lines(caption)
    title = _strip_label(lines[0]) if len(lines) > 0 else ""
    description = _strip_label(lines[1]) if len(lines) > 1 else ""
    version = _strip_label(lines[2]) if len(lines) > 2 else ""
    number_match = re.search(r"#(\d+)\b", caption)
    number = number_match.group(1) if number_match else None
    return {"title": title, "description": description, "version": version, "number": number}


def parse_video_caption(caption: str):
    """فرمت مورد انتظار:
    عنوان
    #ویدئو
    اگر تگ #ویدئو/#ویدیو نباشد، None برمی‌گردد."""
    caption = caption or ""
    if "#ویدئو" not in caption and "#ویدیو" not in caption:
        return None
    lines = _content_lines(caption)
    title = _strip_label(lines[0]) if lines else "ویدیو جدید"
    return {"title": title}


def extract_number(caption: str):
    """شمارهٔ بعد از # را از هر کپشنی (عکس یا فایل) استخراج می‌کند."""
    m = re.search(r"#(\d+)\b", caption or "")
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# ارسال مود / ویدیو به یک کانال مقصد
# ---------------------------------------------------------------------------
def send_mod(token, channel, mod, state):
    lines = []
    if mod.get("title"):
        lines.append(mod["title"])
    if mod.get("description"):
        lines.append(f"📝 {mod['description']}")
    if mod.get("version"):
        lines.append(f"🔢 ورژن: {mod['version']}")

    direct = channel.get("send_file_directly", False)
    if not direct and mod.get("number"):
        lines.append(f"📥 برای دریافت فایل، عدد {mod['number']} یا #{mod['number']} رو به ربات در پیوی بفرستید.")

    lines.append(f"🔗 کانال: {channel['channel_link']}")
    if channel.get("mod_photo_extra_text"):
        lines.append(channel["mod_photo_extra_text"])

    send_file(token, channel["guid"], mod["photo_file_id"], "\n".join(lines))

    if direct and mod.get("number"):
        entry = state.get("files_by_number", {}).get(mod["number"])
        if entry and entry.get("file_id"):
            send_file(token, channel["guid"], entry["file_id"], channel.get("mod_file_caption", ""))


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
# جلوگیری از سیل پیام‌های دوره‌ای (دفترچهٔ ارسال سبک)
# ---------------------------------------------------------------------------
def should_send_now(state, key, min_interval_minutes):
    """اگر برای این key در min_interval_minutes اخیر پیامی ثبت نشده، True
    برمی‌گرداند و زمان الان را ثبت می‌کند. ورودی‌های قدیمی‌تر از ۱ ساعت
    خودکار حذف می‌شوند تا فایل سنگین نشود."""
    log = state.setdefault("sent_log", {})
    now = tehran_now().replace(tzinfo=None)

    last = log.get(key)
    if last:
        try:
            last_dt = datetime.strptime(last, "%Y-%m-%d %H:%M")
            if (now - last_dt) < timedelta(minutes=min_interval_minutes):
                return False
        except Exception:
            pass

    log[key] = now.strftime("%Y-%m-%d %H:%M")

    cutoff = now - timedelta(hours=1)
    for k in list(log.keys()):
        if k == key:
            continue
        try:
            if datetime.strptime(log[k], "%Y-%m-%d %H:%M") < cutoff:
                del log[k]
        except Exception:
            del log[k]

    return True


# ---------------------------------------------------------------------------
# پیام به مالک ربات
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
    notify_owner(token, config, f"⚠️ {len(unnotified)} خطای جدید ثبت شد.\nبرای دیدن جزئیات: /bugs")
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
    delivery = "مستقیم در کانال" if ch.get("send_file_directly") else "از طریق ربات (پیوی)"
    return (
        f"📊 {ch.get('name', guid)}\n"
        f"وضعیت: {status}\n"
        f"روش تحویل فایل: {delivery}\n"
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
