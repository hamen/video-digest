#!/usr/bin/env python3
"""Stand-in for grab. Usage as called: fake_grab.py -o OUT URL.

Writes OUT/<id>/video.info.json and transcript.txt and prints the folder, like grab.
The URL query sets the case: mode=fail (exit 1, no stdout), mode=silent (empty
transcript), mode=hang (sleep 60 s), title=TEXT (video title). Appends each URL to $FAKE_LOG.
"""
import hashlib
import json
import os
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

out, url = sys.argv[sys.argv.index("-o") + 1], sys.argv[-1]
with open(os.environ["FAKE_LOG"], "a") as f:
    f.write(f"grab {url}\n")
q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
if q.get("mode") == "hang":
    import time
    time.sleep(60)
if q.get("mode") == "fail":
    print("grab: download failed: " + url, file=sys.stderr)
    sys.exit(1)
folder = Path(out) / hashlib.sha1(url.encode()).hexdigest()[:8]
folder.mkdir(parents=True, exist_ok=True)
(folder / "video.info.json").write_text(json.dumps(
    {"title": q.get("title", "A video"), "uploader": "creator", "duration": 42,
     "description": "desc"}))
(folder / "transcript.txt").write_text("" if q.get("mode") == "silent" else f"spoken words of {url}\n")
print(folder)
