"""
run_bot.py
----------
اجرای یک‌باره توسط GitHub Actions.
"""

import os
import sys
import uuid

import bot_core as core


def response_data(resp):
    if not isinstance(resp, dict):
        return {}
    data = resp.get("data")
    return data if isinstance(data, dict) else resp


def extract_updates(resp):
    data = response_data(resp)
    updates = data.get("updates", [])
    return updates if isinstance(updates, list) else []


def handle_source_channel_message(state, msg):
    file_info = msg.get("file") or {}
    file_type = file_info.get("file_type")
    file_id = file_info.get("file_id")
    caption = msg.get("text") or ""

    if not file_id:
        return

    if file_type == "Image":
        parsed = core.parse_caption(caption)
        state["pending_photo"] = {
            "file_id": file_id,
            **parsed,
        }

    elif file_type == "Video":
        parsed = core.parse_caption(caption)
        state["videos"].append({
            "id": uuid.uuid4().hex[:8],
            "video_file_id": file_id,
            "title": parsed["title"] or caption.strip() or "ویدیو جدید",
        })
        state["pending_photo"] = None

    elif file_type in ("File", "Music", "Voice", "Gif"):
        pending = state.get("pending_photo")
        if pending:
            state["mods"].append({
                "id": uuid.uuid4().hex[:8],
                "photo_file_id": pending["file_id"],
                "file_file_id": file_id,
                "title": pending["title"],
                "hashtags": pending["hashtags"],
                "description": pending["description"],
                "version": pending["version"],
            })
            state["pending_photo"] = None


def bugs_keyboard(has_more, next_offset):
    # فعلاً بدون دکمه inline؛ /bugs همیشه قابل استفاده است و به API
    # رسمی کم‌ریسک‌تر است. بعداً می‌توانیم keypad را جداگانه اضافه کنیم.
    return None


def handle_owner_message(bot, config, state, msg):
    text = (msg.get("text") or "").strip()

    if text in ("/bugs", "باگ", "باگ‌ها", "bugs"):
        page_text, _ = core.build_bugs_page(state, 0)
        bot.send_message(chat_id=config["owner_guid"], text=page_text)
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
    bot.send_message(chat_id=config["owner_guid"], text=status)


def run_posting_schedule(bot, config, state):
    now = core.tehran_now()
    today = now.strftime("%Y-%m-%d")
    hour = now.hour

    posted = state.setdefault("posted_hours_today", {"date": today, "hours": []})
    if posted.get("date") != today:
        posted["date"] = today
        posted["hours"] = []

    start = config["schedule"]["start_hour_tehran"]
    end = config["schedule"]["end_hour_tehran"]

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
                core.send_video(bot, channel, item)
                posted_summary.append(
                    f"🎬 {channel['name']}: ویدیو «{item['title']}»"
                )
            else:
                used = state["used_mods_per_channel"].setdefault(guid, [])
                item, used = core.pick_item(state["mods"], used)
                state["used_mods_per_channel"][guid] = used
                if item is None:
                    continue
                core.send_mod(bot, channel, item)
                posted_summary.append(
                    f"🎮 {channel['name']}: مود «{item.get('title', '')}»"
                )
        except Exception as exc:
            core.log_error(state, f"ارسال به {channel['name']}", exc)

    posted["hours"].append(hour)

    if posted_summary:
        core.notify_owner(
            bot,
            config,
            f"📤 گزارش پست ساعت {hour}:00\n" + "\n".join(posted_summary),
        )
    else:
        core.notify_owner(
            bot,
            config,
            f"ℹ️ ساعت {hour}:00 چیزی برای پست‌کردن موجود نبود.",
        )


def manual_heartbeat(bot, config):
    owner = config.get("owner_guid")
    if not owner or owner.startswith("c0xYOUR"):
        print("[WARN] owner_guid تنظیم نشده؛ پیام زنده‌بودن ارسال نشد.")
        return
    bot.send_message(
        chat_id=owner,
        text="🟢 من هنوز زنده‌ام.\nاجرای دستی GitHub Actions با موفقیت به روبیکا وصل شد.",
    )


def main():
    config = core.load_config()
    state = core.load_state()

    token = os.environ.get("RUBIKA_BOT_TOKEN") or config.get("bot_token")
    bot = core.RubikaBot(token)

    # تست واقعی احراز هویت با متد رسمی getMe
    me = bot.get_me()
    print("Rubika getMe OK:", me)

    # وقتی از دکمه Run workflow اجرا شده، فوراً پیام سلامت بفرست.
    if os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch":
        try:
            manual_heartbeat(bot, config)
        except Exception as exc:
            core.log_error(state, "پیام سلامت اجرای دستی", exc)

    try:
        resp = bot.get_updates(
            offset_id=state.get("last_offset_id"),
            limit=50,
        )
        updates = extract_updates(resp)
        data = response_data(resp)

        # مهم: next_offset_id را از پاسخ رسمی ذخیره می‌کنیم، نه offset
        # هر update؛ این روش برای اجرای دوره‌ای Actions مطمئن‌تر است.
        next_offset = data.get("next_offset_id")
        if next_offset:
            state["last_offset_id"] = next_offset

        print(f"Updates received: {len(updates)}")

    except Exception as exc:
        core.log_error(state, "دریافت آپدیت‌ها", exc)
        updates = []

    for update in updates:
        msg = update.get("new_message") or update.get("updated_message")
        if not isinstance(msg, dict):
            continue

        chat_id = msg.get("chat_id") or update.get("chat_id")

        try:
            if chat_id == config.get("source_channel_guid"):
                handle_source_channel_message(state, msg)
            elif chat_id == config.get("owner_guid"):
                handle_owner_message(bot, config, state, msg)
        except Exception as exc:
            core.log_error(state, "پردازش پیام", exc)

    try:
        run_posting_schedule(bot, config, state)
    except Exception as exc:
        core.log_error(state, "زمان‌بند پست‌گذاری", exc)

    try:
        core.maybe_notify_new_errors(bot, config, state)
    except Exception as exc:
        print(f"[WARN] ارسال گزارش خطا شکست خورد: {exc}")

    core.save_state(state)


if __name__ == "__main__":
    try:
        main()
    except Exception as fatal:
        print(f"خطای کلی و غیرمنتظره: {fatal}", file=sys.stderr)
        raise
