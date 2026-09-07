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

2. **LLM provider** (used for song classification) -- pick one:
   - **Anthropic** directly: get an API key from [console.anthropic.com](https://console.anthropic.com).
   - **OpenRouter**: get an API key from [openrouter.ai/keys](https://openrouter.ai/keys). Lets you route to Anthropic, OpenAI, or dozens of other models via `OPENROUTER_MODEL` (default `anthropic/claude-sonnet-5`; see [openrouter.ai/models](https://openrouter.ai/models) for other slugs).

3. Copy `.env.example` to `.env` and fill in the API key for whichever provider you picked (`ANTHROPIC_API_KEY` or `OPENROUTER_API_KEY`). If you fill in both, set `LLM_PROVIDER=anthropic` or `LLM_PROVIDER=openrouter` in `.env` to say which one wins -- otherwise it's auto-detected from whichever key is set.

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
playlist (plus a read-only "Liked Videos" column). Each card shows thumbnail,
title, and duration; drag the ⠿ handle in a column's header to reorder
playlists (a local display preference, doesn't touch YouTube).

**Dragging a song between columns only stages the change** — nothing is sent
to YouTube until you click **💾 Save N changes** in the header, which applies
every staged move in one go (✕ **Discard** clears them instead). Staged cards
get a gold outline; dragging one back to its original column cancels that one
change. This lets you do a whole reorganizing session before spending any
quota, rather than paying per drag.

**📋 on a column** creates a new playlist seeded with a copy of everything
currently in that column (works on Liked Videos too, since it only ever adds
to the new playlist — the source is never touched). Large sources are copied
until the daily quota runs out, then stop cleanly and tell you how much made it in.

Everything you *view* is served from a local cache (`state/dashboard_cache.json`)
— opening the dashboard or scrolling through columns never calls the YouTube
API. Moving/adding/removing/creating updates the cache directly too, so
normal use costs no read quota at all. The API is only called on an explicit
refresh, which you control per scope:
- **🔄 New playlists** — cheap; just checks for playlists created elsewhere since you last opened the dashboard, without re-fetching songs in existing ones.
- **🔄 (per column)** — re-fetches just that one playlist's songs.
- **🔄 Refresh all** — re-fetches every playlist and every column's songs; costs the most quota, asks for confirmation.

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
