#!/usr/bin/env python3
"""Stand-in for bird (X) and the Buffer CLI (LinkedIn).

Appends "post <name> <argv as JSON>" to $FAKE_LOG. Exits 1 when $FAKE_POST_FAIL
names this poster (bird or buffer), like a refused post.
"""
import json
import os
import sys

name = sys.argv[1]
with open(os.environ["FAKE_LOG"], "a") as f:
    f.write(f"post {name} {json.dumps(sys.argv[2:])}\n")
if os.environ.get("FAKE_POST_FAIL") == name:
    print(f"{name}: refused", file=sys.stderr)
    sys.exit(1)
print(f"{name}: ok")
