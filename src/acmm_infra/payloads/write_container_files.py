import base64
import json
import pathlib
import shutil
import sys

root = pathlib.Path(sys.argv[1])
data = json.loads(sys.argv[2])
replace = sys.argv[3] == "replace"
if replace:
    shutil.rmtree(root, ignore_errors=True)
for path, value in data.items():
    target = root.joinpath(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(base64.b64decode(value))
