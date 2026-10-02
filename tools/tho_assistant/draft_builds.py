"""UTG manifest parsing and credential-free draft snapshots."""
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib import request


def parse_manifest(text):
    action = None
    records = []
    for line in (text or "").splitlines():
        marker = re.search(r"_(New|Deleted|Modified):_", line, re.I)
        if marker:
            action = marker.group(1).lower()
        if action:
            for path in re.findall(r"input/sourceOfTruth/[^\s\[|\]]+\.(?:xml|json)", line):
                # Jira link labels and URL paths can repeat the same file.
                name = path.rsplit("/", 1)[-1]
                if re.fullmatch(r"(?:CodeSystem|ValueSet)-[A-Za-z0-9_.-]+\.(?:xml|json)", name):
                    row = {"action": action, "source_path": path,
                           "file": name.rsplit(".", 1)[0] + ".json"}
                    if row not in records:
                        records.append(row)
    return records


def retrieve_drafts(issue, cache_dir, local_files=(), timeout=15, allow_fetch=True):
    key = issue.get("key", "")
    if not re.fullmatch(r"UP-\d+", key):
        return []
    fields = issue.get("fields") or {}
    manifest = parse_manifest(fields.get("customfield_13305"))
    results = []
    for item in manifest:
        row = dict(item)
        if item["action"] == "deleted":
            row["status"] = "declared-deleted"
            results.append(row)
            continue
        url = f"http://utg-submitter-builds.hl7.org:9876/{key}/site/en/{item['file']}"
        cache = Path(cache_dir) / key / (item["file"] + ".snapshot.json")
        row["url"] = url
        try:
            local = next((path for path in local_files if Path(path).name == item["file"]), None)
            if local:
                raw = Path(local).read_bytes()
            else:
                if not allow_fetch:
                    raise ValueError("No local file supplied")
                # Deliberately no Jira Cookie/Authorization headers.
                with request.urlopen(request.Request(url, headers={"Accept": "application/json"}), timeout=timeout) as response:
                    raw = response.read(8 * 1024 * 1024 + 1)
            if len(raw) > 8 * 1024 * 1024:
                raise ValueError("Draft exceeds size limit")
            resource = json.loads(raw)
            expected_type = item["file"].split("-", 1)[0]
            expected_id = item["file"][len(expected_type) + 1:-5]
            if resource.get("resourceType") != expected_type or resource.get("id") != expected_id:
                raise ValueError("Draft resource identity does not match manifest")
            canonical = resource.get("url")
            if not isinstance(canonical, str) or not canonical.startswith(("http://terminology.hl7.org/", "https://terminology.hl7.org/")):
                raise ValueError("Draft canonical is not a THO canonical")
            snapshot = {"resource": resource, "url": url, "retrieved_at": datetime.now(timezone.utc).isoformat(),
                        "sha256": hashlib.sha256(raw).hexdigest(), "origin": "local-file" if local else "live-build"}
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")
            row.update(snapshot, status="local-file" if local else "live")
        except (OSError, ValueError, TimeoutError) as exc:
            row.update(status="unavailable", failure_type=type(exc).__name__)
            if cache.exists():
                try:
                    snapshot = json.loads(cache.read_text(encoding="utf-8"))
                    resource = snapshot.get("resource") or {}
                    expected_type = item["file"].split("-", 1)[0]
                    expected_id = item["file"][len(expected_type) + 1:-5]
                    if (snapshot.get("url") == url and resource.get("resourceType") == expected_type
                            and resource.get("id") == expected_id):
                        row.update(snapshot, status="cached-live-unavailable")
                except (OSError, ValueError):
                    pass
        results.append(row)
    return results
