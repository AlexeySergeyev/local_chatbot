import json
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import app, conversation_store, conversation_lock, REQUEST_TIMEOUT


class FakeResponse:
    def __init__(self, lines):
        self._lines = lines
        self.status_code = 200

    def raise_for_status(self):
        pass

    def iter_lines(self, decode_unicode=True):
        for line in self._lines:
            yield line

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class ChatStreamTest(unittest.TestCase):
    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()
        with conversation_lock:
            conversation_store.clear()

    def test_stream_persists_history(self):
        with self.client.session_transaction() as sess:
            sess["conversation_id"] = "test-conv"

        lines = [
            json.dumps({"message": {"role": "assistant", "content": "Hello"}}),
            json.dumps({"message": {"role": "assistant", "content": " world"}}),
            json.dumps({"done": True}),
        ]

        timestamps = [
            "2025-01-01 00:00:00",
            "2025-01-01 00:00:01",
        ]

        with patch("app.requests.post", return_value=FakeResponse(lines)) as mock_post, patch(
            "app.current_timestamp",
            side_effect=timestamps,
        ):
            resp = self.client.post("/chat", json={"message": "Hi"})
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.data.decode(), "Hello world")
            mock_post.assert_called_once()
            _, kwargs = mock_post.call_args
            self.assertEqual(kwargs.get("timeout"), REQUEST_TIMEOUT)
            payload_messages = kwargs.get("json", {}).get("messages", [])
            for message in payload_messages:
                self.assertNotIn("timestamp", message)

        with conversation_lock:
            history = conversation_store.get("test-conv")

        self.assertIsNotNone(history)
        assert history is not None
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["content"], "Hi")
        self.assertEqual(history[1]["content"], "Hello world")
        self.assertEqual(history[0]["timestamp"], timestamps[0])
        self.assertEqual(history[1]["timestamp"], timestamps[1])


if __name__ == "__main__":
    unittest.main()
