"""Chat-completion backends for PV generation: the Mistral chat API or the local Claude CLI.

Adapted from dnd_logger@bc2bdf1 src/summarizer.py: the backends and the code-fence
stripping are kept; the D&D prompts and the single-shot summary worker are replaced by the
PV pipeline (``pv_pipeline.py``). A ``json_mode`` flag is added for the extraction stage,
and the Mistral backend paces its calls with ``rate_limit`` instead of only retrying on 429.
"""

import logging
import re

from . import claude_cli
from .i18n import tr
from .mistral_compat import mistral_class
from .rate_limit import backoff_delay, chat_limiter, estimate_tokens, is_rate_limit_error

log = logging.getLogger("pvwriter.summarizer")

PROVIDER_MISTRAL = "mistral"
PROVIDER_CLAUDE_CLI = "claude_cli"


def strip_code_fences(text: str) -> str:
    """Remove code fences and formatting artifacts that models sometimes add."""
    text = text.strip()
    text = re.sub(r"^```(?:html|json)?\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)
    # Strip markdown horizontal rules
    text = re.sub(r"\n---\n", "\n", text)
    text = re.sub(r"\n---$", "", text)
    text = re.sub(r"^---\n", "", text)
    return text.strip()


def markdown_emphasis_to_html(text: str) -> str:
    """Convert stray markdown bold/italic to HTML tags (models sometimes mix both)."""
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<em>\1</em>", text)
    return text


RATE_LIMIT_RETRIES = 5


class _MistralBackend:
    """Chat completions through the Mistral API."""

    name = PROVIDER_MISTRAL

    def __init__(self, api_key: str, model: str, config: dict | None = None):
        mistral_cls = mistral_class()
        self._client = mistral_cls(api_key=api_key)
        self._model = model
        self._limiter = chat_limiter(config or {}, model)
        self.on_wait = None  # optional callable(seconds) to report rate-limit waits

    @property
    def tokens_per_minute(self) -> int:
        """Token budget per minute of the model (0 = unlimited)."""
        return self._limiter.tokens_per_minute

    def complete(
        self, system_prompt: str, user_prompt: str, temperature: float, max_tokens: int, json_mode: bool = False
    ) -> str:
        """Return the model's answer for the given prompts."""
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_prompt})
        kwargs = dict(model=self._model, messages=messages, temperature=temperature, max_tokens=max_tokens)
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        estimate = estimate_tokens(system_prompt, user_prompt) + max_tokens
        for attempt in range(RATE_LIMIT_RETRIES):
            reservation = self._limiter.acquire(estimate, self.on_wait)
            try:
                response = self._client.chat.complete(**kwargs)
            except Exception as e:
                if is_rate_limit_error(e) and attempt < RATE_LIMIT_RETRIES - 1:
                    delay = backoff_delay(e, attempt)
                    log.warning("Mistral 429 on %s, retrying in %.0f s", self._model, delay)
                    self._limiter.penalize(delay)
                    continue
                raise
            usage = getattr(response, "usage", None)
            self._limiter.record(reservation, getattr(usage, "total_tokens", None))
            return response.choices[0].message.content
        return ""

    def close(self):
        """Release the HTTP connection pool."""
        try:
            self._client.__exit__(None, None, None)
        except Exception:
            pass


class _ClaudeCliBackend:
    """Chat completions through a locally installed Claude CLI."""

    name = PROVIDER_CLAUDE_CLI
    tokens_per_minute = 0  # no client-side limit

    def __init__(self, model: str):
        self._model = model
        self.on_wait = None

    def complete(
        self, system_prompt: str, user_prompt: str, temperature: float, max_tokens: int, json_mode: bool = False
    ) -> str:
        """Return the model's answer for the given prompts.

        The CLI exposes no temperature, token-budget or JSON-mode knobs, so those
        arguments are accepted for interface parity and ignored.
        """
        return claude_cli.complete(user_prompt, system_prompt=system_prompt, model=self._model)

    def close(self):
        """Nothing to release."""


def build_backend(config: dict):
    """Create the generation backend selected in config.

    Falls back to Mistral when the Claude CLI is selected but not installed.

    Raises:
        RuntimeError: If no backend can be built (e.g. no Mistral API key).
    """
    provider = config.get("summary_provider", PROVIDER_MISTRAL)
    if provider == PROVIDER_CLAUDE_CLI:
        if claude_cli.is_available(refresh=True):
            return _ClaudeCliBackend(config.get("claude_model", ""))
        log.warning("Claude CLI not found, falling back to the Mistral API")

    api_key = config.get("api_key", "")
    if not api_key:
        raise RuntimeError(tr("summarizer.error.no_api_key"))
    return _MistralBackend(api_key, config.get("summary_model", "mistral-large-latest"), config)
