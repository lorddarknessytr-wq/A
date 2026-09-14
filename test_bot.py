import unittest
from unittest.mock import patch

import bot_core as core
import run_bot


class BotCoreTests(unittest.TestCase):
    def test_api_call_rejects_api_level_failure(self):
        response = type("Response", (), {
            "raise_for_status": lambda self: None,
            "json": lambda self: {"status": "ERROR", "status_det": "not admin"},
        })()
        with patch("bot_core.requests.post", return_value=response):
            with self.assertRaisesRegex(RuntimeError, "not admin"):
                core.api_call("token", "sendMessage")

    def test_force_join_denies_when_a_channel_is_not_a_member(self):
        config = {"required_channels": [{"guid": "c1", "link": "https://rubika.ir/c1"}]}
        with patch("bot_core.get_chat_member", return_value={"status": "Left"}):
            allowed, missing = core.is_member_of_required_channels("token", config, "u1")
        self.assertFalse(allowed)
        self.assertEqual(missing[0]["guid"], "c1")

    def test_duplicate_and_spam_report_are_throttled(self):
        state = {}
        config = {"anti_spam": {"window_seconds": 60, "duplicate_seconds": 20,
                                "report_after_messages": 1, "report_cooldown_minutes": 30}}
        first = core.record_user_message(state, "u1", "/1", config)
        second = core.record_user_message(state, "u1", "/1", config)
        self.assertFalse(first[0])
        self.assertTrue(second[0])
        self.assertTrue(second[1])

    def test_failed_post_is_not_marked_as_used(self):
        state = {"mods": [{"id": "m1", "title": "mod", "photo_file_id": "p", "number": "1"}],
                 "videos": [], "used_mods_per_channel": {}, "used_videos_per_channel": {},
                 "files_by_number": {}}
        channel = {"guid": "c1", "name": "channel", "channel_link": "https://rubika.ir/c1"}
        with patch("run_bot.core.send_mod", side_effect=RuntimeError("not admin")):
            with self.assertRaises(RuntimeError):
                run_bot.post_mod_and_maybe_video("token", channel, state, False)
        self.assertEqual(state["used_mods_per_channel"]["c1"], [])


if __name__ == "__main__":
    unittest.main()
