"""Thin wrapper around the YouTube Data API v3 calls this project needs.

Quota costs (as documented by Google, subject to change):
  playlistItems.list / videos.list / channels.list / playlists.list -> 1 unit
  playlists.insert / playlistItems.insert / playlistItems.delete    -> 50 units
"""
import re

from googleapiclient.errors import HttpError

from . import state as st

COST_READ = 1
COST_WRITE = 50

MUSIC_CATEGORY_ID = "10"

_ISO8601_DURATION_RE = re.compile(
    r"P(?:\d+D)?T(?:(?P<h>\d+)H)?(?:(?P<m>\d+)M)?(?:(?P<s>\d+)S)?"
)


def parse_duration_seconds(iso_duration: str) -> int:
    """'PT4M13S' -> 253. Returns 0 if unparseable (e.g. live streams: 'P0D')."""
    match = _ISO8601_DURATION_RE.match(iso_duration or "")
    if not match:
        return 0
    h = int(match.group("h") or 0)
    m = int(match.group("m") or 0)
    s = int(match.group("s") or 0)
    return h * 3600 + m * 60 + s


class QuotaExceededError(RuntimeError):
    pass


class QuotaBudgetExhausted(RuntimeError):
    """Raised locally when our own daily_quota_budget (not Google's) is spent."""


def _charge(data: dict, units: int, daily_budget: int) -> None:
    if st.quota_remaining(data, daily_budget) < units:
        raise QuotaBudgetExhausted(
            f"Daily quota budget ({daily_budget} units) would be exceeded; stopping "
            "for now. Re-run later (state is saved, this is resumable)."
        )
    st.spend_quota(data, units, daily_budget)


def _call(request):
    try:
        return request.execute()
    except HttpError as e:
        reason = ""
        try:
            reason = e.error_details[0].get("reason", "") if e.error_details else ""
        except Exception:
            pass
        if e.resp is not None and e.resp.status == 403 and (
            "quotaExceeded" in str(e) or reason == "quotaExceeded"
        ):
            raise QuotaExceededError(
                "YouTube API returned quotaExceeded. Your Google Cloud project's "
                "daily quota (10,000 units by default) is used up; try again after "
                "midnight Pacific Time or request a quota increase."
            ) from e
        raise


def get_liked_playlist_id(youtube, data: dict, daily_budget: int) -> str:
    _charge(data, COST_READ, daily_budget)
    resp = _call(youtube.channels().list(part="contentDetails", mine=True))
    return resp["items"][0]["contentDetails"]["relatedPlaylists"]["likes"]


def iter_liked_videos(youtube, liked_playlist_id: str, data: dict, daily_budget: int,
                       stop_at_ids: set[str] | None = None):
    """Yields liked-video items, most recently liked first. Stops early once it
    reaches a video_id in stop_at_ids (already-seen videos from a prior run),
    since the Liked Videos playlist is ordered by like date descending.
    """
    stop_at_ids = stop_at_ids or set()
    page_token = None
    while True:
        _charge(data, COST_READ, daily_budget)
        resp = _call(
            youtube.playlistItems().list(
                part="snippet,contentDetails",
                playlistId=liked_playlist_id,
                maxResults=50,
                pageToken=page_token,
            )
        )
        for item in resp.get("items", []):
            video_id = item["contentDetails"]["videoId"]
            if video_id in stop_at_ids:
                return
            snippet = item["snippet"]
            yield {
                "video_id": video_id,
                "title": snippet.get("title", ""),
                "channel_title": snippet.get("videoOwnerChannelTitle", ""),
                "description": snippet.get("description", ""),
            }
        page_token = resp.get("nextPageToken")
        if not page_token:
            return


def enrich_with_categories(youtube, video_ids: list[str], data: dict, daily_budget: int) -> dict:
    """Batches videos.list (up to 50 ids/call) -> {video_id: {category_id, tags}}."""
    out = {}
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i : i + 50]
        _charge(data, COST_READ, daily_budget)
        resp = _call(youtube.videos().list(part="snippet", id=",".join(batch)))
        for item in resp.get("items", []):
            snippet = item["snippet"]
            out[item["id"]] = {
                "category_id": snippet.get("categoryId"),
                "tags": snippet.get("tags", []),
            }
    return out


def list_my_playlists(youtube, data: dict, daily_budget: int) -> dict:
    """Returns {title: playlist_id} for the authenticated user's playlists."""
    out = {}
    page_token = None
    while True:
        _charge(data, COST_READ, daily_budget)
        resp = _call(
            youtube.playlists().list(
                part="snippet", mine=True, maxResults=50, pageToken=page_token
            )
        )
        for item in resp.get("items", []):
            out[item["snippet"]["title"]] = item["id"]
        page_token = resp.get("nextPageToken")
        if not page_token:
            return out


def create_playlist(youtube, title: str, description: str, data: dict, daily_budget: int) -> str:
    _charge(data, COST_WRITE, daily_budget)
    resp = _call(
        youtube.playlists().insert(
            part="snippet,status",
            body={
                "snippet": {"title": title, "description": description},
                "status": {"privacyStatus": "private"},
            },
        )
    )
    return resp["id"]


def add_video_to_playlist(youtube, playlist_id: str, video_id: str, data: dict, daily_budget: int) -> str:
    """Returns the new playlistItem id (needed later to remove/move it)."""
    _charge(data, COST_WRITE, daily_budget)
    resp = _call(
        youtube.playlistItems().insert(
            part="snippet",
            body={
                "snippet": {
                    "playlistId": playlist_id,
                    "resourceId": {"kind": "youtube#video", "videoId": video_id},
                }
            },
        )
    )
    return resp["id"]


def remove_playlist_item(playlist_item_id: str, youtube, data: dict, daily_budget: int) -> None:
    _charge(data, COST_WRITE, daily_budget)
    _call(youtube.playlistItems().delete(id=playlist_item_id))


def list_playlists_full(youtube, data: dict, daily_budget: int) -> list[dict]:
    """[{playlist_id, title, thumbnail, item_count}, ...] for the user's own playlists."""
    out = []
    page_token = None
    while True:
        _charge(data, COST_READ, daily_budget)
        resp = _call(
            youtube.playlists().list(
                part="snippet,contentDetails", mine=True, maxResults=50, pageToken=page_token
            )
        )
        for item in resp.get("items", []):
            thumbs = item["snippet"].get("thumbnails", {})
            thumb = (thumbs.get("medium") or thumbs.get("default") or {}).get("url", "")
            out.append(
                {
                    "playlist_id": item["id"],
                    "title": item["snippet"]["title"],
                    "thumbnail": thumb,
                    "item_count": item["contentDetails"]["itemCount"],
                }
            )
        page_token = resp.get("nextPageToken")
        if not page_token:
            return out


def list_playlist_items_full(youtube, playlist_id: str, data: dict, daily_budget: int) -> list[dict]:
    """[{playlist_item_id, video_id, title, thumbnail, duration_seconds}, ...]
    in playlist order. playlist_item_id is required to remove/move an item.
    """
    items = []
    page_token = None
    while True:
        _charge(data, COST_READ, daily_budget)
        resp = _call(
            youtube.playlistItems().list(
                part="snippet,contentDetails",
                playlistId=playlist_id,
                maxResults=50,
                pageToken=page_token,
            )
        )
        for item in resp.get("items", []):
            snippet = item["snippet"]
            thumbs = snippet.get("thumbnails", {})
            thumb = (thumbs.get("medium") or thumbs.get("default") or {}).get("url", "")
            items.append(
                {
                    "playlist_item_id": item["id"],
                    "video_id": item["contentDetails"]["videoId"],
                    "title": snippet.get("title", ""),
                    "thumbnail": thumb,
                }
            )
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    durations = get_video_durations(youtube, [i["video_id"] for i in items], data, daily_budget)
    for item in items:
        item["duration_seconds"] = durations.get(item["video_id"], 0)
    return items


def get_video_durations(youtube, video_ids: list[str], data: dict, daily_budget: int) -> dict:
    """{video_id: duration_seconds}"""
    out = {}
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i : i + 50]
        if not batch:
            continue
        _charge(data, COST_READ, daily_budget)
        resp = _call(youtube.videos().list(part="contentDetails", id=",".join(batch)))
        for item in resp.get("items", []):
            out[item["id"]] = parse_duration_seconds(item["contentDetails"]["duration"])
    return out
