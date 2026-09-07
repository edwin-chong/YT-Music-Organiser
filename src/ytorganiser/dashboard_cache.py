"""Local cache backing the dashboard, so viewing/moving/adding songs never
costs YouTube API quota by itself -- only an explicit refresh (or a real
add/move/remove/create) touches the API.

Layout of state/dashboard_cache.json:
{
  "playlists": {playlist_id: {playlist_id, real_id?, title, thumbnail,
                               item_count, read_only, fetched_at,
                               excluded, manual_override, music_ratio}},
  "playlists_fetched_at": iso8601 | null,
  "items": {playlist_id: {"fetched_at": iso8601, "items": [...]}},
  "column_order": [playlist_id, ...],  # user's drag-to-reorder preference
}
"""
import datetime
import json
import os

from . import state as st

CACHE_FILE = os.path.join("state", "dashboard_cache.json")

_DEFAULT = {"playlists": {}, "playlists_fetched_at": None, "items": {}, "column_order": []}


def now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def load() -> dict:
    if not os.path.exists(CACHE_FILE):
        return json.loads(json.dumps(_DEFAULT))
    with open(CACHE_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    for key, default in _DEFAULT.items():
        data.setdefault(key, default)
    return data


def save(data: dict) -> None:
    st.write_json_atomic(CACHE_FILE, data)
