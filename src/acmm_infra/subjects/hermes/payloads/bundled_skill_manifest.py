import json

from tools.skills_sync import _read_manifest

print(json.dumps(sorted(_read_manifest())))
