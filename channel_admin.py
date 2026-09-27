"""
channel_admin.py
-----------------
بخش «ادمین»: هرچی به دستورهای مالک (پنل، پخش همگانی، پست فوری، بلاک/
آنبلاک، بگ‌ریپورت)، تیکت، و زمان‌بند پست‌گذاری خودکار توی کانال‌های
مقصد مربوطه، اینجاست. بخش «آرشیو مود» (خوندن از کانال منبع، تحویل فایل
به کاربر) در mod_archiver.py هست.
"""

import bot_core as core
import mod_archiver as archiver


def handle_ticket_flow(token, config, state, chat_id, text):
    """True برمی‌گردونه اگه این پیام بخشی از فرایند تیکت بوده (پردازش شده)."""
    awaiting = state.setdefault("awaiting_ticket", [])

    if chat_id in awaiting:
        awaiting.remove(chat_id)
        now = core.tehran_now().strftime("%Y-%m-%d %H:%M")
        core.notify_owner(
            token, config,
            f"🎫 تیکت جدید\n"
            f"GUID فرستنده: {chat_id}\n"
            f"زمان: {now}\n"
            f"متن: {text}"
        )
        core.send_message(token, chat_id, config.get(
            "texts", {}
        ).get("ticket_sent", "تیکت شما ارسال شد. با تشکر 🙏"))
        return True

    if text == "/ticket":
        if chat_id not in awaiting:
            awaiting.append(chat_id)
        core.send_message(token, chat_id, config.get(
            "texts", {}
        ).get("ticket_prompt", "لطفاً متن تیکت خودتون رو بنویسید."))
        return True

    return False


# مدت زمانی که یک پاسخ عمومی/غیرقطعی (وضعیت کلی) دوباره برای همون درخواستِ
# تکراری فرستاده نمی‌شه — طبق درخواست: «اگه قبلاً پاسخ داده شده، دوباره
# پاسخ نده».
STATUS_REPLY_COOLDOWN_MINUTES = 3


def handle_owner_message(token, config, state, text, msg=None):
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

    if state.get("awaiting_channelcast"):
        state["awaiting_channelcast"] = False
        sent, failed = core.broadcast_to_channels(token, config, text)
        core.send_message(token, config["owner_guid"], f"📡 پیام در {sent} کانال پست شد. (ناموفق: {failed})")
        return

    if text == "/channelcast":
        state["awaiting_channelcast"] = True
        core.send_message(token, config["owner_guid"], "پیام خودت رو بفرست تا توی همهٔ کانال‌های فعال پست کنم.")
        return

    if text == panel_keyword:
        state["awaiting_panel_selection"] = True
        core.send_message(token, config["owner_guid"], core.build_panel_list(config))
        return

    if state.get("awaiting_panel_selection") and text.isdigit():
        state["awaiting_panel_selection"] = False
        core.send_message(token, config["owner_guid"], core.build_channel_detail(config, state, int(text)))
        return

    if text.startswith("/postmod"):
        parts = text.split(maxsplit=1)
        target = parts[1].strip() if len(parts) > 1 else ""
        run_force_post_typed(token, config, state, target, "mod")
        return

    if text.startswith("/postvideo"):
        parts = text.split(maxsplit=1)
        target = parts[1].strip() if len(parts) > 1 else ""
        run_force_post_typed(token, config, state, target, "video")
        return

    if text.startswith("/post "):
        parts = text.split(maxsplit=1)
        target = parts[1].strip() if len(parts) > 1 else ""
        run_force_post_typed(token, config, state, target, "mod")
        return

    if text.startswith("/block"):
        parts = text.split(maxsplit=3)
        if len(parts) < 3:
            core.send_message(token, config["owner_guid"], "فرمت درست: /block <chat_id> <روز یا permanent> <دلیل>")
        else:
            target_id = parts[1]
            days_part = parts[2]
            reason = parts[3] if len(parts) > 3 else ""
            days = None if days_part.lower() == "permanent" else days_part
            try:
                info = core.block_user(state, target_id, reason, days)
                core.send_message(token, config["owner_guid"], f"⛔ {target_id} مسدود شد.\nتا: {info['until'] or 'دائمی'}")
            except Exception as e:
                core.send_message(token, config["owner_guid"], f"خطا در مسدودسازی: {e}")
        return

    if text.startswith("/unblock"):
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            core.send_message(token, config["owner_guid"], "فرمت درست: /unblock <chat_id>")
        else:
            ok = core.unblock_user(state, parts[1])
            core.send_message(token, config["owner_guid"], "✅ رفع مسدودیت شد." if ok else "این آیدی مسدود نبود.")
        return

    if text == "/blocked":
        core.send_message(token, config["owner_guid"], core.build_blocked_list(state))
        return

    if text == "/update":
        total, new_mods, new_videos = archiver.run_resync(token, config, state)
        core.send_message(
            token, config["owner_guid"],
            f"🔄 بازخوانی کامل شد.\n"
            f"کل آپدیت‌های بررسی‌شده: {total}\n"
            f"مود جدید پیدا شد: {new_mods}\n"
            f"ویدیوی جدید پیدا شد: {new_videos}"
        )
        return

    if text.startswith("/reply"):
        parts = text.split(maxsplit=2)
        if len(parts) < 3:
            core.send_message(token, config["owner_guid"], "فرمت درست: /reply <GUID> <متن پاسخ>\n(GUID رو از همون پیام تیکتی که برات فرستادم بردار)")
        else:
            target_guid, reply_text = parts[1], parts[2]
            try:
                core.send_message(token, target_guid, f"📩 پاسخ پشتیبانی:\n{reply_text}")
                core.send_message(token, config["owner_guid"], "✅ پاسخ ارسال شد.")
            except Exception as e:
                core.send_message(token, config["owner_guid"], f"❌ ارسال ناموفق بود: {e}")
        return

    if text == "/bugs":
        core.send_message(token, config["owner_guid"], core.build_bugs_page(state))
        return

    if text == "/clearbugs":
        n = len(state.get("errors", []))
        state["errors"] = []
        state["bug_page_offset"] = 0
        core.send_message(token, config["owner_guid"], f"🧹 لیست باگ‌ها پاک شد ({n} مورد حذف شد).")
        return

    if text == "/resetall":
        core.send_message(
            token, config["owner_guid"],
            "⚠️ این کار همهٔ مودها، ویدیوها، فایل‌های شماره‌گذاری‌شده و "
            "تاریخچهٔ پست‌های هر کانال رو کامل پاک می‌کنه (غیرقابل بازگشت).\n"
            "برای تأیید، دقیقاً بفرست: /resetall confirm"
        )
        return

    if text == "/resetall confirm":
        n_mods = len(state.get("mods", []))
        n_videos = len(state.get("videos", []))
        n_files = len(state.get("files_by_number", {}))
        state["mods"] = []
        state["videos"] = []
        state["files_by_number"] = {}
        state["pending_photo"] = None
        state["pending_file_requests"] = {}
        state["used_mods_per_channel"] = {}
        state["used_videos_per_channel"] = {}
        state["posted_slots_today"] = {"date": None, "mod": {}, "video": {}}
        core.send_message(
            token, config["owner_guid"],
            f"🗑 پاک‌سازی کامل انجام شد.\n"
            f"مودها: {n_mods} | ویدیوها: {n_videos} | فایل‌های شماره‌دار: {n_files}\n"
            f"از این لحظه به بعد آرشیو خالیه — منتظر پست جدید از کانال منبع می‌مونه."
        )
        return

    # پیام‌های ناشناخته/عمومی: اگه دقیقاً همین متن به‌تازگی جواب داده شده،
    # دوباره جواب نده (طبق درخواست: از پاسخ تکراری جلوگیری بشه).
    if not core.should_send_now(state, f"ownerreply:{text}", STATUS_REPLY_COOLDOWN_MINUTES):
        print(f"DEBUG: همین متن به مالک به‌تازگی پاسخ داده شده بود؛ دوباره پاسخ داده نشد: {text!r}")
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
        f"برای پاک کردن لیست باگ‌ها: /clearbugs\n"
        f"برای پاک‌سازی کامل آرشیو مود/ویدیو: /resetall\n"
        f"برای پنل کانال‌ها: {panel_keyword}\n"
        f"برای پاسخ به تیکت: /reply <GUID> <متن>\n"
        f"برای راهنما: /help"
    )
    core.send_message(token, config["owner_guid"], status)


def run_posting_schedule(token, config, state):
    now = core.tehran_now()
    today = now.strftime("%Y-%m-%d")
    minute_of_day = now.hour * 60 + now.minute

    start_h = config["schedule"]["start_hour_tehran"]
    end_h = config["schedule"]["end_hour_tehran"]
    start_min, end_min = start_h * 60, end_h * 60

    if not (start_min <= minute_of_day <= end_min):
        return

    posted = state.setdefault("posted_slots_today", {"date": today, "mod": {}, "video": {}})
    if posted.get("date") != today:
        posted["date"] = today
        posted["mod"] = {}
        posted["video"] = {}

    # باید با فاصلهٔ واقعیِ اجرای ورک‌فلو هماهنگ باشه (پیش‌فرض هر ۱۵ دقیقه)
    TOLERANCE_MIN = 15
    elapsed = minute_of_day - start_min
    posted_summary = []

    for channel in config["destination_channels"]:
        if not channel.get("enabled", True):
            continue
        guid = channel["guid"]
        mod_h, video_h = core.resolve_channel_plan(config, channel)
        mod_interval_min = max(1, round(mod_h * 60))
        video_interval_min = max(1, round(video_h * 60))

        mod_slot = elapsed // mod_interval_min
        video_slot = elapsed // video_interval_min
        due_mod = (elapsed % mod_interval_min) < TOLERANCE_MIN
        due_video = (elapsed % video_interval_min) < TOLERANCE_MIN

        mod_key = f"{guid}:{mod_slot}"
        video_key = f"{guid}:{video_slot}"

        try:
            if due_mod and not posted["mod"].get(mod_key):
                used = state["used_mods_per_channel"].setdefault(guid, [])
                item, candidate_used = core.pick_item(state["mods"], used)
                if item is not None:
                    core.send_mod(token, channel, item, state)
                    # فقط بعد از ارسال واقعاً موفق، به‌عنوان «مصرف‌شده» ثبت می‌شه —
                    # قبلاً این خط قبل از send_mod اجرا می‌شد و اگه ارسال شکست
                    # می‌خورد (حتی بعد از همهٔ retry ها)، مود همون لحظه برای
                    # همیشه سوخته می‌شد و دیگه هیچ‌وقت دوباره امتحان نمی‌شد.
                    state["used_mods_per_channel"][guid] = candidate_used
                    posted_summary.append(f"🎮 {channel['name']}: مود «{item.get('title')}»")
                posted["mod"][mod_key] = True

            if due_video and not posted["video"].get(video_key):
                used_v = state["used_videos_per_channel"].setdefault(guid, [])
                vitem, candidate_used_v = core.pick_item(state["videos"], used_v)
                if vitem is not None:
                    core.send_video(token, channel, vitem)
                    state["used_videos_per_channel"][guid] = candidate_used_v
                    posted_summary.append(f"🎬 {channel['name']}: ویدیو «{vitem['title']}»")
                posted["video"][video_key] = True
        except Exception as e:
            core.log_error(state, f"ارسال به {channel['name']}", e)

    if posted_summary:
        core.notify_owner(token, config, f"📤 گزارش پست {now.strftime('%H:%M')}\n" + "\n".join(posted_summary))


def resolve_post_targets(config, target):
    """target رشتهٔ 'all' یا شمارهٔ کانال (۱-پایه) هست. یه tuple
    (لیست کانال‌ها, پیام خطا یا None) برمی‌گردونه."""
    target = (target or "").strip()
    all_channels = config["destination_channels"]
    if not target:
        return None, "فرمت درست: <شماره کانال یا all>"
    if target.lower() == "all":
        return [c for c in all_channels if c.get("enabled", True)], None
    if target.isdigit():
        idx = int(target) - 1
        if 0 <= idx < len(all_channels):
            return [all_channels[idx]], None
        return None, f"شمارهٔ کانال {target} معتبر نیست."
    return None, "مقدار باید عدد یا all باشه."


def run_force_post_typed(token, config, state, target, kind):
    """kind: 'mod' یا 'video' — فقط همون نوعِ محتوا رو فوری پست می‌کنه،
    بدون توجه به ساعت برنامه."""
    targets, err = resolve_post_targets(config, target)
    if err:
        core.notify_owner(token, config, f"⚠️ پست فوری: {err}")
        return

    kind_fa = "مود" if kind == "mod" else "ویدیو"
    summary = []
    for channel in targets:
        guid = channel["guid"]
        try:
            if kind == "mod":
                used = state["used_mods_per_channel"].setdefault(guid, [])
                item, candidate_used = core.pick_item(state["mods"], used)
                if item is not None:
                    core.send_mod(token, channel, item, state)
                    state["used_mods_per_channel"][guid] = candidate_used
                    summary.append(f"🎮 {channel['name']}: مود «{item.get('title')}»")
            else:
                used = state["used_videos_per_channel"].setdefault(guid, [])
                item, candidate_used = core.pick_item(state["videos"], used)
                if item is not None:
                    core.send_video(token, channel, item)
                    state["used_videos_per_channel"][guid] = candidate_used
                    summary.append(f"🎬 {channel['name']}: ویدیو «{item['title']}»")
        except Exception as e:
            core.log_error(state, f"پست فوری {kind_fa} برای {channel['name']}", e)

    if summary:
        core.notify_owner(token, config, "🚀 پست فوری انجام شد:\n" + "\n".join(summary))
    else:
        core.notify_owner(
            token, config,
            f"ℹ️ پست فوری: هیچ {kind_fa}یِ تکراری‌نشده‌ای برای این کانال(ها) موجود نبود.\n"
            f"(مطمئن شو حداقل یک {kind_fa} در state.json ثبت شده — با /bugs یا پیام وضعیت چک کن.)"
        )


def run_force_post(token, config, state, target):
    """برای سازگاری با ورودی force_post_channel در Actions (فقط مود)."""
    if not (target or "").strip():
        return
    run_force_post_typed(token, config, state, target, "mod")
