import unittest
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
