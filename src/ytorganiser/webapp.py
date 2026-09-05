"""Local dashboard: visualize your playlists (incl. read-only Liked Videos)
and drag-and-drop songs between them. Runs entirely on localhost.
"""
import os

from flask import Flask, jsonify, request, send_from_directory

from . import auth
from . import state as st
from . import youtube_client as yt

LIKED_PLAYLIST_KEY = "LIKED_VIDEOS"  # synthetic id used in the UI only

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


@app.get("/")
def index():
    return send_from_directory(_static_dir, "index.html")


@app.get("/api/quota")
def quota():
    data = st.load()
    budget = _budget()
    return jsonify(
        {
            "budget": budget,
            "remaining": st.quota_remaining(data, budget),
        }
    )


@app.get("/api/playlists")
def list_playlists():
    data = st.load()
    youtube = _service()
    try:
        playlists = yt.list_playlists_full(youtube, data, _budget())
        liked_id = yt.get_liked_playlist_id(youtube, data, _budget())
    except (yt.QuotaExceededError, yt.QuotaBudgetExhausted) as e:
        return jsonify({"error": str(e)}), 429
    finally:
        st.save(data)

    playlists.insert(
        0,
        {
            "playlist_id": LIKED_PLAYLIST_KEY,
            "real_id": liked_id,
            "title": "Liked Videos",
            "thumbnail": "",
            "item_count": None,
            "read_only": True,
        },
    )
    return jsonify(playlists)


@app.get("/api/playlists/<playlist_id>/items")
def list_items(playlist_id):
    data = st.load()
    youtube = _service()
    real_id = playlist_id
    if playlist_id == LIKED_PLAYLIST_KEY:
        try:
            real_id = yt.get_liked_playlist_id(youtube, data, _budget())
        except (yt.QuotaExceededError, yt.QuotaBudgetExhausted) as e:
            st.save(data)
            return jsonify({"error": str(e)}), 429
    try:
        items = yt.list_playlist_items_full(youtube, real_id, data, _budget())
    except (yt.QuotaExceededError, yt.QuotaBudgetExhausted) as e:
        return jsonify({"error": str(e)}), 429
    finally:
        st.save(data)
    return jsonify(items)


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
        return jsonify({"error": str(e)}), 429
    finally:
        st.save(data)
    return jsonify({"playlist_id": playlist_id, "title": title})


@app.post("/api/move")
def move_item():
    """body: {video_id, to_playlist_id, from_playlist_id?, from_item_id?}
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
        return jsonify({"error": str(e)}), 429
    finally:
        st.save(data)
    return jsonify({"new_item_id": new_item_id, "removed_from_source": removed})


@app.delete("/api/playlists/<playlist_id>/items/<item_id>")
def remove_item(playlist_id, item_id):
    if playlist_id == LIKED_PLAYLIST_KEY:
        return jsonify({"error": "Liked Videos can't be edited via the API."}), 400
    data = st.load()
    youtube = _service()
    try:
        yt.remove_playlist_item(item_id, youtube, data, _budget())
    except (yt.QuotaExceededError, yt.QuotaBudgetExhausted) as e:
        return jsonify({"error": str(e)}), 429
    finally:
        st.save(data)
    return jsonify({"ok": True})


def main():
    from dotenv import load_dotenv

    load_dotenv()
    app.run(host="127.0.0.1", port=int(os.environ.get("DASHBOARD_PORT", "5000")), debug=False)


if __name__ == "__main__":
    main()
