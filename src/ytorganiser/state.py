"""Persistent state so runs are resumable across quota resets/days.

Layout of state/state.json:
{
  "classified": {video_id: {"bucket": str, "title": str}},
  "playlists": {bucket_name: playlist_id},
  "added": {video_id: [playlist_id, ...]},
  "seen_liked_ids": [video_id, ...],   # most-recently-liked first
  "quota": {"date": "YYYY-MM-DD", "used": int}
}
"""
import datetime
import json
import os
import tempfile

STATE_DIR = "state"
STATE_FILE = os.path.join(STATE_DIR, "state.json")

_DEFAULT = {
    "classified": {},
    "playlists": {},
    "added": {},
    "seen_liked_ids": [],
    "quota": {"date": "", "used": 0},
}


def load() -> dict:
    if not os.path.exists(STATE_FILE):
        return json.loads(json.dumps(_DEFAULT))
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    for key, default in _DEFAULT.items():
        data.setdefault(key, default)
    return data


def save(data: dict) -> None:
    write_json_atomic(STATE_FILE, data)


def write_json_atomic(path: str, data: dict) -> None:
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, path)
    except BaseException:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def quota_remaining(data: dict, daily_budget: int) -> int:
    today = datetime.date.today().isoformat()
    if data["quota"]["date"] != today:
        data["quota"] = {"date": today, "used": 0}
    return max(daily_budget - data["quota"]["used"], 0)


def spend_quota(data: dict, units: int, daily_budget: int) -> None:
    today = datetime.date.today().isoformat()
    if data["quota"]["date"] != today:
        data["quota"] = {"date": today, "used": 0}
    data["quota"]["used"] += units
