import json
import urllib.request

url = __BASE_URL__ + "/api/tags"
target = __TARGET__
out = {"available": False, "models": [], "error": None}
try:
    data = json.load(urllib.request.urlopen(url, timeout=10))
    models = [
        str(item.get("name") or item.get("model"))
        for item in data.get("models", [])
        if isinstance(item, dict) and (item.get("name") or item.get("model"))
    ]
    out = {"available": target in models, "models": models, "error": None}
except Exception as exc:
    out = {"available": False, "models": [], "error": str(exc)}
print(json.dumps(out, separators=(",", ":")))
