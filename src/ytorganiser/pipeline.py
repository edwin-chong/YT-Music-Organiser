"""Orchestrates: read Liked Videos -> filter to music -> classify -> create
playlists -> add songs. Never writes to the Liked Videos playlist itself
(YouTube doesn't allow that anyway -- see README).

Resumability invariant: a liked video is only ever treated as "seen" (skipped
on future fetches) once it's either fully classified or explicitly ignored
(non-music). That way a run cut short by --limit or a quota error never loses
track of a video -- it just gets re-fetched (cheap: 1 unit/page) next time.
"""
from . import state as st
from . import youtube_client as yt
from .classifier import Classifier


def run(
    *,
    dry_run: bool = False,
    daily_quota_budget: int = 9500,
    include_all_categories: bool = False,
    limit: int | None = None,
    buckets_path: str = "config/buckets.yaml",
):
    data = st.load()
    data.setdefault("ignored", [])
    try:
        from . import auth

        youtube = auth.get_service()
    except Exception as e:
        print(f"Auth failed: {e}")
        return

    try:
        liked_playlist_id = yt.get_liked_playlist_id(youtube, data, daily_quota_budget)

        seen_ids = set(data["classified"]) | set(data["ignored"])
        all_items = list(yt.iter_liked_videos(youtube, liked_playlist_id, data, daily_quota_budget))
        new_items = [v for v in all_items if v["video_id"] not in seen_ids]
        print(f"{len(all_items)} liked video(s) total, {len(new_items)} not yet processed.")
        if not new_items:
            return

        if not include_all_categories:
            cats = yt.enrich_with_categories(youtube, [v["video_id"] for v in new_items], data, daily_quota_budget)
            music_items, non_music_items = [], []
            for v in new_items:
                if cats.get(v["video_id"], {}).get("category_id") == yt.MUSIC_CATEGORY_ID:
                    music_items.append(v)
                else:
                    non_music_items.append(v)
            for v in non_music_items:
                data["ignored"].append(v["video_id"])
            print(f"{len(music_items)} of those are categorized as Music ({len(non_music_items)} ignored).")
            new_items = music_items

        if limit is not None:
            new_items = new_items[:limit]

        to_classify = [v for v in new_items if v["video_id"] not in data["classified"]]
        if to_classify:
            print(f"Classifying {len(to_classify)} song(s)...")
            classifier = Classifier(buckets_path=buckets_path)
            by_id = {v["video_id"]: v for v in to_classify}
            for batch, results in classifier.classify_all(to_classify):
                if results is None:
                    continue  # this batch failed even after retries; try again next run
                for r in results:
                    video_id = r["video_id"]
                    data["classified"][video_id] = {
                        "bucket": r["bucket"],
                        "title": by_id[video_id]["title"],
                    }
                st.save(data)  # persist each batch as it lands

        # Group all classified-but-not-yet-added videos by bucket (this can
        # include videos left over from a previous run that was cut short).
        pending_by_bucket: dict[str, list[str]] = {}
        for video_id, info in data["classified"].items():
            if _playlist_has(data, video_id, info["bucket"]):
                continue
            pending_by_bucket.setdefault(info["bucket"], []).append(video_id)

        if not pending_by_bucket:
            print("Nothing new to add to playlists.")
            return

        for bucket, video_ids in pending_by_bucket.items():
            playlist_id = data["playlists"].get(bucket)
            if playlist_id is None:
                if dry_run:
                    print(f"[dry-run] Would create playlist '{bucket}'")
                else:
                    playlist_id = yt.create_playlist(
                        youtube, bucket, "Auto-created from Liked Videos by ytorganiser.", data, daily_quota_budget
                    )
                    data["playlists"][bucket] = playlist_id
                    st.save(data)
                    print(f"Created playlist '{bucket}'")

            for video_id in video_ids:
                title = data["classified"][video_id]["title"]
                if dry_run:
                    print(f"[dry-run] Would add '{title}' -> '{bucket}'")
                    continue
                if playlist_id is None:
                    continue  # dry-run: no real playlist was created to add to
                yt.add_video_to_playlist(youtube, playlist_id, video_id, data, daily_quota_budget)
                data["added"].setdefault(video_id, []).append(playlist_id)
                st.save(data)
                print(f"Added '{title}' -> '{bucket}'")

    except yt.QuotaBudgetExhausted as e:
        print(str(e))
    except yt.QuotaExceededError as e:
        print(str(e))
    finally:
        st.save(data)


def _playlist_has(data: dict, video_id: str, bucket: str) -> bool:
    playlist_id = data["playlists"].get(bucket)
    if playlist_id is None:
        return False
    return playlist_id in data["added"].get(video_id, [])
