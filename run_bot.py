"""Entry point for the Rubka mod/video bot."""

import os

import bot_core


def main():
    # در اجرای دستی GitHub Actions، اگر ADMIN_CHAT_ID تنظیم شده باشد
    # یک پیام سلامت می‌فرستیم تا مطمئن شوی ربات واقعاً وصل شده است.
    bot = bot_core.make_bot()
    bot.get_me()

    if os.getenv("GITHUB_EVENT_NAME") == "workflow_dispatch" and bot_core.ADMIN_CHAT_ID:
        bot.send_message(
            chat_id=bot_core.ADMIN_CHAT_ID,
            text="🟢 من هنوز زنده‌ام.\nاجرای دستی GitHub Actions با موفقیت به روبیکا وصل شد.",
        )

    # اجرای اصلی در همان فرآیند؛ از Rubka برای تمام ارتباطات روبیکا استفاده می‌شود.
    bot_core.main()


if __name__ == "__main__":
    main()
