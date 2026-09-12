"""
run_bot.py
-----------
تنها اسکریپتی که GitHub Actions هر ~۱۵ دقیقه اجرا می‌کند. فقط از متدهای
مستندشدهٔ رسمی روبیکا (از طریق bot_core.py) استفاده می‌کند:

  1. getUpdates می‌زند و آپدیت‌های جدید را می‌گیرد.
  2. اگر پیام از کانال منبع بود -> مود/ویدیوی جدید را ذخیره می‌کند.
  3. دستور /myid در هر چتی -> همان‌جا GUID آن چت را برمی‌گرداند.
  4. اگر پیام از پیوی خودِ شما (owner_guid) بود -> /bugs یا گزارش وضعیت کلی.
  5. اگر به ساعتِ پست‌گذاریِ برنامه‌ریزی‌شده رسیده باشیم -> برای هر کانال
     مقصد مود/ویدیو می‌گذارد و خلاصه را به پیوی شما می‌فرستد.
  6. خطاها ثبت می‌شوند ولی با فاصلهٔ حداقل ۱۰ دقیقه و به‌صورت خلاصه گزارش
     می‌شوند (نه سیل پیام).
"""

import os
import sys
import uuid

import bot_core as core


def uuid_short():
    return uuid.uuid4().hex[:8]


def handle_source_channel_message(state, msg):
    file_info = msg.get("file")
    caption = msg.get("text") or ""

    if file_info and file_info.get("file_type") == "Image":
        parsed = core.parse_caption(caption)
        state["pending_photo"] = {"file_id": file_info.get("file_id"), **parsed}

    elif file_info and file_info.get("file_type") == "Video":
        parsed = core.parse_caption(caption)
        state["videos"].append({
            "id": uuid_short(),
            "video_file_id": file_info.get("file_id"),
            "title": parsed["title"] or caption.strip() or "ویدیو جدید",
        })
        state["pending_photo"] = None

    elif file_info and file_info.get("file_type") in ("File", "Music", "Voice", "Gif"):
        pending = state.get("pending_photo")
        if pending:
            state["mods"].append({
                "id": uuid_short(),
                "photo_file_id": pending["file_id"],
                "file_file_id": file_info.get("file_id"),
                "title": pending["title"],
                "hashtags": pending["hashtags"],
                "description": pending["description"],
                "version": pending["version"],
            })
            state["pending_photo"] = None


def handle_owner_message(token, config, state, text):
    if text == "/bugs":
        core.send_message(token, config["owner_guid"], core.build_bugs_page(state))
        return

    n_mods = len(state.get("mods", []))
    n_videos = len(state.get("videos", []))
    n_errors = len(state.get("errors", []))
    now = core.tehran_now().strftime("%Y-%m-%d %H:%M")
    status = (
        f"✅ ربات فعال است.\n"
        f"🕰 ساعت تهران: {now}\n"
        f"🎮 مودهای ذخیره‌شده: {n_mods}\n"
        f"🎬 ویدیوهای ذخیره‌شده: {n_videos}\n"
        f"🐞 تعداد کل باگ‌های ثبت‌شده: {n_errors}\n"
        f"برای دیدن گزارش باگ‌ها: /bugs"
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

    start, end = config["schedule"]["start_hour_tehran"], config["schedule"]["end_hour_tehran"]
    if not (start <= hour <= end) or hour in posted["hours"]:
        return

    video_slot = core.is_video_slot(hour, config)
    posted_summary = []

    for channel in config["destination_channels"]:
        guid = channel["guid"]
        try:
            if video_slot:
                used = state["used_videos_per_channel"].setdefault(guid, [])
                item, used = core.pick_item(state["videos"], used)
                state["used_videos_per_channel"][guid] = used
                if item is None:
                    continue
                core.send_video(token, channel, item)
                posted_summary.append(f"🎬 {channel['name']}: ویدیو «{item['title']}»")
            else:
                used = state["used_mods_per_channel"].setdefault(guid, [])
                item, used = core.pick_item(state["mods"], used)
                state["used_mods_per_channel"][guid] = used
                if item is None:
                    continue
                core.send_mod(token, channel, item)
                posted_summary.append(f"🎮 {channel['name']}: مود «{item.get('title')}»")
        except Exception as e:
            core.log_error(state, f"ارسال به {channel['name']}", e)

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
        print("DEBUG: توکن پیدا نشد (نه در RUBIKA_BOT_TOKEN نه در config.json)")
        return

    if os.environ.get("TRIGGER_TYPE") == "workflow_dispatch":
        try:
            me = core.get_me(token)
            bot_name = (me.get("bot") or {}).get("title") or "ربات"
            now_str = core.tehran_now().strftime("%Y-%m-%d %H:%M")
            core.notify_owner(token, config, f"🟢 {bot_name} راه‌اندازی شد و وصل است.\n🕰 ساعت تهران: {now_str}")
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

    print(f"DEBUG: تعداد آپدیت‌های دریافتی از getUpdates: {len(updates)}")
    if updates:
        print(f"DEBUG: نمونهٔ خام اولین آپدیت: {updates[0]}")

    for update in updates:
        msg = update.get("new_message") or update.get("updated_message") or update
        if not isinstance(msg, dict):
            continue
        chat_id = msg.get("chat_id") or update.get("chat_id")
        text = (msg.get("text") or "").strip()
        print(f"DEBUG: پیام پردازش‌شده -> chat_id={chat_id} | text={text!r}")

        try:
            if text == "/myid" and chat_id:
                core.send_message(token, chat_id, f"GUID این چت:\n{chat_id}")
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
