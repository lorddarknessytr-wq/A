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


class RubikaAPIError(RuntimeError):
    """An error response returned by Rubika (HTTP 200 is not enough)."""


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
    try:
        body = resp.json()
    except ValueError as exc:
        raise RubikaAPIError(f"{method}: پاسخ JSON معتبر نیست") from exc

    # The API can return an error envelope with HTTP 200.  The old code
    # discarded that envelope and consequently reported a post as successful
    # although Rubika had rejected it (for example, when the bot is not an
    # admin of the destination channel).
    if isinstance(body, dict):
        status = body.get("status")
        if status is not None and str(status).upper() not in {"OK", "SUCCESS"}:
            detail = body.get("status_det") or body.get("message") or body.get("error") or body
            raise RubikaAPIError(f"{method}: {detail}")
        if body.get("error") and "data" not in body:
            raise RubikaAPIError(f"{method}: {body['error']}")
        return body.get("data", body)
    return body


def get_me(token):
    return api_call(token, "getMe")


def send_message(token, chat_id, text):
    return api_call(token, "sendMessage", {"chat_id": chat_id, "text": text})


def send_file(token, chat_id, file_id, text=""):
    return api_call(token, "sendFile", {"chat_id": chat_id, "file_id": file_id, "text": text})


def forward_message(token, chat_id, from_chat_id, message_id):
    """Forward the original media message; support both Rubika API variants."""
    try:
        return api_call(token, "forwardMessage", {
            "chat_id": chat_id,
            "from_chat_id": from_chat_id,
            "message_id": message_id,
        })
    except (requests.RequestException, RubikaAPIError) as first_error:
        # Some Rubika Bot API releases expose the batch spelling instead.
        try:
            return api_call(token, "forwardMessages", {
                "to_chat_id": chat_id,
                "from_chat_id": from_chat_id,
                "message_ids": [message_id],
            })
        except (requests.RequestException, RubikaAPIError):
            raise first_error


def deliver_media(token, chat_id, entry, fallback_file_id=None, caption=""):
    """Deliver media without falsely assuming a received ``file_id`` is uploadable.

    Rubika rejects those IDs with ``INVALID_ACCESS/file_id is not valid``. A
    forward uses the message stored in the source channel and is therefore the
    supported path for newly collected content. We intentionally never retry
    old received IDs with sendFile: the API explicitly rejects them and a
    retry only creates repeated INVALID_ACCESS errors.
    """
    source_chat_id = entry.get("source_chat_id") if isinstance(entry, dict) else None
    source_message_id = entry.get("source_message_id") if isinstance(entry, dict) else None
    if source_chat_id and source_message_id:
        result = forward_message(token, chat_id, source_chat_id, source_message_id)
        if caption:
            send_message(token, chat_id, caption)
        return result
    raise RubikaAPIError(
        "فایل قدیمی message_id کانال منبع ندارد و قابل فوروارد نیست؛ "
        "آن را یک‌بار دوباره در کانال منبع ارسال کنید."
    )


def get_updates(token, offset_id=None, limit=50):
    payload = {"limit": limit}
    if offset_id:
        payload["offset_id"] = offset_id
    return api_call(token, "getUpdates", payload)


def get_chat_member(token, channel_guid, user_guid, method="getChatMember", user_id_key="user_id"):
    """Query a configured membership endpoint.

    Rubika deployments do not all expose this method.  Callers must treat a
    failure as *not verified*, never as a successful membership check.
    """
    return api_call(token, method, {"chat_id": channel_guid, user_id_key: user_guid})


def member_is_active(response, user_guid=None):
    """Accept common Bot API member shapes, but reject unknown responses."""
    if not isinstance(response, dict):
        return False
    member = next((response[key] for key in ("member", "chat_member", "chatMember", "participant")
                   if isinstance(response.get(key), dict)), response)
    status = str(member.get("status") or member.get("member_status") or member.get("state") or "").lower()
    if status:
        return status not in {"left", "kicked", "banned", "removed", "not_member"}
    if member.get("is_member") is True or member.get("in_chat") is True:
        return True
    # Several Rubika responses contain the member's object directly, without
    # a status field. Accept it only when it is the exact requested user.
    if user_guid:
        for key in ("user_id", "user_guid", "object_guid", "member_id"):
            if str(member.get(key, "")) == str(user_guid):
                return True
        user = member.get("user")
        if isinstance(user, dict):
            return any(str(user.get(key, "")) == str(user_guid)
                       for key in ("user_id", "user_guid", "object_guid", "id"))
    return False


def required_memberships(token, config, user_guid):
    """Return (allowed, missing_channels). Disabled means no restriction."""
    settings = config.get("forced_join", {})
    if not settings.get("enabled", False):
        return True, []
    channels = settings.get("channels", [])
    if not channels:
        # A misconfigured enabled gate must never accidentally open access.
        return False, []
    method = settings.get("membership_method", "getChatMember")
    user_id_key = settings.get("membership_user_id_key", "user_id")
    missing = []
    for channel in channels:
        guid = channel.get("guid")
        if not guid:
            missing.append(channel)
            continue
        try:
            if not member_is_active(get_chat_member(token, guid, user_guid, method, user_id_key), user_guid):
                missing.append(channel)
        except Exception as exc:
            print(f"DEBUG: membership check failed for {guid}: {exc}")
            missing.append(channel)
    return not missing, missing


def build_join_required_message(channels):
    lines = ["🔒 برای دریافت فایل، ابتدا در کانال‌های زیر عضو شوید:"]
    for channel in channels:
        link = channel.get("link") or channel.get("channel_link")
        name = channel.get("name") or channel.get("guid", "کانال")
        lines.append(f"• {name}" + (f": {link}" if link else ""))
    lines.append("پس از عضویت، دوباره /start یا شمارهٔ فایل را بفرستید.")
    return "\n".join(lines)


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
        return False
    if chat_id == config.get("owner_guid"):
        return False
    users = state.setdefault("known_users", [])
    if chat_id not in users:
        users.append(chat_id)
        return True
    return False


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


_MOD_TAG_RE = re.compile(r"(?:^|\s)#مود(?:\s|$)")
_VIDEO_TAG_RE = re.compile(r"(?:^|\s)#(?:ویدئو|ویدیو)(?:\s|$)")


def parse_mod_caption(caption: str):
    """فرمت مورد انتظار (هر خط جدا):
    عنوان
    توضیحات
    ورژن
    #مود #<شماره>
    اگر تگ دقیق #مود در کپشن نباشد (نه به‌عنوان بخشی از هشتگ دیگه مثل
    #مود_فایل)، None برمی‌گردد."""
    caption = caption or ""
    if not _MOD_TAG_RE.search(caption):
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
    اگر تگ دقیق #ویدئو/#ویدیو نباشد، None برمی‌گردد."""
    caption = caption or ""
    if not _VIDEO_TAG_RE.search(caption):
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

    deliver_media(token, channel["guid"], mod, mod.get("photo_file_id"), "\n".join(lines))

    if direct and mod.get("number"):
        entry = state.get("files_by_number", {}).get(mod["number"])
        if entry and entry.get("file_id"):
            deliver_media(token, channel["guid"], entry, entry.get("file_id"), channel.get("mod_file_caption", ""))


def send_video(token, channel, video):
    lines = [video.get("title", "ویدیو جدید")]
    if channel.get("video_extra_text"):
        lines.append(channel["video_extra_text"])
    deliver_media(token, channel["guid"], video, video.get("video_file_id"), "\n".join(lines))


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
def get_chat(token, chat_id):
    return api_call(token, "getChat", {"chat_id": chat_id})


def set_chat_keypad(token, chat_id, buttons):
    """کیبورد ثابت پایین صفحه رو تنظیم می‌کنه. buttons یه لیست از متن دکمه‌هاست."""
    rows = [{"buttons": [{"id": str(i), "type": "Simple", "button_text": t}]} for i, t in enumerate(buttons)]
    payload = {
        "chat_id": chat_id,
        "chat_keypad_type": "New",
        "chat_keypad": {"rows": rows, "resize_keyboard": True, "one_time_keyboard": False},
    }
    return api_call(token, "editChatKeypad", payload)


def channel_plan(channel):
    """Return cadence for a per-channel plan; explicit values override it."""
    plans = {
        0: {"mod_interval_minutes": 120, "video_interval_minutes": 270},
        1: {"mod_interval_minutes": 60, "video_interval_minutes": 180},
        2: {"mod_interval_minutes": 30, "video_interval_minutes": 120},
    }
    plan = plans.get(int(channel.get("posting_plan", 1)), plans[1]).copy()
    plan.update(channel.get("posting_schedule", {}))
    return plan


def is_channel_active_now(channel, now):
    schedule = channel.get("posting_schedule", {})
    return int(schedule.get("start_hour_tehran", 9)) <= now.hour < int(schedule.get("end_hour_tehran", 23))


def is_due(last_timestamp, interval_minutes, now):
    """Use elapsed time, so a delayed GitHub Actions run catches up safely."""
    if not last_timestamp:
        return True
    try:
        last = datetime.strptime(last_timestamp, "%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return True
    return now.replace(tzinfo=None) - last >= timedelta(minutes=int(interval_minutes))


# ---------------------------------------------------------------------------
# پخش پیام به همهٔ کانال‌های مقصد
# ---------------------------------------------------------------------------
def broadcast_to_channels(token, config, text):
    sent, failed = 0, 0
    for ch in config.get("destination_channels", []):
        if not ch.get("enabled", True):
            continue
        try:
            send_message(token, ch["guid"], text)
            sent += 1
        except Exception as e:
            failed += 1
            print(f"DEBUG: channelcast failed for {ch.get('name')}: {e}")
    return sent, failed


# ---------------------------------------------------------------------------
# مسدودسازی کاربران
# ---------------------------------------------------------------------------
def is_blocked(state, chat_id):
    """اگه کاربر مسدوده، دیکشنری اطلاعات مسدودی رو برمی‌گردونه؛ وگرنه None.
    اگه تاریخ انقضا گذشته باشه، خودکار از لیست حذف می‌شه."""
    blocked = state.get("blocked_users", {})
    info = blocked.get(chat_id)
    if not info:
        return None
    until = info.get("until")
    if until:
        try:
            until_dt = datetime.strptime(until, "%Y-%m-%d %H:%M")
            if tehran_now().replace(tzinfo=None) >= until_dt:
                del blocked[chat_id]
                return None
        except Exception:
            pass
    return info


def block_user(state, chat_id, reason, days=None):
    blocked_at = tehran_now().strftime("%Y-%m-%d %H:%M")
    until = None
    if days:
        until = (tehran_now().replace(tzinfo=None) + timedelta(days=float(days))).strftime("%Y-%m-%d %H:%M")
    state.setdefault("blocked_users", {})[chat_id] = {
        "reason": reason or "بدون دلیل ذکرشده",
        "blocked_at": blocked_at,
        "until": until,
    }
    return state["blocked_users"][chat_id]


def unblock_user(state, chat_id):
    return state.get("blocked_users", {}).pop(chat_id, None) is not None


def build_blocked_list(state):
    blocked = state.get("blocked_users", {})
    if not blocked:
        return "🚫 هیچ کاربری مسدود نیست."
    lines = [f"🚫 کاربران مسدود ({len(blocked)}):", ""]
    for chat_id, info in blocked.items():
        until = info.get("until") or "دائمی"
        lines.append(f"• {chat_id}\n  دلیل: {info.get('reason')}\n  از: {info.get('blocked_at')} — تا: {until}")
    return "\n".join(lines)


def build_blocked_message(info):
    until = info.get("until") or "دائمی (تا اطلاع ثانوی)"
    return (
        f"⛔ شما توسط مالک ربات مسدود شده‌اید.\n"
        f"دلیل: {info.get('reason')}\n"
        f"تاریخ مسدودیت: {info.get('blocked_at')}\n"
        f"پایان مسدودیت: {until}"
    )


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
