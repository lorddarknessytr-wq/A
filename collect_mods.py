"""
collect_mods.py
----------------
از کانال منبع (source_channel_guid) آپدیت‌های جدید را می‌گیرد و پست‌های
«مود» (عکس + هشتگ‌ها + توضیحات + ورژن، و بلافاصله بعدش فایل مود) و پست‌های
«ویدیو» را تشخیص می‌دهد و در state.json ذخیره می‌کند.

⚠️ نکته مهم: فرمت دقیق کپشن در کانال منبع شما ممکن است کمی فرق داشته باشد.
اگر تشخیص هشتگ/توضیحات/ورژن درست کار نکرد، فقط بخش PARSE توضیح داده‌شده
در پایین فایل را با فرمت واقعی کانال خودتان تطبیق بدهید.

این اسکریپت با get_updates (polling) کار می‌کند، پس نیازی به وب‌هوک یا
سرور همیشه-روشن ندارد؛ هر بار که اجرا شود فقط پیام‌های «جدید» را می‌گیرد.
"""

import json
import os
import re
import uuid
from pathlib import Path

from rubka import Robot

BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / "config.json"
STATE_PATH = BASE_DIR / "state.json"


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def parse_caption(caption: str):
    """از روی متن کپشن، هشتگ‌ها/توضیحات/ورژن/تیتر را استخراج می‌کند.
    این بخش را با فرمت واقعی کانال منبع خودتان تنظیم کنید."""
    caption = caption or ""

    hashtags = re.findall(r"#\S+", caption)

    desc_match = re.search(r"(?:توضیحات|توضیح)\s*[:：]\s*(.+)", caption)
    description = desc_match.group(1).strip() if desc_match else ""

    version_match = re.search(r"(?:ورژن|نسخه)\s*[:：]\s*(\S+)", caption)
    version = version_match.group(1).strip() if version_match else ""

    # اولین خط غیرخالی که هشتگ/توضیحات/ورژن نیست را به‌عنوان تیتر در نظر می‌گیریم
    title = ""
    for line in caption.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
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


def main():
    config = load_json(CONFIG_PATH)
    state = load_json(STATE_PATH)

    token = os.environ.get("RUBIKA_BOT_TOKEN") or config.get("bot_token")
    bot = Robot(token=token)
    source_guid = config["source_channel_guid"]

    updates_resp = bot.get_updates(offset_id=state.get("last_offset_id"), limit=50)
    updates = updates_resp.get("data", {}).get("updates", []) if isinstance(updates_resp, dict) else []

    new_offset = state.get("last_offset_id")

    for update in updates:
        msg = update.get("new_message") or update.get("updated_message")
        if not msg:
            continue

        chat_id = msg.get("chat_id") or update.get("chat_id")
        if chat_id != source_guid:
            # فقط پیام‌های خود کانال منبع را پردازش کن
            new_offset = update.get("offset_id", new_offset)
            continue

        file_info = msg.get("file")
        caption = msg.get("text") or ""

        if file_info and file_info.get("file_type") in ("Image",):
            # این یک عکس است -> احتمالا شروع یک پست مود است
            parsed = parse_caption(caption)
            state["pending_photo"] = {
                "file_id": file_info.get("file_id"),
                **parsed,
            }

        elif file_info and file_info.get("file_type") == "Video":
            # ویدیوی مستقل
            parsed = parse_caption(caption)
            state["videos"].append({
                "id": str(uuid.uuid4())[:8],
                "video_file_id": file_info.get("file_id"),
                "title": parsed["title"] or caption.strip() or "ویدیو جدید",
            })
            state["pending_photo"] = None

        elif file_info and file_info.get("file_type") in ("File", "Music", "Voice", "Gif"):
            # این فایل مود است -> اگر عکسی در انتظار داشتیم، مود کامل می‌شود
            pending = state.get("pending_photo")
            if pending:
                state["mods"].append({
                    "id": str(uuid.uuid4())[:8],
                    "photo_file_id": pending["file_id"],
                    "file_file_id": file_info.get("file_id"),
                    "title": pending["title"],
                    "hashtags": pending["hashtags"],
                    "description": pending["description"],
                    "version": pending["version"],
                })
                state["pending_photo"] = None
            # اگر عکسی در انتظار نبود، این فایل نادیده گرفته می‌شود
            # (یعنی کانال منبع باید همیشه اول عکس و بعد فایل را بفرستد)

        new_offset = update.get("offset_id", new_offset)

    state["last_offset_id"] = new_offset
    save_json(STATE_PATH, state)
    print(f"جمع‌آوری تمام شد. تعداد مودها: {len(state['mods'])} | تعداد ویدیوها: {len(state['videos'])}")


if __name__ == "__main__":
    main()
