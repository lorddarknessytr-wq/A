"""
mod_archiver.py
----------------
بخش «آرشیو/دریافت مود»: هرچی به خوندن پست‌های کانال منبع (عکس/فایل/
ویدیوی مود) و تحویل فایل به کاربرهایی که شماره درخواست می‌کنن مربوطه،
اینجاست. بخش «ادمین» (پنل، پست‌گذاری توی کانال‌های مقصد، دستورهای
مالک) در channel_admin.py هست.
"""

import re
import uuid

import bot_core as core


def uuid_short():
    return uuid.uuid4().hex[:8]


def handle_source_channel_message(state, msg):
    file_info = msg.get("file")
    caption = msg.get("text") or ""
    message_id = msg.get("message_id")

    # اگر همین آپدیت فایل داره، در حافظهٔ موقت ذخیره‌اش کن (حتی اگه هنوز
    # تگ #مود/#ویدئو نداشته باشه) تا اگه بعداً فقط کپشنش ادیت شد و آپدیتِ
    # ادیت فاقد اطلاعات فایل بود، بتونیم از همین حافظه بازیابی‌اش کنیم.
    if file_info and file_info.get("file_type") and message_id:
        core.remember_recent_file(state, message_id, file_info.get("file_id"), file_info.get("file_type"))

    if not file_info and message_id:
        cached = core.recall_recent_file(state, message_id)
        if cached:
            file_info = {"file_type": cached["file_type"], "file_id": cached["file_id"]}
            print(f"DEBUG: فایل از حافظهٔ موقت بازیابی شد (پیام ادیت‌شده) -> {cached['file_type']}")

    if not file_info:
        return

    file_type = file_info.get("file_type")
    print(f"DEBUG: پیام کانال منبع -> file_type={file_type!r} caption={caption[:60]!r} | file_info خام کامل: {file_info}")

    # تشخیص بر اساس تگِ متن، نه رشتهٔ دقیق file_type — چون معلوم شد مقدار
    # file_type برای عکس‌ها همیشه "Image" نیست (برخلاف ویدیو که "Video" بود)
    mod_parsed = core.parse_mod_caption(caption)
    video_parsed = core.parse_video_caption(caption)

    if mod_parsed is not None:
        state["pending_photo"] = {
            "file_id": file_info.get("file_id"),
            "file_type": file_info.get("file_type"),
            "message_id": message_id,
            **mod_parsed,
        }

    elif video_parsed is not None or file_type == "Video":
        title = (video_parsed or {}).get("title") or caption.strip() or ""
        state["videos"].append({
            "id": uuid_short(),
            "video_file_id": file_info.get("file_id"),
            "file_type": file_info.get("file_type") or "Video",
            "message_id": message_id,
            "source_channel_guid": msg.get("chat_id"),
            "title": title,
        })

    else:
        file_number = core.extract_number(caption)
        pending = state.get("pending_photo")

        if not file_number and pending:
            # فایل هشتگ نداشت؛ برای انعطاف، شمارهٔ عکسِ در انتظار را قرض می‌گیریم
            file_number = pending.get("number")

        if not file_number:
            print("DEBUG: فایل بدون هشتگ شماره (#عدد) و بدون عکس در انتظار، نادیده گرفته شد")
            return

        original_name = (
            file_info.get("file_name") or file_info.get("name")
            or file_info.get("original_name") or file_info.get("title")
        )
        # پسوند دستی: اول از کپشن خودِ فایل، وگرنه از کپشن عکسِ در انتظار
        manual_ext = core.extract_extension_line(caption) or (pending.get("extension") if pending else "")

        state["files_by_number"][file_number] = {
            "file_id": file_info.get("file_id"),
            "file_type": core.normalize_send_file_type(file_info.get("file_type")),
            "file_name": original_name,
            "manual_extension": manual_ext,
            "message_id": message_id,
            "source_channel_guid": msg.get("chat_id"),
            "title": (pending.get("title") if pending else None) or f"فایل شماره {file_number}",
        }

        if pending and pending.get("number") == file_number:
            state["mods"].append({
                "id": uuid_short(),
                "photo_file_id": pending["file_id"],
                "photo_file_type": pending.get("file_type"),
                "photo_message_id": pending.get("message_id"),
                "source_channel_guid": msg.get("chat_id"),
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


def handle_start_command(token, config, state, chat_id):
    welcome = config.get(
        "start_text",
        "👋 سلام و خوش اومدید!\n\n"
        "برای دریافت هر مود، شماره‌ای که زیر همون پست توی کانال نوشته شده "
        "رو برام بفرستید (مثلاً 9 یا #9)، بعد /file رو بزنید تا فایلش براتون بیاد.\n\n"
        "🎫 اگه با پشتیبانی کاری داشتید، با /ticket می‌تونید برام پیام بذارید."
    )
    channels = config.get("required_join_channels", [])
    if channels:
        # عضویت فقط اطلاع‌رسانیه؛ /file عمداً حتی برای فرد غیرعضو هم فایل رو می‌ده.
        welcome += "\n\n" + core.build_join_prompt(channels)
    core.send_message(token, chat_id, welcome)


NUMBER_REQUEST_RE = re.compile(r"^(?:[#/])?(\d+)$")
NUMBER_REQUEST_COOLDOWN_MINUTES = 10


def handle_number_request(token, config, state, chat_id, text):
    m = NUMBER_REQUEST_RE.match((text or "").strip())
    if not m:
        return False

    number = m.group(1)
    entry = state.get("files_by_number", {}).get(number)
    if not entry:
        core.send_message(token, chat_id, f"فایلی با شمارهٔ {number} پیدا نشد.")
        return True

    # اگر کاربر همان درخواست را پشت سر هم تکرار کرد، دوباره پیام تولید نکن.
    if not core.should_send_now(state, f"numreq:{chat_id}:{number}", NUMBER_REQUEST_COOLDOWN_MINUTES):
        print(f"DEBUG: درخواست تکراری #{number} از {chat_id} — نادیده گرفته شد.")
        return True

    state.setdefault("pending_file_requests", {})[chat_id] = {
        "number": number,
        "requested_at": core.tehran_now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    core.send_message(token, chat_id, core.build_join_prompt(config.get("required_join_channels", [])))
    return True


def handle_file_command(token, config, state, chat_id):
    """فایل مود آخرین شماره‌ای که کاربر درخواست کرده را تحویل می‌دهد.
    این مسیر هیچ‌وقت عضویت را چک نمی‌کند."""
    if not chat_id:
        return True

    pending = state.setdefault("pending_file_requests", {}).get(chat_id)
    if not pending:
        core.send_message(token, chat_id, "⚠️ اول شمارهٔ مود را بفرستید؛ سپس برای دریافت آن /file را بزنید.")
        return True

    number = str(pending.get("number", "")).strip()
    entry = state.get("files_by_number", {}).get(number)
    # درخواست یک‌بارمصرف است: حتی اگر فایل خراب/حذف شده باشد، همان درخواست دوباره خودکار اجرا نشود.
    state["pending_file_requests"].pop(chat_id, None)

    if not entry:
        core.send_message(token, chat_id, f"فایل مود شمارهٔ {number} دیگر در انبار ربات پیدا نشد.")
        return True

    core.send_file(
        token,
        chat_id,
        entry["file_id"],
        f"#{number}",
        file_type=entry.get("file_type") or "File",
        file_name=f"{number}{entry.get('manual_extension') or core.file_extension(entry.get('file_name'))}",
        source_chat_id=entry.get("source_channel_guid"),
        source_message_id=entry.get("message_id"),
    )
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
            offset = next_off or core.extract_fallback_offset(batch) or offset
            break
        offset = next_off

    if offset:
        state["last_offset_id"] = offset

    return total_updates, len(state.get("mods", [])) - mods_before, len(state.get("videos", [])) - videos_before
