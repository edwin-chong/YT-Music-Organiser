# ytorganiser

Pulls songs out of your YouTube **Liked Videos**, classifies them into
genre/mood playlists with Claude, and gives you a local drag-and-drop
dashboard to fine-tune where everything lands.

## Why it works this way

YouTube has no official MCP server, and the YouTube Data API v3 (the official
API this project uses) explicitly blocks writing to system-managed playlists
like **Liked Videos** and **Watch Later** — you can read them, but you can
never reorder, add to, or remove from them. So this tool never touches Liked
Videos itself: it only *reads* it, then creates and manages regular playlists
you fully control.

## Setup

1. **Google Cloud / YouTube Data API v3**
   - Create a project at [console.cloud.google.com](https://console.cloud.google.com), enable the "YouTube Data API v3".
   - OAuth consent screen: choose "External" (Testing mode is fine for personal use), add your own Google account as a test user.
   - Create OAuth credentials of type **Desktop app**, download the JSON, save it as `client_secret.json` in this folder.

2. **Anthropic**
   - Get an API key from [console.anthropic.com](https://console.anthropic.com).

3. Copy `.env.example` to `.env` and fill in `ANTHROPIC_API_KEY`.

4. Install dependencies:
   ```
   pip install -e .
   ```

5. Authenticate once (opens a browser for the Google OAuth consent flow):
   ```
   ytorganiser auth
   ```

## Usage

### Classify & organize liked songs into playlists

```
ytorganiser run --dry-run          # preview groupings, no writes
ytorganiser run                    # actually create playlists / add songs
ytorganiser status                 # see what's been classified/added so far
```

Edit `config/buckets.yaml` to change the genre/mood categories Claude sorts
songs into (it can also propose new bucket names for songs that don't fit).

### Dashboard (view + manually move/add songs between playlists)

```
ytorganiser dashboard
```

Opens `http://127.0.0.1:5000` — a Trello-style board with one column per
playlist (plus a read-only "Liked Videos" column). Drag a song card between
columns to move it; drop onto a new playlist to add it there. Each card shows
thumbnail, title, and duration.

## Quota notes

The YouTube Data API gives you 10,000 units/day by default:

| Operation | Cost |
|---|---|
| Reading (list liked videos, playlists, items, video metadata) | 1 unit/call (≤50 items/page) |
| Creating a playlist | 50 units |
| Adding a song to a playlist | 50 units |
| Removing a song from a playlist | 50 units |
| Moving a song between two playlists (dashboard drag) | 100 units (add + remove) |

So you get roughly **200 adds** or **~100 drag-and-drop moves** per day on the
free tier. Both the CLI pipeline and the dashboard track a running daily
budget (`DAILY_QUOTA_BUDGET` in `.env`, default 9500 to leave headroom) and
stop gracefully with a clear message when it's spent — nothing is lost,
progress is saved in `state/state.json` and picks up automatically next run
or next day. Request a quota increase from Google if you need more.

## MCP server

To drive this from Claude Desktop/Code instead of the CLI, add to your MCP
config:

```json
{
  "mcpServers": {
    "ytorganiser": {
      "command": "ytorganiser-mcp"
    }
  }
}
```

Exposes `organize_liked_videos(dry_run, limit, daily_quota_budget, include_all_categories)`
and `get_status()`.

## Known limitations

- Liked Videos/Liked Songs ordering can't be changed by anyone — official
  API, unofficial API, or browser automation. It's sorted by like-timestamp
  server-side with no manual-order concept.
- If you run the auto-classify pipeline (`ytorganiser run`) *and* manually
  drag a song into one of its managed playlists via the dashboard, the
  pipeline doesn't currently know about that manual add and could add the
  same song again on a later run, creating a duplicate entry in that
  playlist. Remove duplicates by hand if this happens.
