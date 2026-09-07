"""Local dashboard: visualize your playlists (incl. read-only Liked Videos)
and drag-and-drop songs between them. Runs entirely on localhost.

Everything you *view* comes from a local cache (state/dashboard_cache.json)
-- loading the board or a column never calls the YouTube API. Only three
things touch the API and spend quota: an explicit refresh (you choose what:
one playlist, just discovering new playlists, or everything), and real
mutations (move/add/remove/create), which also update the cache directly so
you don't need to re-fetch afterwards.
"""
import os

from flask import Flask, jsonify, request, send_from_directory

from . import auth
from . import dashboard_cache as dc
from . import state as st
from . import youtube_client as yt

LIKED_PLAYLIST_KEY = "LIKED_VIDEOS"  # synthetic id used in the UI only

# A playlist is auto-excluded (hidden from the board by default) once we've
# seen its songs and fewer than this fraction are YouTube's Music category --
# catches non-music playlists like a manhwa reading list or a game-guide
# playlist mixed in among your real music playlists. A manual include/exclude
# always wins over this once set.
MUSIC_MINORITY_THRESHOLD = 0.5

_static_dir = os.path.join(os.path.dirname(__file__), "static")

app = Flask(__name__, static_folder=_static_dir, static_url_path="")
_youtube = None


def _service():
    global _youtube
    if _youtube is None:
        _youtube = auth.get_service()
    return _youtube


def _budget() -> int:
    return int(os.environ.get("DAILY_QUOTA_BUDGET", "9500"))


def _quota_error_response(e):
    return jsonify({"error": str(e)}), 429


# ---- cache-populating helpers (the only places that call the YouTube API) ----


def _visibility_defaults() -> dict:
    return {"excluded": False, "manual_override": None, "music_ratio": None}


def _refresh_playlists(cache: dict, *, only_new: bool = False) -> None:
    need_liked_id = LIKED_PLAYLIST_KEY not in cache["playlists"]
    data = st.load()
    youtube = _service()
    try:
        live = yt.list_playlists_full(youtube, data, _budget())
        if need_liked_id:
            liked_id = yt.get_liked_playlist_id(youtube, data, _budget())
    finally:
        st.save(data)

    if need_liked_id:
        cache["playlists"][LIKED_PLAYLIST_KEY] = {
            "playlist_id": LIKED_PLAYLIST_KEY,
            "real_id": liked_id,
            "title": "Liked Videos",
            "thumbnail": "",
            "item_count": None,
            "read_only": True,
            **_visibility_defaults(),  # Liked Videos is never auto/manually excluded in practice
        }

    for p in live:
        if only_new and p["playlist_id"] in cache["playlists"]:
            continue
        existing = cache["playlists"].get(p["playlist_id"])
        if existing is None:
            cache["playlists"][p["playlist_id"]] = {**p, "read_only": False, **_visibility_defaults()}
        else:
            cache["playlists"][p["playlist_id"]] = {**existing, **p, "read_only": False}

    cache["playlists_fetched_at"] = dc.now_iso()
    dc.save(cache)


def _refresh_items(cache: dict, playlist_key: str) -> list:
    meta = cache["playlists"].get(playlist_key)
    if meta is None:
        raise ValueError(f"Unknown playlist {playlist_key!r}; refresh the playlist list first.")
    real_id = meta.get("real_id", playlist_key)

    data = st.load()
    youtube = _service()
    try:
        items = yt.list_playlist_items_full(youtube, real_id, data, _budget())
    finally:
        st.save(data)

    cache["items"][playlist_key] = {"fetched_at": dc.now_iso(), "items": items}
    if not meta.get("read_only"):
        meta["item_count"] = len(items)
        if items:
            music_count = sum(1 for it in items if it.get("category_id") == yt.MUSIC_CATEGORY_ID)
            meta["music_ratio"] = round(music_count / len(items), 2)
            if meta.get("manual_override") is None:
                meta["excluded"] = meta["music_ratio"] < MUSIC_MINORITY_THRESHOLD
    dc.save(cache)
    return items


def _sorted_playlists(cache: dict) -> list:
    order = {pid: i for i, pid in enumerate(cache.get("column_order", []))}
    return sorted(cache["playlists"].values(), key=lambda p: order.get(p["playlist_id"], len(order)))


# ---------------------------------- routes ----------------------------------


@app.get("/")
def index():
    return send_from_directory(_static_dir, "index.html")


@app.get("/api/quota")
def quota():
    data = st.load()
    budget = _budget()
    return jsonify({"budget": budget, "remaining": st.quota_remaining(data, budget)})


@app.get("/api/playlists")
def list_playlists():
    cache = dc.load()
    if not cache["playlists"]:
        try:
            _refresh_playlists(cache)
        except (yt.QuotaExceededError, yt.QuotaBudgetExhausted) as e:
            return _quota_error_response(e)
    return jsonify({"playlists": _sorted_playlists(cache), "fetched_at": cache["playlists_fetched_at"]})


@app.post("/api/column-order")
def set_column_order():
    body = request.get_json(force=True)
    order = body.get("order")
    if not isinstance(order, list) or not all(isinstance(x, str) for x in order):
        return jsonify({"error": "order must be a list of playlist ids"}), 400
    cache = dc.load()
    cache["column_order"] = order
    dc.save(cache)
    return jsonify({"ok": True})


@app.get("/api/playlists/<playlist_id>/items")
def list_items(playlist_id):
    cache = dc.load()
    entry = cache["items"].get(playlist_id)
    if entry is None:
        try:
            items = _refresh_items(cache, playlist_id)
        except (yt.QuotaExceededError, yt.QuotaBudgetExhausted) as e:
            return _quota_error_response(e)
        except ValueError as e:
            return jsonify({"error": str(e)}), 404
        return jsonify({"items": items, "fetched_at": cache["items"][playlist_id]["fetched_at"]})
    return jsonify({"items": entry["items"], "fetched_at": entry["fetched_at"]})


@app.post("/api/refresh")
def refresh():
    """body: {target: "playlists" | "new_playlists" | "all"}
    or:      {target: "items", playlist_id: "..."}
    """
    body = request.get_json(force=True)
    target = body.get("target")
    cache = dc.load()
    try:
        if target == "playlists":
            _refresh_playlists(cache)
        elif target == "new_playlists":
            _refresh_playlists(cache, only_new=True)
        elif target == "items":
            playlist_id = body.get("playlist_id")
            if not playlist_id:
                return jsonify({"error": "playlist_id is required for target=items"}), 400
            _refresh_items(cache, playlist_id)
        elif target == "all":
            _refresh_playlists(cache)
            for playlist_id in list(cache["playlists"].keys()):
                _refresh_items(cache, playlist_id)
        else:
            return jsonify({"error": f"unknown target {target!r}"}), 400
    except (yt.QuotaExceededError, yt.QuotaBudgetExhausted) as e:
        return _quota_error_response(e)
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    return jsonify({"ok": True})


@app.post("/api/playlists")
def create_playlist():
    body = request.get_json(force=True)
    title = (body.get("title") or "").strip()
    if not title:
        return jsonify({"error": "title is required"}), 400
    data = st.load()
    youtube = _service()
    try:
        playlist_id = yt.create_playlist(youtube, title, "Created from the ytorganiser dashboard.", data, _budget())
    except (yt.QuotaExceededError, yt.QuotaBudgetExhausted) as e:
        return _quota_error_response(e)
    finally:
        st.save(data)

    cache = dc.load()
    entry = {
        "playlist_id": playlist_id,
        "title": title,
        "thumbnail": "",
        "item_count": 0,
        "read_only": False,
        **_visibility_defaults(),
    }
    cache["playlists"][playlist_id] = entry
    cache["items"][playlist_id] = {"fetched_at": dc.now_iso(), "items": []}
    dc.save(cache)
    return jsonify(entry)


@app.post("/api/playlists/from-source")
def create_playlist_from_source():
    """body: {source_playlist_id, title, item_ids: [playlist_item_id, ...]}.
    Creates a new playlist containing only the selected songs, copied from
    source_playlist_id -- the source is left untouched (works on Liked Videos
    too, since this only ever adds to the new playlist).

    Resilient by design: a quota cutoff stops the whole copy (remaining songs
    just don't get added), but any other per-video failure (deleted/private/
    region-blocked video, etc.) only skips that one song -- and the playlist
    is always saved to the cache with whatever it got, even if something
    raises partway through, so a bad video can never lose track of an
    already-created playlist the way it used to.
    """
    body = request.get_json(force=True)
    source_id = body.get("source_playlist_id")
    title = (body.get("title") or "").strip()
    item_ids = body.get("item_ids")
    if not source_id or not title:
        return jsonify({"error": "source_playlist_id and title are required"}), 400
    if not isinstance(item_ids, list) or not item_ids:
        return jsonify({"error": "Select at least one song."}), 400
    wanted = set(item_ids)

    cache = dc.load()
    source_meta = cache["playlists"].get(source_id)
    if source_meta is None:
        return jsonify({"error": "Unknown source playlist; refresh the playlist list first."}), 404

    if source_id not in cache["items"]:
        try:
            _refresh_items(cache, source_id)
        except (yt.QuotaExceededError, yt.QuotaBudgetExhausted) as e:
            return _quota_error_response(e)
    source_items = [it for it in cache["items"][source_id]["items"] if it["playlist_item_id"] in wanted]

    data = st.load()
    youtube = _service()
    try:
        playlist_id = yt.create_playlist(
            youtube, title, f"Copied from '{source_meta['title']}' via the ytorganiser dashboard.", data, _budget()
        )
    except (yt.QuotaExceededError, yt.QuotaBudgetExhausted) as e:
        st.save(data)
        return _quota_error_response(e)

    copied_items = []
    skipped = 0
    stopped_due_to_quota = False
    try:
        for item in source_items:
            try:
                new_item_id = yt.add_video_to_playlist(youtube, playlist_id, item["video_id"], data, _budget())
            except (yt.QuotaExceededError, yt.QuotaBudgetExhausted):
                stopped_due_to_quota = True
                break
            except Exception as e:
                print(f"Skipping {item.get('title', item['video_id'])!r} -- YouTube rejected it: {e}")
                skipped += 1
                continue
            copied_items.append({**item, "playlist_item_id": new_item_id})
    finally:
        st.save(data)
        entry = {
            "playlist_id": playlist_id,
            "title": title,
            "thumbnail": "",
            "item_count": len(copied_items),
            "read_only": False,
            **_visibility_defaults(),
        }
        cache["playlists"][playlist_id] = entry
        cache["items"][playlist_id] = {"fetched_at": dc.now_iso(), "items": copied_items}
        dc.save(cache)

    return jsonify(
        {
            "playlist": entry,
            "copied": len(copied_items),
            "skipped": skipped,
            "total": len(source_items),
            "stopped_due_to_quota": stopped_due_to_quota,
        }
    )


@app.post("/api/move")
def move_item():
    """body: {video_id, to_playlist_id, from_playlist_id?, from_item_id?, title?, thumbnail?, duration_seconds?}
    If from_playlist_id/from_item_id are omitted (or the source is Liked
    Videos), this is just an add -- Liked Videos can't be edited via the API.
    """
    body = request.get_json(force=True)
    video_id = body.get("video_id")
    to_playlist_id = body.get("to_playlist_id")
    from_playlist_id = body.get("from_playlist_id")
    from_item_id = body.get("from_item_id")
    if not video_id or not to_playlist_id:
        return jsonify({"error": "video_id and to_playlist_id are required"}), 400

    data = st.load()
    youtube = _service()
    try:
        new_item_id = yt.add_video_to_playlist(youtube, to_playlist_id, video_id, data, _budget())
        removed = False
        if from_item_id and from_playlist_id and from_playlist_id != LIKED_PLAYLIST_KEY:
            yt.remove_playlist_item(from_item_id, youtube, data, _budget())
            removed = True
    except (yt.QuotaExceededError, yt.QuotaBudgetExhausted) as e:
        return _quota_error_response(e)
    finally:
        st.save(data)

    cache = dc.load()
    moved_card = None
    if removed and from_playlist_id in cache["items"]:
        source_items = cache["items"][from_playlist_id]["items"]
        for i, it in enumerate(source_items):
            if it["playlist_item_id"] == from_item_id:
                moved_card = source_items.pop(i)
                break
        _bump_item_count(cache, from_playlist_id, -1)

    if to_playlist_id in cache["items"]:
        new_card = {
            "playlist_item_id": new_item_id,
            "video_id": video_id,
            "title": (moved_card or {}).get("title") or body.get("title") or video_id,
            "thumbnail": (moved_card or {}).get("thumbnail") or body.get("thumbnail") or "",
            "duration_seconds": (moved_card or {}).get("duration_seconds") or body.get("duration_seconds") or 0,
        }
        cache["items"][to_playlist_id]["items"].insert(0, new_card)
        _bump_item_count(cache, to_playlist_id, 1)
    dc.save(cache)

    return jsonify({"new_item_id": new_item_id, "removed_from_source": removed})


def _bump_item_count(cache: dict, playlist_id: str, delta: int) -> None:
    meta = cache["playlists"].get(playlist_id)
    if meta is not None and isinstance(meta.get("item_count"), int):
        meta["item_count"] = max(meta["item_count"] + delta, 0)


@app.delete("/api/playlists/<playlist_id>/items/<item_id>")
def remove_item(playlist_id, item_id):
    if playlist_id == LIKED_PLAYLIST_KEY:
        return jsonify({"error": "Liked Videos can't be edited via the API."}), 400
    data = st.load()
    youtube = _service()
    try:
        yt.remove_playlist_item(item_id, youtube, data, _budget())
    except (yt.QuotaExceededError, yt.QuotaBudgetExhausted) as e:
        return _quota_error_response(e)
    finally:
        st.save(data)

    cache = dc.load()
    if playlist_id in cache["items"]:
        items = cache["items"][playlist_id]["items"]
        cache["items"][playlist_id]["items"] = [it for it in items if it["playlist_item_id"] != item_id]
        _bump_item_count(cache, playlist_id, -1)
        dc.save(cache)
    return jsonify({"ok": True})


@app.post("/api/playlists/<playlist_id>/visibility")
def set_playlist_visibility(playlist_id):
    """body: {excluded: bool}. A manual choice here always wins over the
    auto-detection in _refresh_items -- it's remembered as manual_override
    so a later refresh won't silently flip it back."""
    if playlist_id == LIKED_PLAYLIST_KEY:
        return jsonify({"error": "Liked Videos is always shown and can't be excluded."}), 400
    cache = dc.load()
    meta = cache["playlists"].get(playlist_id)
    if meta is None:
        return jsonify({"error": "Unknown playlist; refresh the playlist list first."}), 404
    excluded = bool(request.get_json(force=True).get("excluded"))
    meta["manual_override"] = "exclude" if excluded else "include"
    meta["excluded"] = excluded
    dc.save(cache)
    return jsonify({"ok": True, "excluded": excluded})


def main():
    from dotenv import load_dotenv

    load_dotenv()
    app.run(host="127.0.0.1", port=int(os.environ.get("DASHBOARD_PORT", "5000")), debug=False)


if __name__ == "__main__":
    main()
