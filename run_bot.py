"""
run_bot.py
-----------
هر بار که (توسط زنگ‌زن بیرونی مثل cron-job.org، یا دستی) اجرا می‌شود:

  1. getUpdates می‌زند.
  2. هر چت جدیدی که به ربات پیام داده (به‌جز کانال‌های تنظیم‌شده) را در
     known_users ثبت می‌کند.
  3. دستورهای زیر را در هر چتی پاسخ می‌دهد:
     - /myid                -> GUID همون چت
     - #عدد یا /عدد          -> فوروارد فایل مرتبط با اون عدد
  4. برای مالک ربات (owner_guid) این‌ها هم اضافه می‌شود:
     - کلمهٔ panel_keyword   -> نمایش پنل کانال‌ها
     - یک عدد (بعد از پنل)  -> جزئیات همون کانال
     - broadcast_secret      -> شروع حالت پخش همگانی
     - (در حالت پخش همگانی) هر متنی -> برای همهٔ known_users فرستاده می‌شود
     - /bugs                 -> گزارش خطاها
     - هر متن دیگری          -> وضعیت کلی + تعداد کاربران
  5. اگر پیام از کانال منبع بود -> مود/ویدیو/فایل‌شماره‌دار را ذخیره می‌کند.
  6. سر ساعت‌های ۱۱ تا ۲۳: هر ساعت یک مود در هر کانال مقصدِ فعال پست
     می‌شود؛ هر ۴ ساعت یک‌بار، علاوه بر مود همون ساعت، یک ویدیو هم پست
     می‌شود.
  7. خطاها ثبت می‌شوند ولی حداکثر هر ۱۰ دقیقه یک خلاصه به پیوی می‌رود.
"""

import os
import re
import sys
import uuid

import bot_core as core


def uuid_short():
    return uuid.uuid4().hex[:8]


def handle_source_channel_message(state, msg):
    file_info = msg.get("file")
    caption = msg.get("text") or ""

    if not file_info:
        return

    file_type = file_info.get("file_type")
    print(f"DEBUG: پیام کانال منبع -> file_type={file_type!r} caption={caption[:60]!r}")

    if file_type == "Image":
        parsed = core.parse_caption(caption)
        state["pending_photo"] = {"file_id": file_info.get("file_id"), **parsed}

    elif file_type == "Video":
        parsed = core.parse_caption(caption)
        state["videos"].append({
            "id": uuid_short(),
            "video_file_id": file_info.get("file_id"),
            "title": parsed["title"] or caption.strip() or "ویدیو جدید",
        })

    else:
        parsed = core.parse_caption(caption)
        number = parsed["number"]
        if not number:
            print("DEBUG: فایل بدون هشتگ شماره (#عدد) نادیده گرفته شد")
            return

        state["files_by_number"][number] = {
            "file_id": file_info.get("file_id"),
            "title": parsed["title"] or f"فایل شماره {number}",
        }

        pending = state.get("pending_photo")
        if pending:
            state["mods"].append({
                "id": uuid_short(),
                "photo_file_id": pending["file_id"],
                "title": pending["title"],
                "hashtags": pending["hashtags"],
                "description": pending["description"],
                "version": pending["version"],
                "number": number,
            })
            state["pending_photo"] = None
        else:
            print(f"DEBUG: فایل #{number} بدون عکس همراه، فقط برای دریافت مستقیم ذخیره شد")


NUMBER_REQUEST_RE = re.compile(r"^[#/](\d+)$")


def handle_number_request(token, state, chat_id, text):
    m = NUMBER_REQUEST_RE.match(text)
    if not m:
        return False
    number = m.group(1)
    entry = state.get("files_by_number", {}).get(number)
    if not entry:
        core.send_message(token, chat_id, f"فایلی با شمارهٔ {number} پیدا نشد.")
    else:
        core.send_file(token, chat_id, entry["file_id"], entry.get("title", ""))
    return True


def handle_owner_message(token, config, state, text):
    panel_keyword = config.get("panel_keyword", "پنل")
    broadcast_secret = config.get("broadcast_secret", "")

    if state.get("awaiting_broadcast"):
        state["awaiting_broadcast"] = False
        sent, failed = core.broadcast_to_users(token, state, text)
        core.send_message(token, config["owner_guid"], f"📣 پیام برای {sent} کاربر ارسال شد. (ناموفق: {failed})")
        return

    if broadcast_secret and text == broadcast_secret:
        state["awaiting_broadcast"] = True
        core.send_message(token, config["owner_guid"], "پیام خودت رو بفرست تا برای همهٔ کاربرها ارسالش کنم.")
        return

    if text == panel_keyword:
        state["awaiting_panel_selection"] = True
        core.send_message(token, config["owner_guid"], core.build_panel_list(config))
        return

    if state.get("awaiting_panel_selection") and text.isdigit():
        state["awaiting_panel_selection"] = False
        core.send_message(token, config["owner_guid"], core.build_channel_detail(config, state, int(text)))
        return

    if text == "/bugs":
        core.send_message(token, config["owner_guid"], core.build_bugs_page(state))
        return

    if text == "/reset_queue":
        skipped = core.fast_forward_offset(token, state)
        core.send_message(
            token, config["owner_guid"],
            f"♻️ صف پیام‌های خونده‌نشده خالی شد ({skipped} پیام قدیمی رد شد).\n"
            f"از این لحظه به بعد ربات فقط پیام‌های جدید رو پردازش می‌کنه."
        )
        return

    n_mods = len(state.get("mods", []))
    n_videos = len(state.get("videos", []))
    n_errors = len(state.get("errors", []))
    n_users = len(state.get("known_users", []))
    now = core.tehran_now().strftime("%Y-%m-%d %H:%M")
    status = (
        f"✅ ربات فعال است.\n"
        f"🕰 ساعت تهران: {now}\n"
        f"👥 تعداد کاربران: {n_users}\n"
        f"🎮 مودهای ذخیره‌شده: {n_mods}\n"
        f"🎬 ویدیوهای ذخیره‌شده: {n_videos}\n"
        f"🐞 تعداد کل باگ‌های ثبت‌شده: {n_errors}\n"
        f"برای دیدن گزارش باگ‌ها: /bugs\n"
        f"برای پنل کانال‌ها: {panel_keyword}"
    )
    core.send_message(token, config["owner_guid"], status)


def run_posting_schedule(token, config, state):
    now = core.tehran_now()
    today = now.strftime("%Y-%m-%d")
    hour = now.hour

    posted = state.setdefault("posted_hours_today", {"date": today, "hours": []})
    if posted.get("date") != today:
        posted["date"] = today
        posted["hours"] = []

    start = config["schedule"]["start_hour_tehran"]
    end = config["schedule"]["end_hour_tehran"]
    video_every_n = config["schedule"].get("video_every_n_hours", 4)

    if not (start <= hour <= end) or hour in posted["hours"]:
        return

    also_video = (hour - start) % video_every_n == 0
    posted_summary = []

    for channel in config["destination_channels"]:
        if not channel.get("enabled", True):
            continue
        guid = channel["guid"]

        try:
            used = state["used_mods_per_channel"].setdefault(guid, [])
            item, used = core.pick_item(state["mods"], used)
            state["used_mods_per_channel"][guid] = used
            if item is not None:
                core.send_mod(token, channel, item)
                posted_summary.append(f"🎮 {channel['name']}: مود «{item.get('title')}»")
        except Exception as e:
            core.log_error(state, f"ارسال مود به {channel['name']}", e)

        if also_video:
            try:
                used_v = state["used_videos_per_channel"].setdefault(guid, [])
                vitem, used_v = core.pick_item(state["videos"], used_v)
                state["used_videos_per_channel"][guid] = used_v
                if vitem is not None:
                    core.send_video(token, channel, vitem)
                    posted_summary.append(f"🎬 {channel['name']}: ویدیو «{vitem['title']}»")
            except Exception as e:
                core.log_error(state, f"ارسال ویدیو به {channel['name']}", e)

    posted["hours"].append(hour)

    if posted_summary:
        core.notify_owner(token, config, f"📤 گزارش پست ساعت {hour}:00\n" + "\n".join(posted_summary))
    else:
        core.notify_owner(token, config, f"ℹ️ ساعت {hour}:00 چیزی برای پست‌کردن (مود/ویدیوی تکراری‌نشده) موجود نبود.")


def main():
    config = core.load_config()
    state = core.load_state()
    token = os.environ.get("RUBIKA_BOT_TOKEN") or config.get("bot_token")
    if not token:
        print("DEBUG: توکن پیدا نشد")
        return

    core.track_channel_activation(state, config)
    n_users_before = len(state.get("known_users", []))

    if os.environ.get("TRIGGER_TYPE") == "workflow_dispatch":
        try:
            me = core.get_me(token)
            bot_name = (me.get("bot") or {}).get("title") or "ربات"
            now_str = core.tehran_now().strftime("%Y-%m-%d %H:%M")
            core.notify_owner(
                token, config,
                f"🟢 {bot_name} راه‌اندازی شد و وصل است.\n"
                f"🕰 ساعت تهران: {now_str}\n"
                f"👥 تعداد کاربران: {n_users_before}"
            )
        except Exception as e:
            core.log_error(state, "تست اتصال (getMe)", e)

    try:
        resp = core.get_updates(token, offset_id=state.get("last_offset_id"), limit=50)
        updates = resp.get("updates", []) if isinstance(resp, dict) else []
        next_offset = resp.get("next_offset_id") if isinstance(resp, dict) else None
    except Exception as e:
        core.log_error(state, "دریافت آپدیت‌ها", e)
        updates = []
        next_offset = None

    # --- رفع باگ اصلی: پیشروی همیشگی offset -----------------------------
    # روبیکا وقتی صفحه‌ی بعدی وجود نداره (یعنی همین دسته، آخرین دسته‌ست)
    # next_offset_id رو خالی برمی‌گردونه. قبلاً کد در این حالت last_offset_id
    # رو دست‌نخورده رها می‌کرد، پس دفعه‌ی بعد دقیقاً همین پیام‌ها دوباره
    # خونده و دوباره پردازش می‌شدن (باگ اصلی «تکرار پیام‌ها»).
    # الان: اگه next_offset_id نبود ولی پیام جدیدی رسیده، از شناسه‌ی خودِ
    # آخرین پیام به‌عنوان offset بعدی استفاده می‌کنیم.
    if not next_offset and updates:
        last_update = updates[-1]
        last_msg = last_update.get("new_message") or last_update.get("updated_message") or {}
        fallback_offset = (
            last_update.get("update_id")
            or last_update.get("id")
            or last_msg.get("message_id")
        )
        if fallback_offset:
            next_offset = fallback_offset
            print(f"DEBUG: next_offset_id از روبیکا خالی بود؛ از شناسه‌ی آخرین پیام به‌عنوان offset بعدی استفاده شد: {fallback_offset}")

    print(f"DEBUG: تعداد آپدیت‌های دریافتی: {len(updates)} | offset فعلی: {state.get('last_offset_id')} | offset بعدی: {next_offset}")

    for update in updates:
        msg = update.get("new_message") or update.get("updated_message") or update
        if not isinstance(msg, dict):
            continue
        chat_id = msg.get("chat_id") or update.get("chat_id")
        text = (msg.get("text") or "").strip()
        print(f"DEBUG: پیام -> chat_id={chat_id} | text={text!r}")

        try:
            core.track_known_user(state, config, chat_id)

            if text == "/myid" and chat_id:
                core.send_message(token, chat_id, f"GUID این چت:\n{chat_id}")
            elif handle_number_request(token, state, chat_id, text):
                pass
            elif chat_id and chat_id == config.get("source_channel_guid"):
                handle_source_channel_message(state, msg)
            elif chat_id and chat_id == config.get("owner_guid"):
                handle_owner_message(token, config, state, text)
        except Exception as e:
            core.log_error(state, "پردازش پیام", e)

    if next_offset:
        state["last_offset_id"] = next_offset

    try:
        run_posting_schedule(token, config, state)
    except Exception as e:
        core.log_error(state, "زمان‌بند پست‌گذاری", e)

    try:
        core.maybe_notify_new_errors(token, config, state)
    except Exception:
        pass

    core.save_state(state)


if __name__ == "__main__":
    try:
        main()
    except Exception as fatal:
        print(f"خطای کلی و غیرمنتظره: {fatal}", file=sys.stderr)
        raise
