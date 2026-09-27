"""
run_bot.py
-----------
فقط هماهنگ‌کننده‌ست؛ منطق واقعی در دو فایل جداست:
  - mod_archiver.py: خوندن از کانال منبع، تحویل فایل به کاربر
  - channel_admin.py: دستورهای مالک، پنل، پست‌گذاری خودکار در کانال‌ها

هر بار اجرا می‌شود (توسط زنگ‌زن بیرونی cron-job.org، یا دستی از Actions):
  1. اگر ورودی force_post_channel داده شده باشد -> فوراً یک مود پست می‌کند.
  2. getUpdates می‌زند، کاربرهای جدید را ثبت می‌کند.
  3. دستورها: /myidver001، #عدد یا /عدد (ثبت درخواست فایل)، /file (تحویل)،
     /start، /help، /ticket، و برای مالک: پنل، رمز پخش همگانی، /bugs و غیره.
  4. کانال منبع: عکس مود باید تگ #مود داشته باشد؛ ویدیو باید تگ #ویدئو داشته باشد.
  5. زمان‌بندی پست‌گذاری طبق schedule/plan هر کانال در config.json.
  6. پیام‌های دوره‌ای (راه‌اندازی/گزارش) با فاصلهٔ حداقلی، تا سیل نشوند.
"""

import os
import sys

import bot_core as core
import mod_archiver as archiver
import channel_admin as admin


# مدت زمانی که یک پاسخ اطلاع‌رسانیِ ساده (/start یا /help) دوباره برای
# همون فرد فرستاده نمی‌شه، اگه به‌تازگی همون درخواست رو داده باشه.
INFO_REPLY_COOLDOWN_MINUTES = 3


def main():
    config = core.load_config()
    state = core.load_state()
    token = os.environ.get("RUBIKA_BOT_TOKEN") or config.get("bot_token")
    if not token:
        print("DEBUG: توکن پیدا نشد")
        return

    core.track_channel_activation(state, config)
    core.prune_known_users(state, config)
    n_users_before = len(state.get("known_users", []))

    print(f"DEBUG: وضعیت لود شده -> last_offset_id={state.get('last_offset_id')!r} "
          f"mods={len(state.get('mods', []))} videos={len(state.get('videos', []))} "
          f"users={n_users_before} errors={len(state.get('errors', []))}")

    if os.environ.get("FLUSH_UPDATES", "").strip().lower() in ("yes", "true", "1"):
        flushed = 0
        offset = state.get("last_offset_id")
        for _ in range(30):  # حداکثر ۳۰ بار (۳۰×۵۰=۱۵۰۰ پیام) در یک اجرا
            try:
                resp = core.get_updates(token, offset_id=offset, limit=50)
            except Exception as e:
                core.log_error(state, "پاک‌سازی انبار", e)
                break
            batch = resp.get("updates", []) if isinstance(resp, dict) else []
            next_off = resp.get("next_offset_id") if isinstance(resp, dict) else None
            flushed += len(batch)
            if not batch or not next_off or next_off == offset:
                offset = next_off or core.extract_fallback_offset(batch) or offset
                break
            offset = next_off

        if offset:
            state["last_offset_id"] = offset
        core.notify_owner(token, config, f"🧹 پاک‌سازی انجام شد. {flushed} پیام قدیمی بدون پاسخ دور ریخته شد.")
        core.save_state(state)
        print(f"DEBUG: flush کامل شد -> {flushed} پیام, offset نهایی={offset!r}")
        return

    if os.environ.get("TRIGGER_TYPE") == "workflow_dispatch" and core.should_send_now(state, "heartbeat", 8):
        try:
            me = core.get_me(token)
            bot_name = (me.get("bot") or {}).get("title") or "ربات"
            now_str = core.tehran_now().strftime("%Y-%m-%d %H:%M")
            n_active_channels = len([c for c in config.get("destination_channels", []) if c.get("enabled", True)])
            core.notify_owner(
                token, config,
                f"🟢 {bot_name} راه‌اندازی شد و وصل است.\n"
                f"🕰 ساعت تهران: {now_str}\n"
                f"👥 تعداد کاربران: {n_users_before}\n"
                f"📡 تعداد کانال‌های فعال: {n_active_channels}\n"
                f"🎮 تعداد مودها: {len(state.get('mods', []))}\n"
                f"🎬 تعداد ویدیوها: {len(state.get('videos', []))}"
            )
        except Exception as e:
            core.log_error(state, "تست اتصال (getMe)", e)

    force_target = os.environ.get("FORCE_POST_CHANNEL", "")
    if force_target:
        try:
            admin.run_force_post(token, config, state, force_target)
        except Exception as e:
            core.log_error(state, "پست فوری", e)

    try:
        resp = core.get_updates(token, offset_id=state.get("last_offset_id"), limit=50)
        updates = resp.get("updates", []) if isinstance(resp, dict) else []
        next_offset = resp.get("next_offset_id") if isinstance(resp, dict) else None
    except Exception as e:
        core.log_error(state, "دریافت آپدیت‌ها", e)
        updates = []
        next_offset = None

    print(f"DEBUG: تعداد آپدیت‌های دریافتی: {len(updates)} | next_offset={next_offset!r}")

    for update in updates:
        print(f"DEBUG: RAW update کامل: {update}")
        msg = update.get("new_message") or update.get("updated_message") or update
        if not isinstance(msg, dict):
            continue
        update_identity = core.get_update_identity(update, msg)
        if core.was_processed(state, update_identity):
            print(f"DEBUG: آپدیت تکراری رد شد -> {update_identity}")
            continue
        chat_id = msg.get("chat_id") or update.get("chat_id")
        text = (msg.get("text") or "").strip()
        print(f"DEBUG: پیام -> chat_id={chat_id} | text={text!r}")

        try:
            # ۱) اگه مسدوده (و مالک نیست)، فقط پیام مسدودی رو بده و رد شو
            if chat_id and chat_id != config.get("owner_guid"):
                block_info = core.is_blocked(state, chat_id)
                if block_info:
                    core.send_message(token, chat_id, core.build_blocked_message(block_info))
                    continue

            is_new_user = core.track_known_user(state, config, chat_id)
            owner_needs_keypad = (
                chat_id == config.get("owner_guid") and not state.get("owner_keypad_set")
            )
            if is_new_user or owner_needs_keypad:
                try:
                    core.set_chat_keypad(token, chat_id, ["/help", "/ticket"])
                    if owner_needs_keypad:
                        state["owner_keypad_set"] = True
                except Exception as e:
                    core.log_error(state, "تنظیم کیبورد ثابت", e)

            # تشخیص اسپم/فعالیت بیش‌ازحد (فقط برای کاربرهای عادی، نه مالک/کانال‌ها)
            if chat_id and chat_id not in (config.get("owner_guid"), config.get("source_channel_guid")):
                if core.check_spam(state, chat_id) and core.should_send_now(state, f"spamreport:{chat_id}", core.SPAM_REPORT_COOLDOWN_MINUTES):
                    core.notify_owner(
                        token, config,
                        f"🚨 فعالیت مشکوک/اسپم\n"
                        f"GUID فرد: {chat_id}\n"
                        f"بیش از {core.SPAM_THRESHOLD} پیام در {core.SPAM_WINDOW_MINUTES} دقیقهٔ اخیر."
                    )

            if chat_id and chat_id == config.get("source_channel_guid"):
                print(f"DEBUG: RAW پیام کانال منبع (کامل): {msg}")

            if text == "/myidver001" and chat_id:
                core.send_message(token, chat_id, f"GUID این چت:\n{chat_id}")
            elif text == "/start" and chat_id and chat_id not in (config.get("owner_guid"), config.get("source_channel_guid")):
                # اگه همین درخواست به‌تازگی پاسخ داده شده، دوباره جواب نده.
                if core.should_send_now(state, f"start:{chat_id}", INFO_REPLY_COOLDOWN_MINUTES):
                    archiver.handle_start_command(token, config, state, chat_id)
            elif text == "/help" and chat_id:
                if core.should_send_now(state, f"help:{chat_id}", INFO_REPLY_COOLDOWN_MINUTES):
                    help_text = config.get("help_text", "برای دریافت فایل مود، شمارهٔ زیر پست رو با # یا / به من بفرستید (مثلاً #1).")
                    core.send_message(token, chat_id, help_text)
            elif chat_id and admin.handle_ticket_flow(token, config, state, chat_id, text):
                pass
            elif text == "/file" and chat_id:
                archiver.handle_file_command(token, config, state, chat_id)
            elif not msg.get("file") and archiver.handle_number_request(token, config, state, chat_id, text):
                pass
            elif chat_id and msg.get("file") and chat_id in (
                config.get("source_channel_guid"), config.get("owner_guid")
            ):
                # پست کانال منبع، یا فایل/عکسی که مستقیم (یا فوروارد) به
                # پیوی خودِ ربات فرستاده شده — هر دو با یک منطق پردازش می‌شن
                archiver.handle_source_channel_message(state, msg)
            elif chat_id and chat_id == config.get("owner_guid"):
                admin.handle_owner_message(token, config, state, text, msg)
        except Exception as e:
            core.log_error(state, "پردازش پیام", e)
        finally:
            # حتی اگر پیام مربوط به دستور خاصی نبود، همان update دوباره پردازش نشود.
            core.mark_processed(state, update_identity)

    if next_offset:
        # فقط next_offset_id خودِ API معتبر است؛ message_id آپدیت را به‌جای
        # offset_id حدس نمی‌زنیم، چون همین حدس می‌تواند باعث تکرار آپدیت‌ها شود.
        state["last_offset_id"] = next_offset
    else:
        print("DEBUG: next_offset_id خالی بود؛ offset قبلی حفظ شد و dedupe از تکرار جلوگیری می‌کند.")

    try:
        admin.run_posting_schedule(token, config, state)
    except Exception as e:
        core.log_error(state, "زمان‌بند پست‌گذاری", e)

    try:
        core.maybe_notify_new_errors(token, config, state)
    except Exception:
        pass

    core.trim_stored_content(state)
    core.prune_sent_log(state)  # پاک‌سازی تضمینیِ یادداشت‌های قدیمی (مثل heartbeat) هر اجرا

    print(f"DEBUG: وضعیت قبل از ذخیره -> last_offset_id={state.get('last_offset_id')!r} "
          f"mods={len(state.get('mods', []))} videos={len(state.get('videos', []))} "
          f"users={len(state.get('known_users', []))}")

    core.save_state(state)


if __name__ == "__main__":
    try:
        main()
    except Exception as fatal:
        print(f"خطای کلی و غیرمنتظره: {fatal}", file=sys.stderr)
        raise
