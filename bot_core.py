diff --git a/bot_core.py b/bot_core.py
index de20f29d59ab05492b5ed383e085d7851cf40c83..0eec142b5c9038fb766a0c279ec60007cff8c874 100644
--- a/bot_core.py
+++ b/bot_core.py
@@ -9,111 +9,201 @@ bot_core.py
   جایی در کپشن باشد.
 - جلوگیری از سیل پیام با یک «دفترچهٔ ارسال» سبک (sent_log): قبل از هر
   پیام دوره‌ای (مثل پیام راه‌اندازی)، چک می‌شود که در N دقیقهٔ اخیر
   فرستاده نشده باشد؛ ورودی‌های قدیمی‌تر از ۱ ساعت خودکار پاک می‌شوند.
 - سوییچ per-channel «send_file_directly»: روشن = فایل مستقیم در کانال
   پست شود؛ خاموش (پیش‌فرض) = فقط لینک دریافت از ربات نشان داده شود.
 """
 
 import json
 import re
 import uuid
 from datetime import datetime, timedelta, timezone
 from pathlib import Path
 
 import requests
 
 BASE_DIR = Path(__file__).parent
 CONFIG_PATH = BASE_DIR / "config.json"
 STATE_PATH = BASE_DIR / "state.json"
 
 TEHRAN_OFFSET = timedelta(hours=3, minutes=30)
 MAX_ERRORS_STORED = 200
 ERRORS_PER_PAGE = 5
 ERROR_NOTIFY_COOLDOWN_MINUTES = 10
 REQUEST_TIMEOUT = 20
+MEMBER_STATUSES = {"member", "administrator", "creator", "owner", "admin"}
 
 
 # ---------------------------------------------------------------------------
 # فایل‌های JSON
 # ---------------------------------------------------------------------------
 def load_json(path):
     with open(path, "r", encoding="utf-8") as f:
         return json.load(f)
 
 
 def save_json(path, data):
     with open(path, "w", encoding="utf-8") as f:
         json.dump(data, f, ensure_ascii=False, indent=2)
 
 
 def load_config():
     return load_json(CONFIG_PATH)
 
 
 def load_state():
     return load_json(STATE_PATH)
 
 
 def save_state(state):
     save_json(STATE_PATH, state)
 
 
 def tehran_now():
     return datetime.now(timezone.utc) + TEHRAN_OFFSET
 
 
 # ---------------------------------------------------------------------------
 # ارتباط خام با Rubika Bot API
 # ---------------------------------------------------------------------------
 def api_call(token, method, payload=None):
     url = f"https://botapi.rubika.ir/v3/{token}/{method}"
     resp = requests.post(url, json=payload or {}, timeout=REQUEST_TIMEOUT)
     resp.raise_for_status()
     body = resp.json()
-    return body.get("data", body) if isinstance(body, dict) else body
+    if not isinstance(body, dict):
+        raise RuntimeError(f"پاسخ نامعتبر از {method}: {body!r}")
+    # Rubika may return HTTP 200 even when the API operation itself failed.
+    # Do not report a post as successful until that API-level result is OK.
+    status = body.get("status")
+    if status is not None and str(status).upper() not in {"OK", "SUCCESS"}:
+        detail = body.get("status_det") or body.get("message") or body
+        raise RuntimeError(f"{method} ناموفق بود: {detail}")
+    if body.get("ok") is False:
+        raise RuntimeError(f"{method} ناموفق بود: {body.get('description') or body}")
+    return body.get("data", body)
 
 
 def get_me(token):
     return api_call(token, "getMe")
 
 
 def send_message(token, chat_id, text):
     return api_call(token, "sendMessage", {"chat_id": chat_id, "text": text})
 
 
 def send_file(token, chat_id, file_id, text=""):
     return api_call(token, "sendFile", {"chat_id": chat_id, "file_id": file_id, "text": text})
 
 
 def get_updates(token, offset_id=None, limit=50):
     payload = {"limit": limit}
     if offset_id:
         payload["offset_id"] = offset_id
     return api_call(token, "getUpdates", payload)
 
 
+def get_chat_member(token, channel_guid, user_guid):
+    """Membership check supported by Rubika's Bot API-compatible endpoint."""
+    return api_call(token, "getChatMember", {"chat_id": channel_guid, "user_id": user_guid})
+
+
+def is_member_of_required_channels(token, config, user_guid):
+    """Return (allowed, unchecked_channels).
+
+    A failed lookup is deliberately treated as *not allowed*: force-join must
+    never accidentally leak a file when the membership check is unavailable.
+    """
+    channels = [c for c in config.get("required_channels", []) if c.get("enabled", True)]
+    missing = []
+    for channel in channels:
+        try:
+            result = get_chat_member(token, channel["guid"], user_guid)
+            member = result.get("member", result) if isinstance(result, dict) else {}
+            status = str(member.get("status", "")).lower() if isinstance(member, dict) else ""
+            if status not in MEMBER_STATUSES:
+                missing.append(channel)
+        except Exception as exc:
+            print(f"DEBUG: membership check failed for {channel.get('guid')}: {exc}")
+            missing.append(channel)
+    return not missing, missing
+
+
+def build_join_required_message(channels):
+    lines = ["🔒 برای دریافت فایل ابتدا باید در کانال‌های زیر عضو شوید:"]
+    for channel in channels:
+        name = channel.get("name") or "کانال"
+        link = channel.get("link") or channel.get("channel_link")
+        lines.append(f"• {name}: {link}" if link else f"• {name}")
+    lines.append("\nبعد از عضویت، دوباره /start یا شمارهٔ فایل را بفرستید.")
+    return "\n".join(lines)
+
+
+def record_user_message(state, chat_id, text, config):
+    """Deduplicate repeated input and identify command/message flooding."""
+    spam = config.get("anti_spam", {})
+    window = int(spam.get("window_seconds", 60))
+    report_at = int(spam.get("report_after_messages", 8))
+    cooldown = int(spam.get("report_cooldown_minutes", 30))
+    now = tehran_now().replace(tzinfo=None)
+    users = state.setdefault("user_activity", {})
+    activity = users.setdefault(chat_id, {"messages": [], "last_text": None, "last_response_at": None})
+    messages = activity.setdefault("messages", [])
+    cutoff = now - timedelta(seconds=window)
+    valid = []
+    for stamp in messages:
+        try:
+            if datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S") >= cutoff:
+                valid.append(stamp)
+        except (TypeError, ValueError):
+            continue
+    valid.append(now.strftime("%Y-%m-%d %H:%M:%S"))
+    activity["messages"] = valid
+    duplicate = activity.get("last_text") == text and activity.get("last_response_at") == now.strftime("%Y-%m-%d %H:%M:%S")
+    # Actions polls updates in batches; same-text messages should get one reply
+    # in a short window, while later legitimate requests remain possible.
+    if activity.get("last_text") == text and activity.get("last_response_at"):
+        try:
+            duplicate = (now - datetime.strptime(activity["last_response_at"], "%Y-%m-%d %H:%M:%S")) < timedelta(seconds=int(spam.get("duplicate_seconds", 20)))
+        except ValueError:
+            duplicate = False
+    activity["last_text"] = text
+    activity["last_response_at"] = now.strftime("%Y-%m-%d %H:%M:%S")
+    should_report = len(valid) > report_at
+    if should_report:
+        last_report = activity.get("last_report_at")
+        if last_report:
+            try:
+                should_report = (now - datetime.strptime(last_report, "%Y-%m-%d %H:%M:%S")) >= timedelta(minutes=cooldown)
+            except ValueError:
+                pass
+        if should_report:
+            activity["last_report_at"] = now.strftime("%Y-%m-%d %H:%M:%S")
+    return duplicate, should_report, len(valid)
+
+
 # ---------------------------------------------------------------------------
 # شناسایی کاربر در برابر کانال
 # ---------------------------------------------------------------------------
 def known_channel_guids(config):
     guids = {config.get("source_channel_guid")}
     for ch in config.get("destination_channels", []):
         guids.add(ch.get("guid"))
     guids.discard(None)
     return guids
 
 
 def track_known_user(state, config, chat_id):
     if not chat_id or chat_id in known_channel_guids(config):
         return False
     if chat_id == config.get("owner_guid"):
         return False
     users = state.setdefault("known_users", [])
     if chat_id not in users:
         users.append(chat_id)
         return True
     return False
 
 
 def prune_known_users(state, config):
     """ورودی‌هایی که در واقع کانال هستن (مثلاً چون قبلاً GUID اشتباه بوده)
