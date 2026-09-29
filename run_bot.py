"""
run_bot.py — مخزن «ادمین»
---------------------------
این مخزن هیچ‌وقت مستقیم getUpdates نمی‌زنه (فقط مخزن آرشیور این کار رو
می‌کنه). به‌جاش:
  1. فایل state.json مخزن آرشیور رو با GitHub API می‌خونه (فقط خواندن،
     هیچ‌چیز توی مخزن آرشیور نوشته نمی‌شه).
  2. دستورهای مالکی که مخزن آرشیور صف کرده (pending_owner_commands) رو
     پردازش می‌کنه — هر کدوم فقط یک‌بار (با یک نشانگر محلی).
  3. طبق زمان‌بندی/پلن هر کانال، از مودها/ویدیوهای آرشیور، به‌صورت
     خودکار توی کانال‌های مقصد پست می‌کنه.

مودها/ویدیوها و used_mods_per_channel و... همه توی state.json خودِ همین
مخزنه (نه مخزن آرشیور) — یعنی این مخزن هیچ‌وقت چیزی توی مخزن آرشیور
commit نمی‌کنه، فقط می‌خونه.

این مخزن می‌تونه با فاصلهٔ معمولی (مثلاً هر ۱۵-۳۰ دقیقه) اجرا بشه، چون
سرعت پاسخ به کاربر عادی به این مخزن ربطی نداره.
"""

import os
import sys

import bot_core as core


def fetch_archiver_data(config):
    """state.json مخزن آرشیور رو می‌خونه. اگه نشد (PAT اشتباه، مخزن در
    دسترس نیست، ...)، None برمی‌گردونه — صدازننده باید این حالت رو جدا
    مدیریت کنه (نه اینکه انگار آرشیور خالیه)."""
    pat = os.environ.get("ARCHIVER_READ_TOKEN")
    archiver_cfg = config.get("archiver_repo", {})
    owner = archiver_cfg.get("owner")
    repo = archiver_cfg.get("repo")
    branch = archiver_cfg.get("branch", "main")
    path = archiver_cfg.get("state_path", "state.json")

    if not (pat and owner and repo):
        print("DEBUG: تنظیمات مخزن آرشیور (archiver_repo/ARCHIVER_READ_TOKEN) کامل نیست")
        return None

    try:
        return core.fetch_repo_json_file(pat, owner, repo, path, branch)
    except Exception as e:
        print(f"DEBUG: خوندن state.json مخزن آرشیور شکست خورد: {e}")
        return None


def handle_ticket_reply_and_admin_texts(token, config, text, target_guid=None):
    pass  # جا برای گسترش آینده؛ فعلاً /reply مستقیم در handle_owner_command هست


def handle_owner_command(token, config, admin_state, archiver_data, text):
    panel_keyword = config.get("panel_keyword", "پنل")
    broadcast_secret = config.get("broadcast_secret", "")
    owner_guid = config["owner_guid"]

    # مودها/ویدیوها فقط برای خوندن از آرشیوره؛ اینجا (state ادمین) فقط
    # used_mods_per_channel و مشابهش نگه‌داری می‌شه.
    mods = (archiver_data or {}).get("mods", [])
    videos = (archiver_data or {}).get("videos", [])

    if admin_state.get("awaiting_broadcast"):
        admin_state["awaiting_broadcast"] = False
        users = (archiver_data or {}).get("known_users", [])
        sent, failed = 0, 0
        for uid in users:
            try:
                core.send_message(token, uid, text)
                sent += 1
            except Exception:
                failed += 1
        core.send_message(token, owner_guid, f"📣 پیام برای {sent} کاربر ارسال شد. (ناموفق: {failed})")
        return

    if broadcast_secret and text == broadcast_secret:
        admin_state["awaiting_broadcast"] = True
        core.send_message(token, owner_guid, "پیام خودت رو بفرست تا برای همهٔ کاربرها ارسالش کنم.")
        return

    if admin_state.get("awaiting_channelcast"):
        admin_state["awaiting_channelcast"] = False
        sent, failed = core.broadcast_to_channels(token, config, text)
        core.send_message(token, owner_guid, f"📡 پیام در {sent} کانال پست شد. (ناموفق: {failed})")
        return

    if text == "/channelcast":
        admin_state["awaiting_channelcast"] = True
        core.send_message(token, owner_guid, "پیام خودت رو بفرست تا توی همهٔ کانال‌های فعال پست کنم.")
        return

    if text == panel_keyword:
        admin_state["awaiting_panel_selection"] = True
        core.send_message(token, owner_guid, core.build_panel_list(config))
        return

    if admin_state.get("awaiting_panel_selection") and text.isdigit():
        admin_state["awaiting_panel_selection"] = False
        core.send_message(token, owner_guid, core.build_channel_detail(config, admin_state, int(text)))
        return

    if text.startswith("/postmod") or text.startswith("/postvideo") or text.startswith("/post "):
        kind = "video" if text.startswith("/postvideo") else "mod"
        parts = text.split(maxsplit=1)
        target = parts[1].strip() if len(parts) > 1 else ""
        run_force_post_typed(token, config, admin_state, mods, videos, target, kind)
        return

    if text.startswith("/block"):
        parts = text.split(maxsplit=3)
        if len(parts) < 3:
            core.send_message(token, owner_guid, "فرمت درست: /block <chat_id> <روز یا permanent> <دلیل>")
        else:
            target_id, days_part = parts[1], parts[2]
            reason = parts[3] if len(parts) > 3 else ""
            days = None if days_part.lower() == "permanent" else days_part
            try:
                info = core.block_user(admin_state, target_id, reason, days)
                core.send_message(token, owner_guid, f"⛔ {target_id} مسدود شد.\nتا: {info['until'] or 'دائمی'}")
            except Exception as e:
                core.send_message(token, owner_guid, f"خطا در مسدودسازی: {e}")
        return

    if text.startswith("/unblock"):
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            core.send_message(token, owner_guid, "فرمت درست: /unblock <chat_id>")
        else:
            ok = core.unblock_user(admin_state, parts[1])
            core.send_message(token, owner_guid, "✅ رفع مسدودیت شد." if ok else "این آیدی مسدود نبود.")
        return

    if text == "/blocked":
        core.send_message(token, owner_guid, core.build_blocked_list(admin_state))
        return

    if text.startswith("/reply"):
        parts = text.split(maxsplit=2)
        if len(parts) < 3:
            core.send_message(token, owner_guid, "فرمت درست: /reply <GUID> <متن پاسخ>")
        else:
            target_guid, reply_text = parts[1], parts[2]
            try:
                core.send_message(token, target_guid, f"📩 پاسخ پشتیبانی:\n{reply_text}")
                core.send_message(token, owner_guid, "✅ پاسخ ارسال شد.")
            except Exception as e:
                core.send_message(token, owner_guid, f"❌ ارسال ناموفق بود: {e}")
        return

    if text == "/bugs":
        core.send_message(token, owner_guid, core.build_bugs_page(admin_state))
        return

    if text == "/clearbugs":
        n = len(admin_state.get("errors", []))
        admin_state["errors"] = []
        admin_state["bug_page_offset"] = 0
        core.send_message(token, owner_guid, f"🧹 لیست باگ‌ها پاک شد ({n} مورد حذف شد).")
        return

    if text == "/resetall":
        core.send_message(
            token, owner_guid,
            "⚠️ این کار فقط تاریخچهٔ پست هر کانال (used_mods_per_channel) رو "
            "توی مخزن ادمین پاک می‌کنه — برای پاک کردن خودِ مودها/ویدیوها، "
            "این دستور رو توی پیوی ربات از طریق مخزن آرشیور بفرست.\n"
            "برای تأیید همین پاک‌سازی، بفرست: /resetall confirm"
        )
        return

    if text == "/resetall confirm":
        admin_state["used_mods_per_channel"] = {}
        admin_state["used_videos_per_channel"] = {}
        admin_state["posted_slots_today"] = {"date": None, "mod": {}, "video": {}}
        core.send_message(token, owner_guid, "🗑 تاریخچهٔ پست هر کانال (در مخزن ادمین) پاک شد.")
        return

    if not core.should_send_now(admin_state, f"ownerreply:{text}", 3):
        print(f"DEBUG: همین متن به مالک به‌تازگی پاسخ داده شده بود؛ دوباره پاسخ داده نشد: {text!r}")
        return

    n_mods = len(mods)
    n_videos = len(videos)
    n_errors = len(admin_state.get("errors", []))
    now = core.tehran_now().strftime("%Y-%m-%d %H:%M")
    archiver_status = "✅ متصل" if archiver_data is not None else "❌ در دسترس نیست (چک کن ARCHIVER_READ_TOKEN)"
    status = (
        f"✅ ربات (بخش ادمین) فعال است.\n"
        f"🕰 ساعت تهران: {now}\n"
        f"📦 اتصال به آرشیو: {archiver_status}\n"
        f"🎮 مودهای آرشیو: {n_mods}\n"
        f"🎬 ویدیوهای آرشیو: {n_videos}\n"
        f"🐞 باگ‌های ثبت‌شده (ادمین): {n_errors}\n"
        f"برای دیدن گزارش باگ‌ها: /bugs\n"
        f"برای پاک کردن لیست باگ‌ها: /clearbugs\n"
        f"برای پنل کانال‌ها: {panel_keyword}"
    )
    core.send_message(token, owner_guid, status)


def resolve_post_targets(config, target):
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


def run_force_post_typed(token, config, admin_state, mods, videos, target, kind):
    targets, err = resolve_post_targets(config, target)
    owner_guid = config["owner_guid"]
    if err:
        core.send_message(token, owner_guid, f"⚠️ پست فوری: {err}")
        return

    kind_fa = "مود" if kind == "mod" else "ویدیو"
    summary = []
    for channel in targets:
        guid = channel["guid"]
        try:
            if kind == "mod":
                used = admin_state.setdefault("used_mods_per_channel", {}).setdefault(guid, [])
                item, candidate_used = core.pick_item(mods, used)
                if item is not None:
                    core.send_mod(token, channel, item, admin_state)
                    admin_state["used_mods_per_channel"][guid] = candidate_used
                    summary.append(f"🎮 {channel['name']}: مود «{item.get('title')}»")
            else:
                used = admin_state.setdefault("used_videos_per_channel", {}).setdefault(guid, [])
                item, candidate_used = core.pick_item(videos, used)
                if item is not None:
                    core.send_video(token, channel, item)
                    admin_state["used_videos_per_channel"][guid] = candidate_used
                    summary.append(f"🎬 {channel['name']}: ویدیو «{item['title']}»")
        except Exception as e:
            core.log_error(admin_state, f"پست فوری {kind_fa} برای {channel['name']}", e)

    if summary:
        core.send_message(token, owner_guid, "🚀 پست فوری انجام شد:\n" + "\n".join(summary))
    else:
        core.send_message(
            token, owner_guid,
            f"ℹ️ پست فوری: هیچ {kind_fa}یِ تکراری‌نشده‌ای برای این کانال(ها) موجود نبود."
        )


def run_posting_schedule(token, config, admin_state, mods, videos):
    now = core.tehran_now()
    today = now.strftime("%Y-%m-%d")
    minute_of_day = now.hour * 60 + now.minute

    start_h = config["schedule"]["start_hour_tehran"]
    end_h = config["schedule"]["end_hour_tehran"]
    start_min, end_min = start_h * 60, end_h * 60
    if not (start_min <= minute_of_day <= end_min):
        return

    posted = admin_state.setdefault("posted_slots_today", {"date": today, "mod": {}, "video": {}})
    if posted.get("date") != today:
        posted["date"] = today
        posted["mod"] = {}
        posted["video"] = {}

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
                used = admin_state.setdefault("used_mods_per_channel", {}).setdefault(guid, [])
                item, candidate_used = core.pick_item(mods, used)
                if item is not None:
                    core.send_mod(token, channel, item, admin_state)
                    admin_state["used_mods_per_channel"][guid] = candidate_used
                    posted_summary.append(f"🎮 {channel['name']}: مود «{item.get('title')}»")
                posted["mod"][mod_key] = True

            if due_video and not posted["video"].get(video_key):
                used_v = admin_state.setdefault("used_videos_per_channel", {}).setdefault(guid, [])
                vitem, candidate_used_v = core.pick_item(videos, used_v)
                if vitem is not None:
                    core.send_video(token, channel, vitem)
                    admin_state["used_videos_per_channel"][guid] = candidate_used_v
                    posted_summary.append(f"🎬 {channel['name']}: ویدیو «{vitem['title']}»")
                posted["video"][video_key] = True
        except Exception as e:
            core.log_error(admin_state, f"ارسال به {channel['name']}", e)

    if posted_summary:
        core.send_message(token, config["owner_guid"], f"📤 گزارش پست {now.strftime('%H:%M')}\n" + "\n".join(posted_summary))


def main():
    config = core.load_config()
    admin_state = core.load_state()
    token = os.environ.get("RUBIKA_BOT_TOKEN") or config.get("bot_token")
    if not token:
        print("DEBUG: توکن پیدا نشد")
        return

    archiver_data = fetch_archiver_data(config)
    mods = (archiver_data or {}).get("mods", [])
    videos = (archiver_data or {}).get("videos", [])
    print(f"DEBUG: دادهٔ آرشیور -> مودها={len(mods)} ویدیوها={len(videos)} "
          f"pending_owner_commands={len((archiver_data or {}).get('pending_owner_commands', []))}")

    if archiver_data is not None:
        # دستورهای مالک صف‌شده رو پردازش کن — هر id فقط یک‌بار (نشانگر محلی)
        processed_ids = set(admin_state.setdefault("processed_owner_cmd_ids", []))
        new_processed = []
        for cmd in archiver_data.get("pending_owner_commands", []):
            cmd_id = cmd.get("id")
            if not cmd_id or cmd_id in processed_ids:
                continue
            try:
                handle_owner_command(token, config, admin_state, archiver_data, cmd.get("text", ""))
            except Exception as e:
                core.log_error(admin_state, "پردازش دستور صف‌شدهٔ مالک", e)
            new_processed.append(cmd_id)

        if new_processed:
            all_ids = list(processed_ids) + new_processed
            admin_state["processed_owner_cmd_ids"] = all_ids[-500:]  # سقف، تا سنگین نشه
    else:
        print("DEBUG: چون داده‌ی آرشیور در دسترس نبود، دستورهای مالک این دور پردازش نشدن.")

    try:
        run_posting_schedule(token, config, admin_state, mods, videos)
    except Exception as e:
        core.log_error(admin_state, "زمان‌بند پست‌گذاری", e)

    try:
        core.maybe_notify_new_errors(token, config, admin_state)
    except Exception:
        pass

    core.prune_sent_log(admin_state)
    core.save_state(admin_state)


if __name__ == "__main__":
    try:
        main()
    except Exception as fatal:
        print(f"خطای کلی و غیرمنتظره: {fatal}", file=sys.stderr)
        raise
