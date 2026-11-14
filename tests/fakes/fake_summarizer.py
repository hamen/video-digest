#!/usr/bin/env python3
"""Stand-in for `claude -p`: reads the prompt on stdin, appends it to $FAKE_LOG,
prints "SUMMARY <title line>". FAKE_SUM=fail exits 1; FAKE_SUM=sections prints
the three marked sections, like the real prompt asks."""
import os
import sys

prompt = sys.stdin.read()
with open(os.environ["FAKE_LOG"], "a") as f:
    f.write("prompt " + prompt.replace("\n", "\\n") + "\n")
if os.environ.get("FAKE_SUM") == "fail":
    print("usage limit reached", file=sys.stderr)
    sys.exit(1)
title = next(l for l in prompt.splitlines() if l.startswith("Title: "))
title = title[len("Title: "):]
if os.environ.get("FAKE_SUM") == "sections":
    print(f"SUMMARY:\nAbout {title}.\n- a claim\nAction: try it\n\n"
          f"POST:\nPost about {title}.\n\nSecond paragraph.")
else:
    print("SUMMARY " + title)
