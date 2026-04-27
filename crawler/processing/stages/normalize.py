"""NormalizeStage — RawMention → NormalizedMention conversion.

Implements algorithm D from core/CLAUDE.md exactly (6 steps).
Section B.1 of processing/CLAUDE.md.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import TYPE_CHECKING
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from crawler.core.models import NormalizedMention, RawMention

if TYPE_CHECKING:
    from crawler.processing.context import PipelineContext

# ---------------------------------------------------------------------------
# Tracking params to strip from inline URLs (D.2 from core/CLAUDE.md)
# ---------------------------------------------------------------------------

_TRACKING_PARAMS: frozenset[str] = frozenset({
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "fbclid",
    "gclid",
    "mc_eid",
    "mc_cid",
    "igshid",
    "_hsenc",
    "_hsmi",
    "ref",
    "ref_src",
    "ref_url",
    "vero_id",
    "yclid",
    "msclkid",
    "twclid",
})

_URL_RE = re.compile(r"https?://\S+")
_WS_RE = re.compile(r"\s+")


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _extract_text(mention: RawMention) -> tuple[str, bool]:
    """
    Step 1: extract text from HTML or plain text.
    Returns: (raw_text, is_html_stripped)
    """
    if mention.text_html:
        from selectolax.parser import HTMLParser

        tree = HTMLParser(mention.text_html)
        # Remove script/style/noscript and their content entirely
        for tag in tree.css("script, style, noscript"):
            tag.decompose()
        body = tree.body
        text = body.text(separator=" ") if body else ""
        return text, True
    return mention.text, False


def _strip_tracking_params(text: str) -> tuple[str, list[str]]:
    """
    Step 2: strip tracking parameters from inline URLs.
    Returns: (cleaned_text, list_of_removed_param_names)
    """
    removed_all: list[str] = []

    def replace_url(match: re.Match[str]) -> str:
        url = match.group(0)
        try:
            parts = urlsplit(url)
            params = parse_qsl(parts.query, keep_blank_values=True)
            kept: list[tuple[str, str]] = []
            removed: list[str] = []
            for k, v in params:
                if k.lower() in _TRACKING_PARAMS:
                    removed.append(k.lower())
                else:
                    kept.append((k, v))
            removed_all.extend(removed)
            new_query = urlencode(kept)
            return urlunsplit(parts._replace(query=new_query))
        except Exception:
            return url

    cleaned = _URL_RE.sub(replace_url, text)
    return cleaned, removed_all


def _compute_content_hash(text_clean: str) -> str:
    """Step 6: SHA-256 hex of normalized text."""
    return hashlib.sha256(text_clean.encode("utf-8")).hexdigest()


def _detect_lang(text_clean: str) -> str:
    """
    Detect language via langdetect.
    Falls back to "und" (ISO 639-3 undefined) on error or short text.
    """
    if len(text_clean.strip()) < 20:
        return "und"
    try:
        from langdetect import detect

        return detect(text_clean)
    except Exception:
        return "und"


# ---------------------------------------------------------------------------
# NormalizeStage
# ---------------------------------------------------------------------------


class NormalizeStage:
    """
    Bridge between RawMention and NormalizedMention.

    Implements exactly algorithm D from core/CLAUDE.md (6 steps):
      1. Extract text (selectolax HTML strip if text_html present)
      2. Strip tracking params from inline URLs
      3. NFKC unicode normalization
      4. Lowercase
      5. Collapse whitespace + strip
      6. SHA-256 content_hash

    Does NOT filter — output length equals input length.
    """

    name: str = "normalize"

    async def process(
        self,
        mentions: list[NormalizedMention],  # accepts RawMention via duck-typing
        ctx: PipelineContext,
    ) -> list[NormalizedMention]:
        """
        Convert RawMention list to NormalizedMention list.

        The type hint says list[NormalizedMention] for IStage compatibility,
        but the first stage receives list[RawMention] from Pipeline.run().
        This is safe because NormalizedMention is a subclass of RawMention —
        we handle both via duck-typing.
        """
        result: list[NormalizedMention] = []

        for mention in mentions:  # type: ignore[assignment]
            raw_text, is_html_stripped = _extract_text(mention)  # type: ignore[arg-type]

            # Step 2: strip tracking params
            text_stripped, tracking_params_removed = _strip_tracking_params(raw_text)

            # Step 3: NFKC
            text_nfkc = unicodedata.normalize("NFKC", text_stripped)

            # Step 4: lowercase
            text_lower = text_nfkc.lower()

            # Step 5: collapse whitespace
            text_clean = _WS_RE.sub(" ", text_lower).strip()

            # Step 6: content_hash
            content_hash = _compute_content_hash(text_clean)

            # Language detection
            lang = _detect_lang(text_clean)

            # Build NormalizedMention from RawMention fields + new computed fields.
            # We extract only the RawMention-level fields (not NormalizedMention extras)
            # to avoid duplicate-key errors when re-normalizing an already-normalized mention.
            raw_data = {
                "source_id": mention.source_id,  # type: ignore[union-attr]
                "external_id": mention.external_id,  # type: ignore[union-attr]
                "author": mention.author,  # type: ignore[union-attr]
                "author_id": mention.author_id,  # type: ignore[union-attr]
                "text": mention.text,  # type: ignore[union-attr]
                "text_html": mention.text_html,  # type: ignore[union-attr]
                "url": mention.url,  # type: ignore[union-attr]
                "lang_hint": mention.lang_hint,  # type: ignore[union-attr]
                "engagement": mention.engagement,  # type: ignore[union-attr]
                "raw": mention.raw,  # type: ignore[union-attr]
                "published_at": mention.published_at,  # type: ignore[union-attr]
                "discovered_at": mention.discovered_at,  # type: ignore[union-attr]
                "fetched_at": mention.fetched_at,  # type: ignore[union-attr]
            }

            normalized = NormalizedMention(
                **raw_data,
                text_clean=text_clean,
                lang=lang,
                content_hash=content_hash,
                is_html_stripped=is_html_stripped,
                normalize_version=1,
                tracking_params_removed=tracking_params_removed,
                # minhash_signature=None — Phase 1+
                # embedding=None — E2a
            )
            result.append(normalized)

        # Populate ctx.all_normalized (used by api_core/scanning.py)
        ctx.all_normalized.extend(result)
        return result
