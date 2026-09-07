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
import time

import yaml

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

BATCH_SIZE = 25
MAX_TOKENS = 8000
INTER_BATCH_DELAY_SECONDS = 1.0

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

    def _call_llm(self, prompt: str) -> str:
        if self.provider == "anthropic":
            resp = self.client.messages.create(
                model=self.model,
                max_tokens=MAX_TOKENS,
                messages=[{"role": "user", "content": prompt}],
            )
            if resp.stop_reason == "max_tokens":
                print(f"Warning: response hit max_tokens ({MAX_TOKENS}) and was truncated.")
            return "".join(block.text for block in resp.content if block.type == "text")

        resp = self.client.chat.completions.create(
            model=self.model,
            max_tokens=MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
        choice = resp.choices[0]
        if choice.finish_reason == "length":
            print(f"Warning: response hit max_tokens ({MAX_TOKENS}) and was truncated.")
        return choice.message.content or ""

    def classify_batch(self, songs: list[dict], retries: int = 2) -> list[dict]:
        """songs: [{video_id, title, channel_title, description}, ...]
        Returns [{video_id, bucket, is_new_bucket}, ...]

        Retries on a malformed/empty LLM response (transient provider hiccups
        are common on batches this size); raises after exhausting retries so
        the caller can decide what to do with this one batch without losing
        any other already-classified batches.
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

        last_err = None
        for attempt in range(retries + 1):
            text = None
            try:
                text = self._call_llm(prompt)
                results = _parse_json_array(text)
                break
            except Exception as e:
                snippet = repr(text[:200]) if text else "<empty>"
                last_err = f"{e} (provider={self.provider}, model={self.model}, raw response: {snippet})"
                if attempt < retries:
                    time.sleep(5 * (attempt + 1))
        else:
            raise RuntimeError(
                f"Classification failed for a batch of {len(songs)} song(s) after "
                f"{retries + 1} attempt(s): {last_err}"
            )

        by_id = {r["video_id"]: r for r in results if "video_id" in r}
        # Fall back to "Other" for anything the model dropped.
        return [
            by_id.get(s["video_id"], {"video_id": s["video_id"], "bucket": "Other", "is_new_bucket": False})
            for s in songs
        ]

    def classify_all(self, songs: list[dict]):
        """Yields (batch_songs, batch_results) per batch, so a caller can save
        progress incrementally. If a batch fails even after retries, yields
        (batch_songs, None) for it and moves on -- those songs stay
        unclassified and get retried on the next run rather than crashing
        (and losing) everything already classified in earlier batches.
        """
        for i in range(0, len(songs), BATCH_SIZE):
            if i > 0:
                time.sleep(INTER_BATCH_DELAY_SECONDS)
            batch = songs[i : i + BATCH_SIZE]
            try:
                yield batch, self.classify_batch(batch)
            except Exception as e:
                print(f"Warning: {e}. These {len(batch)} song(s) will be retried next run.")
                yield batch, None
