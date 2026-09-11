"""
post_scheduler.py
------------------
این اسکریپت هر بار که توسط GitHub Actions (طبق کرون‌تایم تنظیم‌شده در
.github/workflows/rubika-bot.yml) اجرا می‌شود:

  1. ساعت فعلی به وقت تهران را حساب می‌کند.
  2. تصمیم می‌گیرد این نوبت باید «ویدیو» گذاشته شود یا «مود».
  3. برای هر کانال مقصد در config.json، یک آیتم تصادفی که قبلا در آن
     کانال گذاشته نشده انتخاب و پست می‌کند (با متن/لینک/هشتگ مخصوص
     همان کانال).
  4. state.json را برای جلوگیری از تکرار به‌روزرسانی می‌کند.
"""

import json
import os
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

from rubka import Robot

BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / "config.json"
STATE_PATH = BASE_DIR / "state.json"

TEHRAN_OFFSET = timedelta(hours=3, minutes=30)


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def tehran_now():
    return datetime.now(timezone.utc) + TEHRAN_OFFSET


def is_video_slot(hour: int, config: dict) -> bool:
    start = config["schedule"]["start_hour_tehran"]
    every_n = config["schedule"]["video_every_n_slots"]
    return (hour - start) % every_n == 0


def pick_item(items, used_ids):
    """یک آیتم تصادفی که هنوز استفاده نشده برمی‌گرداند.
    اگر همه استفاده شده باشند، چرخه دوباره از اول شروع می‌شود (ریست)."""
    if not items:
        return None, used_ids
    available = [i for i in items if i["id"] not in used_ids]
    if not available:
        used_ids = []
        available = items
    chosen = random.choice(available)
    used_ids = used_ids + [chosen["id"]]
    return chosen, used_ids


def send_mod(bot, channel, mod):
    hashtags_line = " ".join(mod.get("hashtags", []))
    caption1_lines = []
    if mod.get("title"):
        caption1_lines.append(mod["title"])
    if hashtags_line:
        caption1_lines.append(hashtags_line)
    if mod.get("description"):
        caption1_lines.append(f"📝 توضیحات: {mod['description']}")
    if mod.get("version"):
        caption1_lines.append(f"🔢 ورژن: {mod['version']}")
    caption1_lines.append(f"🔗 کانال: {channel['channel_link']}")
    if channel.get("mod_photo_extra_text"):
        caption1_lines.append(channel["mod_photo_extra_text"])
    caption1 = "\n".join(caption1_lines)

    bot.send_image(chat_id=channel["guid"], file_id=mod["photo_file_id"], text=caption1)

    caption2 = channel.get("mod_file_caption", "")
    bot.send_document(chat_id=channel["guid"], file_id=mod["file_file_id"], text=caption2)


def send_video(bot, channel, video):
    caption_lines = [video.get("title", "ویدیو جدید")]
    if channel.get("video_extra_text"):
        caption_lines.append(channel["video_extra_text"])
    caption = "\n".join(caption_lines)

    # کتابخانه rubka متدی به اسم send_video در مستندات فعلی ندارد؛
    # اگر نسخه‌ی شما این متد را دارد همان استفاده می‌شود، وگرنه با
    # send_document (ارسال فایل عمومی) جایگزین می‌شود.
    send_video_fn = getattr(bot, "send_video", None)
    if callable(send_video_fn):
        send_video_fn(chat_id=channel["guid"], file_id=video["video_file_id"], text=caption)
    else:
        bot.send_document(chat_id=channel["guid"], file_id=video["video_file_id"], text=caption)


def main():
    config = load_json(CONFIG_PATH)
    state = load_json(STATE_PATH)

    token = os.environ.get("RUBIKA_BOT_TOKEN") or config.get("bot_token")
    bot = Robot(token=token)

    now = tehran_now()
    hour = now.hour

    start = config["schedule"]["start_hour_tehran"]
    end = config["schedule"]["end_hour_tehran"]
    if not (start <= hour <= end):
        print(f"ساعت {hour} خارج از بازه {start}-{end} است، کاری انجام نمی‌شود.")
        return

    video_slot = is_video_slot(hour, config)
    print(f"ساعت تهران: {hour} | نوع پست این نوبت: {'ویدیو' if video_slot else 'مود'}")

    for channel in config["destination_channels"]:
        guid = channel["guid"]

        if video_slot:
            used = state["used_videos_per_channel"].setdefault(guid, [])
            item, used = pick_item(state["videos"], used)
            state["used_videos_per_channel"][guid] = used
            if item is None:
                print(f"[{channel['name']}] هیچ ویدیویی در دیتابیس موجود نیست، رد شد.")
                continue
            send_video(bot, channel, item)
            print(f"[{channel['name']}] ویدیو ارسال شد: {item['title']}")
        else:
            used = state["used_mods_per_channel"].setdefault(guid, [])
            item, used = pick_item(state["mods"], used)
            state["used_mods_per_channel"][guid] = used
            if item is None:
                print(f"[{channel['name']}] هیچ مودی در دیتابیس موجود نیست، رد شد.")
                continue
            send_mod(bot, channel, item)
            print(f"[{channel['name']}] مود ارسال شد: {item.get('title')}")

    save_json(STATE_PATH, state)


if __name__ == "__main__":
    main()
