"""Process plugin protocol: read ONE JSON-RPC request from stdin, write ONE response line to stdout."""
import json
import math
import sys


def zscore(values, n):
    out = []
    for i in range(len(values)):
        if i + 1 < n:
            out.append(None)
            continue
        w = values[i + 1 - n:i + 1]
        m = sum(w) / n
        sd = math.sqrt(sum((x - m) ** 2 for x in w) / n)
        out.append(None if sd == 0 else (values[i] - m) / sd)
    return out


req = json.loads(sys.stdin.read())
if req.get("method") == "compute":
    p = req.get("params", {})
    result = {"zscore": zscore(p.get("close", []), int(p.get("length", 20)))}
    print(json.dumps({"jsonrpc": "2.0", "id": req.get("id"), "result": result}))
else:
    print(json.dumps({"jsonrpc": "2.0", "id": req.get("id"), "error": "unknown method"}))
