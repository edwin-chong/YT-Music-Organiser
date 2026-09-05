"""MCP server exposing the liked-videos-organiser pipeline as tools, so it can
be driven conversationally from Claude Desktop/Code instead of the CLI.
"""
import io
from contextlib import redirect_stdout

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

load_dotenv()
mcp = FastMCP("ytorganiser")


@mcp.tool()
def organize_liked_videos(
    dry_run: bool = True,
    limit: int | None = None,
    daily_quota_budget: int = 9500,
    include_all_categories: bool = False,
) -> str:
    """Fetch new Liked Videos, classify songs into genre/mood playlists with
    Claude, and add them (or just preview, if dry_run=True). Never modifies
    the Liked Videos playlist itself -- YouTube doesn't allow that.
    """
    from . import pipeline

    buf = io.StringIO()
    with redirect_stdout(buf):
        pipeline.run(
            dry_run=dry_run,
            daily_quota_budget=daily_quota_budget,
            include_all_categories=include_all_categories,
            limit=limit,
        )
    return buf.getvalue() or "Done (no output)."


@mcp.tool()
def get_status() -> dict:
    """Return current classification/playlist/quota state."""
    from . import state as st

    data = st.load()
    return {
        "classified_count": len(data["classified"]),
        "playlists": data["playlists"],
        "added_counts": {
            bucket: sum(1 for v in data["added"].values() if pid in v)
            for bucket, pid in data["playlists"].items()
        },
        "quota": data["quota"],
    }


def main():
    mcp.run()


if __name__ == "__main__":
    main()
