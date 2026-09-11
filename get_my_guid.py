"""
get_my_guid.py
--------------
GUID های دیده‌شده در getUpdates رسمی Rubika Bot API v3 را چاپ می‌کند.
"""

import os
import bot_core as core


def response_data(resp):
    if not isinstance(resp, dict):
        return {}
    data = resp.get("data")
    return data if isinstance(data, dict) else resp


def main():
    token = os.environ.get("RUBIKA_BOT_TOKEN")
    bot = core.RubikaBot(token)

    print("=== تست getMe ===")
    print(bot.get_me())

    resp = bot.get_updates(limit=100)
    data = response_data(resp)
    updates = data.get("updates", []) if isinstance(data, dict) else []

    seen = {}
    for u in updates:
        msg = u.get("new_message") or u.get("updated_message") or {}
        if not isinstance(msg, dict):
            continue

        chat_id = msg.get("chat_id") or u.get("chat_id")
        if not chat_id:
            continue

        preview = (msg.get("text") or "").strip()[:80]
        seen.setdefault(
            chat_id,
            {"count": 0, "sample_text": preview},
        )
        seen[chat_id]["count"] += 1

    print("\n=== GUID های دیده‌شده در آپدیت‌های اخیر ===")
    if not seen:
        print(
            "هیچ آپدیتی دیده نشد. یک پیام به پیوی ربات بفرستید "
            "یا در کانال منبع یک پست تستی بگذارید و دوباره اجرا کنید."
        )
        return

    for chat_id, info in seen.items():
        print(
            f"{chat_id} | تعداد پیام: {info['count']} "
            f"| نمونه: {info['sample_text']}"
        )


if __name__ == "__main__":
    main()
