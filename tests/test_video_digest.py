"""Tests for video-digest with a fake Telegram Bot API, fake grab and fake summarizer.

Run: python3 -m unittest discover -s tests
"""
import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "video-digest"
FAKES = Path(__file__).resolve().parent / "fakes"
OWNER = "1001"
TOKEN = "TESTTOKEN"


def load_module():
    loader = importlib.machinery.SourceFileLoader("video_digest", str(TOOL))
    spec = importlib.util.spec_from_loader("video_digest", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class FakeTelegram:
    """Bot API stand-in: serves queued updates once, records every call."""

    def __init__(self):
        self.updates, self.calls = [], []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
                token, method = self.path.split("/")[1][3:], self.path.split("/")[2]
                fake.calls.append((method, body))
                if token != TOKEN:
                    result = {"ok": False, "description": "Unauthorized"}
                elif method == "getUpdates":
                    result = {"ok": True, "result": [u for u in fake.updates
                                                     if u["update_id"] >= body.get("offset", 0)]}
                elif method == "sendMessage" and fake.fail_send:
                    result = {"ok": False, "description": "Bad Request: can't parse entities"}
                else:
                    result = {"ok": True, "result": {"message_id": 1}}
                data = json.dumps(result).encode()
                self.send_response(200 if result["ok"] else 400)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.fail_send = False
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def sent(self):
        return [b for m, b in self.calls if m == "sendMessage"]

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


def message(update_id, text, chat=OWNER):
    return {"update_id": update_id,
            "message": {"message_id": update_id * 10, "chat": {"id": int(chat)}, "text": text}}


class VideoDigestTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="vd-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.tg = FakeTelegram()
        self.addCleanup(self.tg.stop)
        self.log = self.tmp / "calls.log"
        self.log.touch()
        self.env = dict(os.environ, TELEGRAM_API=self.tg.url, TELEGRAM_BOT_TOKEN=TOKEN,
                        TELEGRAM_CHAT_ID=OWNER, VD_STATE_DIR=str(self.tmp / "state"),
                        FAKE_LOG=str(self.log),
                        VD_GRAB=f"{sys.executable} {FAKES / 'fake_grab.py'}",
                        VD_SUMMARIZER=f"{sys.executable} {FAKES / 'fake_summarizer.py'}")

    def run_tool(self, *args, **env):
        return subprocess.run([sys.executable, str(TOOL), *args], capture_output=True,
                              text=True, env={**self.env, **env}, timeout=60)

    def items(self):
        path = self.tmp / "state" / "items.json"
        return json.loads(path.read_text()) if path.exists() else []

    def queue(self, *urls):
        self.tg.updates = [message(i + 1, u) for i, u in enumerate(urls)]
        r = self.run_tool("listen", "--once")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.tg.calls.clear()

    # ---- listen

    def test_listen_queues_owner_links_and_ignores_strangers(self):
        self.tg.updates = [
            message(1, "look https://vm.tiktok.com/abc/ and https://youtu.be/xyz."),
            message(2, "https://evil.example/v", chat="999"),
        ]
        r = self.run_tool("listen", "--once")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([i["url"] for i in self.items()],
                         ["https://vm.tiktok.com/abc/", "https://youtu.be/xyz"])
        self.assertTrue(all(i["status"] == "queued" for i in self.items()))
        sent = self.tg.sent()
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["chat_id"], OWNER)
        self.assertEqual(sent[0]["text"], "Queued 2 links.")
        self.assertEqual((self.tmp / "state" / "telegram-offset").read_text(), "3")

    def test_listen_does_not_queue_a_link_twice(self):
        self.queue("https://youtu.be/xyz")
        self.tg.updates = [message(5, "https://youtu.be/xyz")]
        self.run_tool("listen", "--once")
        self.assertEqual(len(self.items()), 1)
        self.assertEqual(self.tg.sent()[0]["text"], "Already queued.")

    def test_listen_answers_a_message_without_links(self):
        self.tg.updates = [message(1, "hello")]
        self.run_tool("listen", "--once")
        self.assertEqual(self.items(), [])
        self.assertIn("Send me a video link", self.tg.sent()[0]["text"])

    def test_listen_resumes_from_saved_offset(self):
        self.queue("https://youtu.be/one")
        self.tg.updates.append(message(2, "https://youtu.be/two"))
        self.run_tool("listen", "--once")
        self.assertEqual([i["url"] for i in self.items()],
                         ["https://youtu.be/one", "https://youtu.be/two"])
        self.assertEqual(self.tg.calls[0][1]["offset"], 2)

    def test_wrong_token_fails_without_queuing(self):
        self.tg.updates = [message(1, "https://youtu.be/one")]
        r = self.run_tool("listen", "--once", TELEGRAM_BOT_TOKEN="WRONG")
        self.assertEqual(r.returncode, 1)
        self.assertIn("Unauthorized", r.stderr)
        self.assertEqual(self.items(), [])

    # ---- process

    def test_process_summarizes_from_transcript_and_metadata(self):
        self.queue("https://youtu.be/one?title=Funnels")
        r = self.run_tool("process")
        self.assertEqual(r.returncode, 0, r.stderr)
        item = self.items()[0]
        self.assertEqual(item["status"], "done")
        self.assertEqual(item["title"], "Funnels")
        self.assertEqual(item["summary"], "SUMMARY Funnels")
        prompt = next(l for l in self.log.read_text().splitlines() if l.startswith("prompt "))
        self.assertIn("spoken words of https://youtu.be/one?title=Funnels", prompt)
        self.assertIn("Do not invent", prompt)

    def test_process_retries_once_then_marks_failed(self):
        self.queue("https://youtu.be/bad?mode=fail")
        self.run_tool("process")
        item = self.items()[0]
        self.assertEqual((item["status"], item["attempts"]), ("queued", 1))
        self.assertIn("grab failed", item["error"])
        self.run_tool("process")
        item = self.items()[0]
        self.assertEqual((item["status"], item["attempts"]), ("failed", 2))
        self.run_tool("process")
        self.assertEqual(self.log.read_text().count("grab "), 2)

    def test_summarizer_failure_is_recorded(self):
        self.queue("https://youtu.be/one")
        self.run_tool("process", FAKE_SUM="fail")
        item = self.items()[0]
        self.assertEqual(item["status"], "queued")
        self.assertIn("usage limit reached", item["error"])

    def test_process_does_not_redo_done_items(self):
        self.queue("https://youtu.be/one")
        self.run_tool("process")
        self.run_tool("process")
        self.assertEqual(self.log.read_text().count("grab "), 1)

    # ---- digest

    def test_digest_sends_done_and_failed_once(self):
        self.queue("https://youtu.be/one?title=A%3Cb%3E%26c", "https://youtu.be/bad?mode=fail")
        self.run_tool("process")
        self.run_tool("process")
        r = self.run_tool("digest")
        self.assertEqual(r.returncode, 0, r.stderr)
        sent = self.tg.sent()
        self.assertEqual(len(sent), 1)
        text = sent[0]["text"]
        self.assertEqual(sent[0]["parse_mode"], "HTML")
        self.assertIn("2 videos", text)
        self.assertIn("A&lt;b&gt;&amp;c", text)  # the title is escaped for HTML
        self.assertIn("Could not process", text)
        self.assertTrue(all(i.get("digested_at") for i in self.items()))
        self.tg.calls.clear()
        self.run_tool("digest")
        self.assertEqual(self.tg.sent(), [])

    def test_digest_skips_items_still_queued(self):
        self.queue("https://youtu.be/one")
        r = self.run_tool("digest")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.tg.sent(), [])
        self.assertIsNone(self.items()[0].get("digested_at"))

    def test_failed_send_leaves_items_for_the_next_digest(self):
        self.queue("https://youtu.be/one")
        self.run_tool("process")
        self.tg.fail_send = True
        r = self.run_tool("digest")
        self.assertNotEqual(r.returncode, 0)
        self.assertIsNone(self.items()[0].get("digested_at"))

    # ---- message packing

    def test_split_messages_stays_under_the_limit(self):
        mod = load_module()
        blocks = [f"block {n} " + "x" * 900 for n in range(10)]
        msgs = mod.split_messages("HEAD", blocks, limit=4096)
        self.assertGreater(len(msgs), 1)
        self.assertTrue(all(len(m) <= 4096 for m in msgs))
        self.assertTrue(all(m.startswith("HEAD") for m in msgs))
        joined = "".join(msgs)
        self.assertTrue(all(f"block {n} " in joined for n in range(10)))

    def test_split_messages_cuts_one_oversized_block(self):
        mod = load_module()
        msgs = mod.split_messages("HEAD", ["y" * 10000], limit=4096)
        self.assertEqual(len(msgs), 1)
        self.assertLessEqual(len(msgs[0]), 4096)


if __name__ == "__main__":
    unittest.main()
