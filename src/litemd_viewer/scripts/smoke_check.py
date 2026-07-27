"""Ad-hoc end-to-end check against a running server.

Exercises the endpoints the frontend depends on, then edits a managed document on disk and
watches the index update in place. Usage, from the repository root, with the app running:

    .venv\\Scripts\\python src\\litemd_viewer\\scripts\\smoke_check.py
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:5099"


def _decode(raw: bytes):
    """Parse a JSON body, falling back to a short preview for HTML and other payloads."""
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return raw[:60].decode("utf-8", "replace")


def call(method: str, path: str, body=None):
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.status, _decode(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, _decode(exc.read())


def get(path):
    return call("GET", path)[1]


def show_hits(query: str, k: int = 3) -> None:
    result = get("/api/search?" + urllib.parse.urlencode({"q": query, "k": k}))
    print(f"  query: {query!r}")
    if not result["hits"]:
        print("    (no hits)")
    for hit in result["hits"]:
        title = hit["title"][:40]
        print(f"    {hit['score']:.4f}  {title:<42} chunk {hit['chunkIndex']:>2} "
              f"@ {hit['startOffset']:>6}")
        print(f"            {hit['snippet'][:100]}")
    print()


def main() -> int:
    print("=== static + tree ===")
    status, _ = call("GET", "/index.html")
    print(f"  GET /index.html -> {status}")

    tree = get("/api/tree")
    print(f"  {len(tree['folders'])} folders, {len(tree['files'])} files")
    print(f"  missing on disk: {sum(1 for f in tree['files'] if f['missing'])}")

    print("\n=== error envelope ===")
    status, body = call("POST", "/api/files", {"path": "C:/nope/missing.md"})
    print(f"  add missing file -> {status} {body}")
    status, body = call("GET", "/api/files/99999/content")
    print(f"  unknown document -> {status} {body}")

    print("\n=== graph ===")
    sample = tree["files"][0]
    graph = get(f"/api/files/{sample['id']}/graph")
    print(f"  {sample['title']}: {len(graph['nodes'])} nodes, "
          f"{len(graph['edges'])} edges, {len(graph['companions'])} companions")

    print("\n=== index status ===")
    print("  " + json.dumps(get("/api/search/status")))

    print("\n=== search ===")
    for query in (
        "how do I upgrade a project to .NET 8",
        "database indexing and query performance",
        "storage device configuration",
    ):
        show_hits(query)

    print("=== incremental re-index of a live document ===")
    target = next(
        (f for f in tree["files"] if not f["missing"] and f["fullPath"].endswith(".md")),
        None,
    )
    if target is None:
        print("  no on-disk document to edit; skipping")
        return 0

    path = Path(target["fullPath"])
    original = path.read_text(encoding="utf-8")
    before = get("/api/search/status")
    marker = "Xylophone quokka telemetry is the distinctive marker sentence."

    try:
        path.write_text(
            original + "\n\n## Marker Section\n\n" + marker + "\n",
            encoding="utf-8", newline="",
        )
        print(f"  appended a marker paragraph to {path.name}")

        found = None
        for _ in range(40):
            time.sleep(1)
            result = get("/api/search?" + urllib.parse.urlencode(
                {"q": "xylophone quokka telemetry", "k": 3}
            ))
            if result["hits"] and result["hits"][0]["fileId"] == target["id"]:
                found = result["hits"][0]
                break

        after = get("/api/search/status")
        if found is None:
            print("  FAILED: the new passage never became searchable")
            return 1

        print(f"  found it: chunk {found['chunkIndex']} @ {found['startOffset']} "
              f"score {found['score']:.4f}")
        print(f"  chunks {before['indexedChunks']} -> {after['indexedChunks']} "
              f"(a whole-document re-embed would be far larger)")
    finally:
        path.write_text(original, encoding="utf-8", newline="")
        print(f"  restored {path.name}")

        for _ in range(30):
            time.sleep(1)
            result = get("/api/search?" + urllib.parse.urlencode(
                {"q": "xylophone quokka telemetry", "k": 3}
            ))
            top = result["hits"][0] if result["hits"] else None
            if top is None or top["fileId"] != target["id"]:
                print("  the reverted passage is no longer the top hit")
                break

    final = get("/api/search/status")
    print(f"\n  final: {final['indexedFiles']} files, {final['indexedChunks']} chunks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
