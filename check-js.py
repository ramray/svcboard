# -*- coding: utf-8 -*-
"""Dev check: extract the <script> block from svcboard.py's PAGE, fill the
placeholders, write it to %TEMP%\\svcboard-page.js for `node --check`."""
import os
import re
import sys
import tempfile

src = open(sys.argv[1], encoding="utf-8").read()
m = re.search(r"<script>(.*?)</script>", src, re.S)
if not m:
    sys.exit("no <script> block found")
js = m.group(1).replace("__NODES_JSON__", "[]").replace("__LAN__", "x")
out = os.path.join(tempfile.gettempdir(), "svcboard-page.js")
with open(out, "w", encoding="utf-8") as f:
    f.write(js)
print(out)
