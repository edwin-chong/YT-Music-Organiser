import os

import click
from dotenv import load_dotenv

load_dotenv()


@click.group()
def main():
    """Organise your YouTube Liked Videos into playlists."""


@main.command()
def auth():
    """Run the one-time Google OAuth flow."""
    from . import auth as auth_mod

    auth_mod.get_credentials()
    click.echo("Authenticated. Token saved.")


@main.command()
@click.option("--dry-run", is_flag=True, help="Preview groupings without writing to YouTube.")
@click.option(
    "--daily-quota-budget",
    default=lambda: int(os.environ.get("DAILY_QUOTA_BUDGET", "9500")),
    show_default="DAILY_QUOTA_BUDGET env var, else 9500",
    type=int,
)
@click.option("--include-all-categories", is_flag=True, help="Skip the Music-category prefilter.")
@click.option("--limit", default=None, type=int, help="Only process the N most recently liked videos.")
@click.option("--buckets", "buckets_path", default="config/buckets.yaml", show_default=True)
def run(dry_run, daily_quota_budget, include_all_categories, limit, buckets_path):
    """Classify new liked songs and add them to playlists."""
    from . import pipeline

    pipeline.run(
        dry_run=dry_run,
        daily_quota_budget=daily_quota_budget,
        include_all_categories=include_all_categories,
        limit=limit,
        buckets_path=buckets_path,
    )


@main.command()
def status():
    """Show cached classification/quota state."""
    from . import state as st

    data = st.load()
    click.echo(f"Classified videos: {len(data['classified'])}")
    click.echo(f"Playlists created: {len(data['playlists'])}")
    for bucket, pid in data["playlists"].items():
        n = sum(1 for v in data["added"].values() if pid in v)
        click.echo(f"  - {bucket}: {n} song(s) added (playlist {pid})")
    click.echo(f"Quota used today: {data['quota']}")


@main.command()
@click.option("--port", default=5000, show_default=True, type=int)
def dashboard(port):
    """Launch the local drag-and-drop playlist dashboard."""
    import os
    import webbrowser

    os.environ["DASHBOARD_PORT"] = str(port)
    webbrowser.open(f"http://127.0.0.1:{port}")
    from . import webapp

    webapp.app.run(host="127.0.0.1", port=port, debug=False)


if __name__ == "__main__":
    main()
