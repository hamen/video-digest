#!/usr/bin/env python3
"""Stand-in for `claude -p`: reads the prompt on stdin, appends it to $FAKE_LOG,
prints "SUMMARY <title line>". FAKE_SUM=fail exits 1."""
import os
import sys

prompt = sys.stdin.read()
with open(os.environ["FAKE_LOG"], "a") as f:
    f.write("prompt " + prompt.replace("\n", "\\n") + "\n")
if os.environ.get("FAKE_SUM") == "fail":
    print("usage limit reached", file=sys.stderr)
    sys.exit(1)
title = next(l for l in prompt.splitlines() if l.startswith("Title: "))
print("SUMMARY " + title[len("Title: "):])
