import unittest
from datetime import datetime
from unittest.mock import patch

import bot_core as core
import run_bot


class BotCoreTests(unittest.TestCase):
    def test_api_error_envelope_raises(self):
        response = type("Response", (), {
            "raise_for_status": lambda self: None,
            "json": lambda self: {"status": "ERROR", "status_det": "not admin"},
        })()
        with patch("bot_core.requests.post", return_value=response):
            with self.assertRaises(core.RubikaAPIError):
                core.api_call("token", "sendFile")

    def test_api_ok_envelope_returns_data(self):
        response = type("Response", (), {
            "raise_for_status": lambda self: None,
            "json": lambda self: {"status": "OK", "data": {"message_id": "1"}},
        })()
        with patch("bot_core.requests.post", return_value=response):
            self.assertEqual(core.api_call("token", "sendFile"), {"message_id": "1"})

    def test_forced_join_denies_when_membership_check_fails(self):
        config = {"forced_join": {"enabled": True, "channels": [{"guid": "c1", "link": "x"}]}}
        with patch("bot_core.get_chat_member", side_effect=core.RubikaAPIError("unsupported")):
            allowed, missing = core.required_memberships("token", config, "u1")
        self.assertFalse(allowed)
        self.assertEqual(missing[0]["guid"], "c1")

    def test_membership_accepts_rubika_chat_member_shape(self):
        self.assertTrue(core.member_is_active({"chat_member": {"status": "Member"}}))

    def test_membership_accepts_identity_only_response(self):
        self.assertTrue(core.member_is_active({"participant": {"user_guid": "u1"}}, "u1"))

    def test_new_media_is_forwarded_not_reuploaded(self):
        entry = {"source_chat_id": "source", "source_message_id": "42", "file_id": "invalid"}
        with patch("bot_core.forward_message") as forward, patch("bot_core.send_file") as send_file:
            core.deliver_media("t", "destination", entry, entry["file_id"])
        forward.assert_called_once_with("t", "destination", "source", "42")
        send_file.assert_not_called()

    def test_legacy_media_does_not_retry_invalid_file_id(self):
        with patch("bot_core.send_file") as send_file:
            with self.assertRaises(core.RubikaAPIError):
                core.deliver_media("t", "destination", {"file_id": "invalid"}, "invalid")
        send_file.assert_not_called()

    def test_channel_plan_uses_elapsed_time_for_late_runs(self):
        self.assertEqual(core.channel_plan({"posting_plan": 0})["video_interval_minutes"], 270)
        self.assertEqual(core.channel_plan({"posting_plan": 2})["mod_interval_minutes"], 30)
        now = datetime(2026, 9, 15, 10, 37)
        self.assertTrue(core.is_due("2026-09-15 10:00", 30, now))
        self.assertFalse(core.is_due("2026-09-15 10:10", 30, now))

    def test_duplicate_request_is_suppressed(self):
        state = {}
        with patch("run_bot.core.tehran_now") as now:
            now.return_value = type("Now", (), {"timestamp": lambda self: 1000})()
            self.assertTrue(run_bot.should_reply_to_duplicate(state, "u1", "#9"))
            self.assertFalse(run_bot.should_reply_to_duplicate(state, "u1", "#9"))

    def test_ticket_report_contains_user_and_chat_guids(self):
        state = {"awaiting_ticket": ["chat-guid"]}
        config = {"texts": {}}
        with patch("run_bot.core.notify_owner") as notify, patch("run_bot.core.send_message"):
            self.assertTrue(run_bot.handle_ticket_flow("t", config, state, "chat-guid", "user-guid", "help"))
        report = notify.call_args.args[2]
        self.assertIn("user-guid", report)
        self.assertIn("chat-guid", report)


if __name__ == "__main__":
    unittest.main()
