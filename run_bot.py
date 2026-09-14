 def handle_source_channel_message(state, msg):
         state["files_by_number"][file_number] = {
             "file_id": file_info.get("file_id"),
             "title": (pending.get("title") if pending else None) or f"فایل شماره {file_number}",
         }
 
         if pending and pending.get("number") == file_number:
             state["mods"].append({
                 "id": uuid_short(),
                 "photo_file_id": pending["file_id"],
                 "title": pending["title"],
                 "description": pending["description"],
                 "version": pending["version"],
                 "number": file_number,
             })
             state["pending_photo"] = None
         elif pending and pending.get("number") != file_number:
             print(f"DEBUG: شمارهٔ فایل (#{file_number}) با شمارهٔ عکسِ در انتظار "
                   f"(#{pending.get('number')}) یکی نیست؛ عکس همچنان منتظر می‌مونه")
         else:
             print(f"DEBUG: فایل #{file_number} بدون عکس همراه، فقط برای دریافت مستقیم ذخیره شد")
 
 
 NUMBER_REQUEST_RE = re.compile(r"^[#/](\d+)$")
 
 
-def handle_number_request(token, state, chat_id, text):
+def handle_number_request(token, config, state, chat_id, text, user_guid=None):
     m = NUMBER_REQUEST_RE.match(text)
     if not m:
         return False
     number = m.group(1)
     entry = state.get("files_by_number", {}).get(number)
     if not entry:
         core.send_message(token, chat_id, f"فایلی با شمارهٔ {number} پیدا نشد.")
     else:
        core.send_file(token, chat_id, entry["file_id"], entry.get("title", ""))
        allowed, missing = core.is_member_of_required_channels(token, config, user_guid or chat_id)
        if not allowed:
            core.send_message(token, chat_id, core.build_join_required_message(missing))
        else:
            core.send_file(token, chat_id, entry["file_id"], entry.get("title", ""))
     return True
 
 
 def run_resync(token, config, state):
     """از ابتدای بافر روبیکا (نه از last_offset_id فعلی) همه‌چیز رو دوباره
     می‌خونه و از کانال منبع، مود/ویدیوهایی که جا مونده بودن رو پیدا می‌کنه.
     برای موقعی که مشکوکیم یه پیام قدیمی رد شده (مثلاً با flush قبلی)."""
     offset = None
     total_updates = 0
     mods_before = len(state.get("mods", []))
     videos_before = len(state.get("videos", []))
 
     for _ in range(30):
         try:
             resp = core.get_updates(token, offset_id=offset, limit=50)
         except Exception as e:
             core.log_error(state, "دستور /update", e)
             break
         batch = resp.get("updates", []) if isinstance(resp, dict) else []
         next_off = resp.get("next_offset_id") if isinstance(resp, dict) else None
         total_updates += len(batch)
 
         for update in batch:
             msg = update.get("new_message") or update.get("updated_message") or update
             if not isinstance(msg, dict):
                 continue
             chat_id = msg.get("chat_id") or update.get("chat_id")
             if chat_id == config.get("source_channel_guid") or (msg.get("file") is not None):
                 print(f"DEBUG: RAW (در /update) update کامل: {update}")
             if chat_id == config.get("source_channel_guid"):
                 try:
                     handle_source_channel_message(state, msg)
                 except Exception as e:
                     core.log_error(state, "پردازش /update", e)
 
         if not batch or not next_off or next_off == offset:
             offset = next_off or offset
             break
         offset = next_off
 
     if offset:
         state["last_offset_id"] = offset
 
     return total_updates, len(state.get("mods", [])) - mods_before, len(state.get("videos", [])) - videos_before
 
 
-def handle_ticket_flow(token, config, state, chat_id, text):
+def handle_ticket_flow(token, config, state, chat_id, text, sender_guid=None):
     """True برمی‌گردونه اگه این پیام بخشی از فرایند تیکت بوده (پردازش شده)."""
     awaiting = state.setdefault("awaiting_ticket", [])
 
     if chat_id in awaiting:
         awaiting.remove(chat_id)
         now = core.tehran_now().strftime("%Y-%m-%d %H:%M")
         core.notify_owner(
             token, config,
             f"🎫 تیکت جدید\n"
            f"از: {chat_id}\n"
            f"شناسهٔ کاربر: {sender_guid or chat_id}\n"
            f"GUID چت: {chat_id}\n"
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
 
 
 def handle_owner_message(token, config, state, text, msg=None):
     panel_keyword = config.get("panel_keyword", "پنل")
     broadcast_secret = config.get("broadcast_secret", "")
 
     if state.get("awaiting_broadcast"):
         state["awaiting_broadcast"] = False
@@ -290,145 +295,151 @@ def handle_owner_message(token, config, state, text, msg=None):
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
         f"برای پنل کانال‌ها: {panel_keyword}\n"
         f"برای راهنما: /help"
     )
     core.send_message(token, config["owner_guid"], status)
 
 
 def post_mod_and_maybe_video(token, channel, state, also_video):
     summary = []
     guid = channel["guid"]
 
     used = state["used_mods_per_channel"].setdefault(guid, [])
     item, used = core.pick_item(state["mods"], used)
    state["used_mods_per_channel"][guid] = used
     if item is not None:
         core.send_mod(token, channel, item, state)
        # Record an item only after Rubika confirms the send succeeded.
        state["used_mods_per_channel"][guid] = used
         summary.append(f"🎮 {channel['name']}: مود «{item.get('title')}»")
 
     if also_video:
         used_v = state["used_videos_per_channel"].setdefault(guid, [])
         vitem, used_v = core.pick_item(state["videos"], used_v)
        state["used_videos_per_channel"][guid] = used_v
         if vitem is not None:
             core.send_video(token, channel, vitem)
            state["used_videos_per_channel"][guid] = used_v
             summary.append(f"🎬 {channel['name']}: ویدیو «{vitem['title']}»")
 
     return summary
 
 
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
 
    failures = 0
     for channel in config["destination_channels"]:
         if not channel.get("enabled", True):
             continue
         try:
             posted_summary += post_mod_and_maybe_video(token, channel, state, also_video)
         except Exception as e:
            failures += 1
             core.log_error(state, f"ارسال به {channel['name']}", e)
 
    posted["hours"].append(hour)
    # A failed API call must be retried on the next scheduled Actions run;
    # the old implementation marked the hour as posted even when every send failed.
    if failures == 0:
        posted["hours"].append(hour)
 
     if posted_summary:
         core.notify_owner(token, config, f"📤 گزارش پست ساعت {hour}:00\n" + "\n".join(posted_summary))
     else:
         core.notify_owner(token, config, f"ℹ️ ساعت {hour}:00 چیزی برای پست‌کردن (مود/ویدیوی تکراری‌نشده) موجود نبود.")
 
 
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
                 item, used = core.pick_item(state["mods"], used)
                state["used_mods_per_channel"][guid] = used
                 if item is not None:
                     core.send_mod(token, channel, item, state)
                    state["used_mods_per_channel"][guid] = used
                     summary.append(f"🎮 {channel['name']}: مود «{item.get('title')}»")
             else:
                 used = state["used_videos_per_channel"].setdefault(guid, [])
                 item, used = core.pick_item(state["videos"], used)
                state["used_videos_per_channel"][guid] = used
                 if item is not None:
                     core.send_video(token, channel, item)
                    state["used_videos_per_channel"][guid] = used
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
 
 
 def main():
     config = core.load_config()
     state = core.load_state()
     token = os.environ.get("RUBIKA_BOT_TOKEN") or config.get("bot_token")
@@ -488,84 +499,102 @@ def main():
 
     force_target = os.environ.get("FORCE_POST_CHANNEL", "")
     if force_target:
         try:
             run_force_post(token, config, state, force_target)
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
         chat_id = msg.get("chat_id") or update.get("chat_id")
        sender_guid = msg.get("author_object_guid") or msg.get("sender_id") or chat_id
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
 
             if chat_id and chat_id == config.get("source_channel_guid"):
                 print(f"DEBUG: RAW پیام کانال منبع (کامل): {msg}")
 
            # Messages from ordinary users are deduplicated before a response,
            # and excessive command/message bursts are reported to the owner.
            if chat_id and chat_id not in (config.get("owner_guid"), config.get("source_channel_guid")):
                duplicate, report_spam, count = core.record_user_message(state, chat_id, text, config)
                if report_spam:
                    core.notify_owner(token, config,
                        f"⚠️ هشدار اسپم/استفادهٔ زیاد از دستورها\n"
                        f"شناسهٔ کاربر: {sender_guid}\nGUID چت: {chat_id}\nتعداد پیام در بازه: {count}")
                if duplicate:
                    continue
                if text == "/start":
                    allowed, missing = core.is_member_of_required_channels(token, config, sender_guid)
                    core.send_message(token, chat_id,
                        config.get("help_text", "برای دریافت فایل، شمارهٔ آن را بفرستید.")
                        if allowed else core.build_join_required_message(missing))
                    continue

             if text == "/myid" and chat_id:
                 core.send_message(token, chat_id, f"GUID این چت:\n{chat_id}")
             elif text == "/help" and chat_id:
                 help_text = config.get("help_text", "برای دریافت فایل مود، شمارهٔ زیر پست رو با # یا / به من بفرستید (مثلاً #1).")
                 core.send_message(token, chat_id, help_text)
            elif chat_id and handle_ticket_flow(token, config, state, chat_id, text):
            elif chat_id and handle_ticket_flow(token, config, state, chat_id, text, sender_guid):
                 pass
            elif not msg.get("file") and handle_number_request(token, state, chat_id, text):
            elif not msg.get("file") and handle_number_request(token, config, state, chat_id, text, sender_guid):
                 pass
             elif chat_id and msg.get("file") and chat_id in (
                 config.get("source_channel_guid"), config.get("owner_guid")
             ):
                 # پست کانال منبع، یا فایل/عکسی که مستقیم (یا فوروارد) به
                 # پیوی خودِ ربات فرستاده شده — هر دو با یک منطق پردازش می‌شن
                 handle_source_channel_message(state, msg)
             elif chat_id and chat_id == config.get("owner_guid"):
                 handle_owner_message(token, config, state, text, msg)
         except Exception as e:
             core.log_error(state, "پردازش پیام", e)
 
     if next_offset:
         state["last_offset_id"] = next_offset
     else:
         print("DEBUG: next_offset خالی بود؛ last_offset_id تغییر نکرد")
 
     try:
         run_posting_schedule(token, config, state)
     except Exception as e:
         core.log_error(state, "زمان‌بند پست‌گذاری", e)
 
     try:
         core.maybe_notify_new_errors(token, config, state)
     except Exception:
