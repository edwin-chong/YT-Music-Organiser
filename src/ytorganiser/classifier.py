"""LLM-based classification of liked songs into genre/mood buckets.

Supports two interchangeable providers, picked via LLM_PROVIDER=anthropic|
openrouter in .env (or auto-detected from whichever API key is set, if
LLM_PROVIDER is left blank):
  - anthropic:  calls Anthropic directly.
  - openrouter: calls OpenRouter's OpenAI-compatible chat completions
                endpoint, which can also route to Anthropic models (and many
                others) via OPENROUTER_MODEL.
"""
import json
import os

import yaml

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

BATCH_SIZE = 40

_PROMPT_TEMPLATE = """You are sorting a person's liked songs into playlists.

Candidate buckets (use these names verbatim when they fit):
{buckets}

{new_bucket_instructions}

For each song below, pick the single best-fitting bucket. Judge from the title,
channel/artist name, and description snippet only.

Songs (JSON array of {{"video_id", "title", "channel_title", "description"}}):
{songs}

Respond with ONLY a JSON array, no other text, no markdown fences:
[{{"video_id": "...", "bucket": "...", "is_new_bucket": true|false}}, ...]
"""


def _load_bucket_config(path: str) -> tuple[list[str], bool]:
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    names = [b["name"] if isinstance(b, dict) else b for b in cfg.get("buckets", [])]
    allow_new = bool(cfg.get("allow_new_buckets", True))
    return names, allow_new


def _parse_json_array(text: str) -> list[dict]:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
    return json.loads(text)


def _resolve_provider() -> str:
    provider = os.environ.get("LLM_PROVIDER", "").strip().lower()
    if provider in ("anthropic", "openrouter"):
        return provider
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.environ.get("OPENROUTER_API_KEY"):
        return "openrouter"
    raise RuntimeError(
        "No LLM provider configured. Set ANTHROPIC_API_KEY (to use Anthropic "
        "directly) or OPENROUTER_API_KEY (to use OpenRouter) in .env -- see "
        ".env.example. Set LLM_PROVIDER=anthropic|openrouter to be explicit "
        "if you have both keys set."
    )


class Classifier:
    def __init__(self, buckets_path: str = "config/buckets.yaml", model: str | None = None):
        self.bucket_names, self.allow_new = _load_bucket_config(buckets_path)
        self.provider = _resolve_provider()

        if self.provider == "anthropic":
            import anthropic

            self.model = model or os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")
            workspace_id = os.environ.get("ANTHROPIC_WORKSPACE_ID")
            extra_headers = {"anthropic-workspace-id": workspace_id} if workspace_id else None
            self.client = anthropic.Anthropic(default_headers=extra_headers)
        else:
            import openai

            self.model = model or os.environ.get("OPENROUTER_MODEL", "anthropic/claude-sonnet-5")
            self.client = openai.OpenAI(
                base_url=OPENROUTER_BASE_URL,
                api_key=os.environ["OPENROUTER_API_KEY"],
                default_headers={
                    "HTTP-Referer": "https://github.com/",
                    "X-Title": "ytorganiser",
                },
            )

    def classify_batch(self, songs: list[dict]) -> list[dict]:
        """songs: [{video_id, title, channel_title, description}, ...]
        Returns [{video_id, bucket, is_new_bucket}, ...]
        """
        new_bucket_instructions = (
            'If a song clearly does not fit any bucket, propose a short new bucket '
            'name and set "is_new_bucket": true.'
            if self.allow_new
            else "You must use one of the buckets listed above for every song."
        )
        prompt = _PROMPT_TEMPLATE.format(
            buckets="\n".join(f"- {b}" for b in self.bucket_names),
            new_bucket_instructions=new_bucket_instructions,
            songs=json.dumps(
                [
                    {
                        "video_id": s["video_id"],
                        "title": s["title"],
                        "channel_title": s.get("channel_title", ""),
                        "description": (s.get("description") or "")[:200],
                    }
                    for s in songs
                ],
                ensure_ascii=False,
            ),
        )

        if self.provider == "anthropic":
            resp = self.client.messages.create(
                model=self.model,
                max_tokens=4096,
                messages=[{"role": "user", "content": prompt}],
            )
            text = "".join(block.text for block in resp.content if block.type == "text")
        else:
            resp = self.client.chat.completions.create(
                model=self.model,
                max_tokens=4096,
                messages=[{"role": "user", "content": prompt}],
            )
            text = resp.choices[0].message.content

        results = _parse_json_array(text)
        by_id = {r["video_id"]: r for r in results if "video_id" in r}
        # Fall back to "Other" for anything the model dropped.
        return [
            by_id.get(s["video_id"], {"video_id": s["video_id"], "bucket": "Other", "is_new_bucket": False})
            for s in songs
        ]

    def classify_all(self, songs: list[dict]) -> list[dict]:
        out = []
        for i in range(0, len(songs), BATCH_SIZE):
            out.extend(self.classify_batch(songs[i : i + BATCH_SIZE]))
        return out
