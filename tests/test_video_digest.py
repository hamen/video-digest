"""Tests for video-digest with a fake Telegram Bot API, fake grab and fake summarizer.

Run: python3 -m unittest discover -s tests
"""
import importlib.machinery
import importlib.util
import html
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock
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
                elif method == "sendMessage" and fake.fail_send_from is not None \
                        and len([c for c in fake.calls if c[0] == "sendMessage"]) > fake.fail_send_from:
                    result = {"ok": False, "description": "Bad Request: can't parse entities"}
                else:
                    fake.next_id += 1
                    result = {"ok": True, "result": {"message_id": fake.next_id}}
                data = json.dumps(result).encode()
                self.send_response(200 if result["ok"] else 400)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.fail_send_from = None
        self.next_id = 100
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def sent(self):
        return [b for m, b in self.calls if m == "sendMessage"]

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


def parsed_len(mod, block):
    """Length as Telegram counts it: after it parses the HTML tags and entities."""
    import re
    return mod.tg_len(html.unescape(re.sub(r"<[^>]+>", "", block)))


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
                        VD_SUMMARIZER=f"{sys.executable} {FAKES / 'fake_summarizer.py'}",
                        VD_BIRD=f"{sys.executable} {FAKES / 'fake_poster.py'} bird",
                        VD_BUFFER=f"{sys.executable} {FAKES / 'fake_poster.py'} buffer",
                        BUFFER_API_KEY="bk", BUFFER_LINKEDIN_CHANNEL="chan", BUFFER_X_CHANNEL="",
                        VD_KICK=f"touch {self.tmp / 'kicked'}",
                        VD_SLOP_FILE=str(FAKES / "slop.md"),
                        VD_VOICE_FILE=str(FAKES / "voice.md"))

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
        self.assertEqual(sent[0]["text"], "Queued 2 links. Drafts in a few minutes.")
        self.assertEqual((self.tmp / "state" / "telegram-offset").read_text(), "3")

    def test_listen_does_not_queue_a_link_twice(self):
        self.queue("https://youtu.be/xyz")
        self.tg.updates = [message(5, "https://youtu.be/xyz")]
        self.run_tool("listen", "--once")
        self.assertEqual(len(self.items()), 1)
        self.assertEqual(self.tg.sent()[0]["text"], "Already queued.")

    def test_listen_keeps_the_words_around_the_link_as_the_note(self):
        self.tg.updates = [message(1, "lol this is my Monday {0} https://youtu.be/one"),
                           message(2, "https://youtu.be/two")]
        self.run_tool("listen", "--once")
        one, two = self.items()
        self.assertEqual(one["note"], "lol this is my Monday {0}")
        self.assertNotIn("note", two)

    def test_no_note_from_forwards_several_links_or_bare_punctuation(self):
        fwd = message(1, "this is great https://youtu.be/one")
        fwd["message"]["forward_origin"] = {"type": "user"}
        self.tg.updates = [fwd, message(2, "skip https://youtu.be/two great https://youtu.be/three"),
                           message(3, "(https://youtu.be/four).")]
        self.run_tool("listen", "--once")
        self.assertEqual(len(self.items()), 4)
        self.assertFalse([i for i in self.items() if "note" in i])

    def test_long_note_is_cut_to_the_limit(self):
        self.tg.updates = [message(1, "https://youtu.be/one " + "a" * 1500)]
        self.run_tool("listen", "--once")
        self.assertEqual(self.items()[0]["note"], "a" * 1000)

    def test_the_note_reaches_the_prompt_and_its_absence_is_stated(self):
        self.tg.updates = [message(1, "https://youtu.be/one so true {x} FFS"),
                           message(2, "https://youtu.be/two")]
        self.run_tool("listen", "--once")
        r = self.run_tool("process")
        self.assertEqual(r.returncode, 0, r.stderr)
        one, two = [l for l in self.log.read_text().splitlines() if l.startswith("prompt ")]
        self.assertIn("note on this video (his own words):\\nso true {x} FFS", one)
        self.assertNotIn("note on this video: none.", one)
        self.assertIn("note on this video: none.", two)
        self.assertEqual([i["status"] for i in self.items()], ["done", "done"])

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

    def test_storage_failure_does_not_ack_the_update(self):
        state = self.tmp / "state"
        state.mkdir(parents=True)
        (state / "items.json").write_text("this is not json")
        self.tg.updates = [message(1, "https://youtu.be/one")]
        r = self.run_tool("listen", "--once")
        self.assertEqual(r.returncode, 1)
        self.assertFalse((state / "telegram-offset").exists())
        (state / "items.json").write_text("[]")
        r = self.run_tool("listen", "--once")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([i["url"] for i in self.items()], ["https://youtu.be/one"])

    def test_lost_reply_still_queues_and_acks(self):
        self.tg.fail_send_from = 0
        self.tg.updates = [message(1, "https://youtu.be/one")]
        r = self.run_tool("listen", "--once")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(self.items()), 1)
        self.assertEqual((self.tmp / "state" / "telegram-offset").read_text(), "2")

    def test_braces_in_metadata_do_not_break_the_prompt(self):
        self.queue("https://youtu.be/one?title=%7Bx%7D+and+%7B0%7D")
        r = self.run_tool("process")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.items()[0]["summary"], "SUMMARY {x} and {0}")

    def test_empty_transcript_still_gets_summarized(self):
        self.queue("https://youtu.be/one?mode=silent")
        self.run_tool("process")
        self.assertEqual(self.items()[0]["status"], "done")
        prompt = next(l for l in self.log.read_text().splitlines() if l.startswith("prompt "))
        self.assertIn("If the transcript is empty", prompt)

    def test_hung_grab_counts_as_a_failed_attempt(self):
        self.queue("https://youtu.be/slow?mode=hang", "https://youtu.be/two")
        r = self.run_tool("process", VD_GRAB_TIMEOUT="2")
        self.assertEqual(r.returncode, 0, r.stderr)
        slow, two = self.items()
        self.assertEqual((slow["status"], slow["attempts"]), ("queued", 1))
        self.assertIn("timed out", slow["error"])
        self.assertEqual(two["status"], "done")

    def test_drafts_reach_the_message(self):
        self.queue("https://youtu.be/one?title=Funnels")
        self.run_tool("process", FAKE_SUM="sections")
        item = self.items()[0]
        self.assertEqual(item["summary"], "About Funnels.\n- a claim\nAction: try it")
        link = "\n\nhttps://youtu.be/one?title=Funnels"
        self.assertEqual(item["post"], "Post about Funnels.\n\nSecond paragraph." + link)
        self.assertNotIn("x_post", item)
        text = self.tg.sent()[0]["text"]
        self.assertIn("<b>Draft for 𝕏 and LinkedIn</b>\n<blockquote>Post about Funnels.\n\n"
                      "Second paragraph.\n\nhttps://youtu.be/one?title=Funnels</blockquote>", text)
        self.assertEqual(text.count("<blockquote"), 1)
        prompt = next(l for l in self.log.read_text().splitlines() if l.startswith("prompt "))
        self.assertIn("Never invent an event", prompt)
        self.assertIn("no emoji, no em dashes", prompt)
        self.assertIn("the same text goes to X and to LinkedIn", prompt)

    # ---- real-time messages

    def test_each_video_gets_its_own_message_with_buttons(self):
        self.queue("https://youtu.be/one?title=One", "https://youtu.be/two?title=Two")
        r = self.run_tool("process", FAKE_SUM="sections")
        self.assertEqual(r.returncode, 0, r.stderr)
        sent = self.tg.sent()
        self.assertEqual(len(sent), 2)
        self.assertIn("One", sent[0]["text"])
        self.assertIn("Two", sent[1]["text"])
        row = sent[0]["reply_markup"]["inline_keyboard"][0]
        item = self.items()[0]
        self.assertEqual([b["callback_data"] for b in row],
                         [f"vd:{item['id']}:x", f"vd:{item['id']}:li", f"vd:{item['id']}:both"])
        self.assertTrue(all(i.get("message_id") for i in self.items()))
        self.tg.calls.clear()
        self.run_tool("process")
        self.assertEqual(self.tg.sent(), [])  # never sent twice

    def test_failed_video_message_has_no_buttons(self):
        self.queue("https://youtu.be/bad?mode=fail")
        self.run_tool("process")
        self.assertEqual(self.tg.sent(), [])  # first failure: retry later, say nothing yet
        self.run_tool("process")
        sent = self.tg.sent()
        self.assertEqual(len(sent), 1)
        self.assertIn("Could not process", sent[0]["text"])
        self.assertNotIn("reply_markup", sent[0])

    def test_video_without_drafts_has_no_buttons(self):
        self.queue("https://youtu.be/one")
        self.run_tool("process")  # plain summary, no sections: no drafts
        self.assertNotIn("reply_markup", self.tg.sent()[0])

    def test_unsent_message_is_retried_on_the_next_run(self):
        self.queue("https://youtu.be/one")
        self.tg.fail_send_from = 0
        self.run_tool("process")
        self.assertIsNone(self.items()[0].get("message_id"))
        self.tg.fail_send_from = None
        self.run_tool("process")
        self.assertIsNotNone(self.items()[0].get("message_id"))

    def test_queuing_starts_processing_right_away(self):
        self.queue("https://youtu.be/one")
        self.assertTrue((self.tmp / "kicked").exists())

    def test_duplicate_link_does_not_start_processing(self):
        self.queue("https://youtu.be/one")
        (self.tmp / "kicked").unlink()
        self.tg.updates = [message(9, "https://youtu.be/one")]
        self.run_tool("listen", "--once")
        self.assertFalse((self.tmp / "kicked").exists())

    # ---- buttons

    def press(self, data, who=OWNER, update_id=50):
        self.tg.updates = [{"update_id": update_id, "callback_query": {
            "id": f"cb{update_id}", "from": {"id": int(who)}, "data": data,
            "message": {"message_id": 1, "chat": {"id": int(who)}}}}]
        self.tg.calls.clear()
        r = self.run_tool("listen", "--once")
        self.assertEqual(r.returncode, 0, r.stderr)

    def posts(self):
        return [l for l in self.log.read_text().splitlines() if l.startswith("post ")]

    def ready_item(self):
        self.queue("https://youtu.be/one?title=One")
        self.run_tool("process", FAKE_SUM="sections")
        return self.items()[0]

    def test_x_button_posts_the_draft_once(self):
        item = self.ready_item()
        self.press(f"vd:{item['id']}:x")
        self.assertEqual(self.posts(), ['post bird ["tweet", "--", "Post about One.\\n\\n'
                                        'Second paragraph.\\n\\nhttps://youtu.be/one?title=One"]'])
        self.assertTrue(self.items()[0].get("posted_x"))
        report = self.tg.sent()[0]
        self.assertEqual(report["text"], "✓ X: posted")
        self.assertEqual(report["reply_to_message_id"], item["message_id"])
        self.press(f"vd:{item['id']}:x", update_id=51)
        self.assertEqual(len(self.posts()), 1)
        self.assertIn("already posted", self.tg.sent()[0]["text"])

    def test_failed_save_after_a_post_never_posts_again(self):
        item = self.ready_item()
        mod = load_module()
        cb = {"id": "cb1", "from": {"id": int(OWNER)}, "data": f"vd:{item['id']}:x"}
        with mock.patch.dict(os.environ, self.env):
            with mock.patch.object(mod, "update_item", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    mod.handle_callback(cb, OWNER)  # posted, then the save failed: no ack
            mod.handle_callback(cb, OWNER)  # Telegram resends the same press
        self.assertEqual(len(self.posts()), 1)
        self.assertIn("did not finish", self.tg.sent()[-1]["text"])

    def test_displayed_draft_is_the_published_draft(self):
        self.queue("https://youtu.be/one?title=" + "%26" * 600)
        self.run_tool("process", FAKE_SUM="sections")
        shown = self.tg.sent()[0]["text"]
        item = self.items()[0]
        self.press(f"vd:{item['id']}:x")
        published = json.loads(self.posts()[0][len("post bird "):])[-1]
        self.assertTrue(published.startswith("Post about &&&"))
        self.assertIn(f"<blockquote>{html.escape(published, quote=False)}</blockquote>", shown)

    def test_post_timeout_keeps_the_attempt_and_never_reposts(self):
        item = self.ready_item()
        mod = load_module()
        cb = {"id": "cb1", "from": {"id": int(OWNER)}, "data": f"vd:{item['id']}:x"}
        hang = subprocess.TimeoutExpired("bird", 120)
        with mock.patch.dict(os.environ, self.env):
            with mock.patch.object(mod.subprocess, "run", side_effect=hang) as run:
                mod.handle_callback(cb, OWNER)
                mod.handle_callback(cb, OWNER)
        self.assertEqual(run.call_count, 1)
        self.assertTrue(self.items()[0].get("posting_x"))
        self.assertIn("did not finish", self.tg.sent()[-1]["text"])

    def test_unexpected_error_on_one_video_does_not_stop_the_queue(self):
        self.queue("https://youtu.be/one", "https://youtu.be/two")
        mod = load_module()
        real = mod.process_one
        bad = lambda item: (_ for _ in ()).throw(TypeError("bad metadata")) \
            if item["url"].endswith("one") else real(item)
        with mock.patch.dict(os.environ, self.env), mock.patch.object(mod, "process_one", bad):
            self.assertEqual(mod.cmd_process(), 0)
        one, two = self.items()
        self.assertEqual((one["status"], one["attempts"]), ("queued", 1))
        self.assertIn("TypeError", one["error"])
        self.assertEqual(two["status"], "done")

    def test_every_prompt_carries_the_slop_patterns(self):
        self.queue("https://youtu.be/one")
        self.run_tool("process")
        (prompt,) = [l for l in self.log.read_text().splitlines() if l.startswith("prompt ")]
        self.assertIn("FAKE-SLOP-01", prompt)
        self.assertNotIn("not part of the patterns", prompt)
        self.assertNotIn("FAKE-AFTER", prompt)

    def test_missing_slop_file_fails_the_video(self):
        self.queue("https://youtu.be/one")
        self.run_tool("process", VD_SLOP_FILE=str(self.tmp / "missing.md"))
        item = self.items()[0]
        self.assertEqual(item["status"], "queued")
        self.assertIn("AI Slop Detector", item["error"])
        self.assertNotIn("post", item)

    def test_slop_file_without_patterns_section_fails_the_video(self):
        bad = self.tmp / "bad.md"
        bad.write_text("# Skill\n\nSee the ## Patterns section.\n")
        self.queue("https://youtu.be/one")
        self.run_tool("process", VD_SLOP_FILE=str(bad))
        self.assertIn("no '## Patterns' section", self.items()[0]["error"])
        self.assertFalse([l for l in self.log.read_text().splitlines() if l.startswith("prompt ")])

    def test_default_grab_names_the_keyring(self):
        mod = load_module()
        env = {k: v for k, v in self.env.items() if k != "VD_GRAB"}
        done = subprocess.CompletedProcess([], 1, "", "grab: stop here")
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(mod.subprocess, "run", return_value=done) as run:
            with self.assertRaises(RuntimeError):
                mod.process_one({"url": "https://youtu.be/one"})
        argv = run.call_args[0][0]
        self.assertEqual(argv[argv.index("--cookies-from-browser") + 1], "chrome+gnomekeyring")

    def test_process_unit_path_has_uvx_before_the_system_dirs(self):
        unit = (ROOT / "systemd" / "video-digest-process.service").read_text()
        path = next(l for l in unit.splitlines() if l.startswith("Environment=PATH="))
        dirs = path.split("=", 2)[2].split(":")
        self.assertLess(dirs.index("/home/linuxbrew/.linuxbrew/bin"), dirs.index("/usr/bin"))

    def test_every_prompt_carries_the_x_voice_without_front_matter(self):
        self.queue("https://youtu.be/one")
        self.run_tool("process")
        (prompt,) = [l for l in self.log.read_text().splitlines() if l.startswith("prompt ")]
        self.assertIn("FAKE-VOICE-RULE", prompt)
        self.assertNotIn("name: fake-voice", prompt)

    def test_missing_voice_file_fails_the_video(self):
        self.queue("https://youtu.be/one")
        self.run_tool("process", VD_VOICE_FILE=str(self.tmp / "missing.md"))
        item = self.items()[0]
        self.assertIn("ivan-x-voice", item["error"])
        self.assertNotIn("post", item)

    def test_empty_or_front_matter_only_voice_file_fails_the_video(self):
        for i, body in enumerate(["", "---\nname: x\n---\n", "\ufeff---\r\nname: x\r\n---\r\n"]):
            voice = self.tmp / f"voice{i}.md"
            voice.write_text(body)
            self.queue(f"https://youtu.be/v{i}")
            self.run_tool("process", VD_VOICE_FILE=str(voice))
        for item in self.items():
            self.assertIn("ivan-x-voice", item["error"])
            self.assertNotIn("post", item)
        self.assertFalse([l for l in self.log.read_text().splitlines() if l.startswith("prompt ")])

    def test_draft_starting_with_a_dash_is_not_a_flag(self):
        item = self.ready_item()
        self.press(f"vd:{item['id']}:x")
        argv = json.loads(self.posts()[0][len("post bird "):])
        self.assertEqual(argv[:2], ["tweet", "--"])

    def test_link_queued_during_processing_joins_the_run(self):
        self.queue("https://youtu.be/one")
        r = self.run_tool("process", FAKE_GRAB_QUEUE_URL="https://youtu.be/two")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([i["status"] for i in self.items()], ["done", "done"])

    def test_linkedin_button_uses_buffer_share_now(self):
        item = self.ready_item()
        self.press(f"vd:{item['id']}:li")
        (line,) = self.posts()
        self.assertTrue(line.startswith("post buffer "))
        argv = json.loads(line[len("post buffer "):])
        self.assertEqual(argv[argv.index("--mode") + 1], "shareNow")
        self.assertEqual(argv[argv.index("--channel-id") + 1], "chan")
        self.assertEqual(next(a for a in argv if a.startswith("--text=")),
                         "--text=Post about One.\n\nSecond paragraph.\n\nhttps://youtu.be/one?title=One")

    def test_both_after_x_posts_only_linkedin(self):
        item = self.ready_item()
        self.press(f"vd:{item['id']}:x")
        self.press(f"vd:{item['id']}:both", update_id=51)
        self.assertEqual([l.split()[1] for l in self.posts()], ["bird", "buffer"])
        self.assertIn("X: already posted", self.tg.sent()[0]["text"])

    def test_refused_post_reports_the_reason(self):
        item = self.ready_item()
        self.tg.updates = [{"update_id": 60, "callback_query": {
            "id": "cb60", "from": {"id": int(OWNER)}, "data": f"vd:{item['id']}:x",
            "message": {"message_id": 1, "chat": {"id": int(OWNER)}}}}]
        self.run_tool("listen", "--once", FAKE_POST_FAIL="bird")
        self.assertIsNone(self.items()[0].get("posted_x"))
        self.assertIn("✗ X: bird: refused", self.tg.sent()[-1]["text"])
        self.press(f"vd:{item['id']}:x", update_id=61)
        self.assertTrue(self.items()[0].get("posted_x"))
        self.assertEqual(len(self.posts()), 2)

    def test_x_goes_through_buffer_when_its_channel_is_set(self):
        item = self.ready_item()
        self.tg.updates = [{"update_id": 80, "callback_query": {
            "id": "cb80", "from": {"id": int(OWNER)}, "data": f"vd:{item['id']}:x",
            "message": {"message_id": 1, "chat": {"id": int(OWNER)}}}}]
        self.run_tool("listen", "--once", BUFFER_X_CHANNEL="xchan")
        (line,) = self.posts()
        argv = json.loads(line[len("post buffer "):])
        self.assertEqual(argv[argv.index("--channel-id") + 1], "xchan")
        self.assertEqual(argv[argv.index("--mode") + 1], "shareNow")
        self.assertTrue(self.items()[0].get("posted_x"))

    def test_x_through_buffer_without_a_key_fails_and_never_falls_back_to_bird(self):
        item = self.ready_item()
        self.tg.updates = [{"update_id": 81, "callback_query": {
            "id": "cb81", "from": {"id": int(OWNER)}, "data": f"vd:{item['id']}:x",
            "message": {"message_id": 1, "chat": {"id": int(OWNER)}}}}]
        self.tg.calls.clear()
        self.run_tool("listen", "--once", BUFFER_X_CHANNEL="xchan", BUFFER_API_KEY="")
        self.assertEqual(self.posts(), [])
        self.assertIsNone(self.items()[0].get("posted_x"))
        self.assertIn("Buffer is not configured (BUFFER_API_KEY, BUFFER_X_CHANNEL)",
                      self.tg.sent()[0]["text"])

    def test_linkedin_without_buffer_config_says_so(self):
        item = self.ready_item()
        self.tg.updates = [{"update_id": 70, "callback_query": {
            "id": "cb70", "from": {"id": int(OWNER)}, "data": f"vd:{item['id']}:both",
            "message": {"message_id": 1, "chat": {"id": int(OWNER)}}}}]
        self.tg.calls.clear()
        self.run_tool("listen", "--once", BUFFER_API_KEY="")
        text = self.tg.sent()[0]["text"]
        self.assertIn("✓ X: posted", text)
        self.assertIn("Buffer is not configured", text)

    def test_stranger_cannot_press_the_buttons(self):
        item = self.ready_item()
        self.press(f"vd:{item['id']}:both", who="999")
        self.assertEqual(self.posts(), [])
        self.assertEqual(self.tg.sent(), [])
        self.assertEqual(self.tg.calls[-1][0], "answerCallbackQuery")

    def test_unknown_button_data_posts_nothing(self):
        self.ready_item()
        self.press("vd:nope:x")
        self.press("something:else", update_id=51)
        self.assertEqual(self.posts(), [])

    # ---- message packing

    def test_long_html_title_is_clipped_before_escaping(self):
        mod = load_module()
        item = {"url": "https://x.example/v", "status": "done", "uploader": "u",
                "title": "&" * 2000, "summary": "<b>" * 2000}
        block = mod.format_item(item)
        self.assertLess(parsed_len(mod, block), 4096)
        # every & is part of a whole entity: no "&am" or "&l" fragment at a cut
        self.assertNotRegex(block.replace("&amp;", "").replace("&lt;", "").replace("&gt;", ""), "&")

    def test_parse_sections_handles_none_and_missing_markers(self):
        mod = load_module()
        out = mod.parse_sections("SUMMARY:\nNo speech in this video.\n\nPOST:\nNone.")
        self.assertEqual(out, {"summary": "No speech in this video.", "post": ""})
        out = mod.parse_sections("just a plain summary")
        self.assertEqual(out["summary"], "just a plain summary")
        self.assertEqual(out["post"], "")

    def test_worst_case_block_fits_one_message(self):
        mod = load_module()
        item = {"url": "https://x.example/" + "p" * 280, "status": "done", "uploader": "&" * 500,
                "title": "&" * 2000, "summary": "&" * 5000, "post": "&" * 5000}
        self.assertLessEqual(parsed_len(mod, mod.format_item(item)), 4096)

    def test_the_video_link_follows_the_draft_and_fits_the_budget(self):
        mod = load_module()
        url = "https://vm.tiktok.com/ZNabc/"
        self.assertEqual(mod.with_link("A post", url, 600), "A post\n\n" + url)
        long = mod.with_link("word " * 300, url, 600)
        self.assertTrue(long.endswith("…\n\n" + url))
        self.assertLessEqual(mod.tg_len(long), 600)
        self.assertEqual(mod.with_link("", url, 600), "")
        huge = "https://x.example/" + "p" * 300
        self.assertEqual(mod.with_link("A post", huge, 600), "A post")

    def test_old_items_pick_their_draft(self):
        mod = load_module()
        old = {"id": "abc", "x_post": "short X text", "linkedin_post": "the LinkedIn text"}
        self.assertEqual(mod.draft_of(old), "the LinkedIn text")
        self.assertEqual(len(mod.keyboard(old)["inline_keyboard"][0]), 3)
        self.assertIn("the LinkedIn text", mod.format_item({**old, "url": "https://u", "status": "done",
                                                            "summary": "s"}))
        self.assertEqual(mod.draft_of({"x_post": "only X"}), "only X")
        self.assertEqual(mod.draft_of({"post": "", "linkedin_post": "stale"}), "")
        self.assertIsNone(mod.keyboard({"id": "abc", "post": "", "linkedin_post": "stale"}))

    def test_x_button_on_an_old_item_posts_its_linkedin_draft(self):
        item = self.ready_item()
        state = self.tmp / "state" / "items.json"
        data = json.loads(state.read_text())
        data[0].pop("post")
        data[0].update(x_post="short X text", linkedin_post="the LinkedIn text")
        state.write_text(json.dumps(data))
        self.press(f"vd:{item['id']}:both")
        self.assertEqual(self.posts()[0], 'post bird ["tweet", "--", "the LinkedIn text"]')
        self.assertIn("--text=the LinkedIn text", self.posts()[1])

    def test_tg_len_counts_emoji_as_two(self):
        mod = load_module()
        self.assertEqual(mod.tg_len("a🎬"), 3)


if __name__ == "__main__":
    unittest.main()
