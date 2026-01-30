import json
from pathlib import Path


def list_leases_hosts(leases_file: Path) -> list[str]:
    if not leases_file.exists():
        return []
    hosts = []
    with leases_file.open("r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            parts = line.strip().split()
            if len(parts) >= 4:
                hosts.append(parts[3])
    return sorted(set(hosts))


def load_known_targets(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    targets: list[dict] = []
    for item in data:
        if isinstance(item, str):
            targets.append({"name": item, "target": item})
        elif isinstance(item, dict):
            name = str(item.get("name") or item.get("target") or "")
            target = str(item.get("target") or "")
            ip = str(item.get("ip") or "")
            if name or target:
                targets.append({"name": name or target, "target": target or name, "ip": ip})
    return targets


def save_known_targets(path: Path, targets: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(targets, indent=2), encoding="utf-8")


def is_ip(value: str) -> bool:
    parts = value.split(".")
    if len(parts) != 4:
        return False
    for p in parts:
        if not p.isdigit():
            return False
        n = int(p)
        if n < 0 or n > 255:
            return False
    return True
