"""
get_company_news_history.py

Returns recent news summaries for a company from the company_news_summaries
Firestore collection. Used by Layer 3 (Signal Freshness) to assess whether the
announcement topic has prior coverage and whether the market may have been
anticipating this category of news.

The company_news_summaries collection is new and starts empty. During early PoC
operation, results will frequently be sparse or empty. This is expected — the
tool returns clear data_maturity and collection_age_days fields so the caller
can reason transparently about data availability.

Execution: backend or frontend (Firestore read, no IP sensitivity).

Usage:
    from utilities.get_company_news_history import get_company_news_history
    result = get_company_news_history(
        ticker="FGEN",
        days_lookback=90,
        exclude_id="rns_article_id_of_current_article",
        current_topics=["regulatory approval", "planning permission"],
    )
"""

import os
from datetime import datetime, date, timezone, timedelta
from typing import Optional

from dotenv import load_dotenv

_UTILITIES_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_UTILITIES_DIR)
load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))

# Credentials fallback to local frontend credentials if VM path unavailable
_creds_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
if not _creds_path or not os.path.exists(_creds_path):
    _frontend_creds = os.path.join(_PROJECT_ROOT, "frontend", "gcp-credentials.json")
    if os.path.exists(_frontend_creds):
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = _frontend_creds

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_COLLECTION = "company_news_summaries"

_DEFAULT_DAYS_LOOKBACK = 90

# data_maturity thresholds
_SUFFICIENT_THRESHOLD = 3

# sentiment_trend: minimum articles for a non-"insufficient_data" classification
_MIN_ARTICLES_FOR_TREND = 3

# Majority threshold for "improving" / "deteriorating" classification
_MAJORITY_THRESHOLD = 0.5


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_company_news_history(
    ticker: str,
    days_lookback: int = _DEFAULT_DAYS_LOOKBACK,
    exclude_id: str = None,
    current_topics: list = None,
    db=None,
) -> dict:
    """
    Return recent news summaries for a company from company_news_summaries.

    Parameters
    ----------
    ticker : str
        LSE ticker (e.g. "FGEN", "LLOY").
    days_lookback : int, optional
        Number of days of history to return. Default 90.
    exclude_id : str, optional
        rns_article_id of the current article being investigated.
        Excluded from results to prevent the current article appearing in
        its own history context.
    current_topics : list, optional
        key_topics from the current article. Used to compute topic_match —
        whether any of these topics have appeared in prior articles.
        If None, topic_match is returned as None (cannot be determined).
    db : Firestore client, optional
        Injected for testing. Created automatically if None.

    Returns
    -------
    dict with keys:
        ticker              str
        days_lookback       int
        articles            list    sorted by published_at descending
                                    each: {rns_article_id, headline,
                                           published_at, article_subtype,
                                           sentiment, summary, key_topics}
        article_count       int
        topics_seen         list    deduplicated union of all key_topics
                                    across all returned articles
        topic_match         bool | None
                                    True if any topic in current_topics
                                    appears in topics_seen.
                                    None if current_topics not provided.
        sentiment_trend     str     "improving" | "deteriorating" |
                                    "stable" | "mixed" |
                                    "insufficient_data"
        data_maturity       str     "sufficient" | "sparse" | "empty"
        collection_age_days int | None  days since oldest article in the
                                    company_news_summaries collection
    """
    if db is None:
        from google.cloud import firestore
        db = firestore.Client()

    cutoff = datetime.now(timezone.utc) - timedelta(days=days_lookback)

    # --- Fetch all docs for this ticker ---
    docs = list(
        db.collection(_COLLECTION)
        .where("ticker", "==", ticker)
        .stream()
    )

    # --- Filter, exclude, and parse ---
    articles = []
    for doc in docs:
        data = doc.to_dict()
        data["_doc_id"] = doc.id

        if exclude_id and data.get("rns_article_id") == exclude_id:
            continue

        pub_at = _parse_published_at(data.get("published_at"))
        if pub_at is None:
            continue  # unparseable date — skip

        if pub_at < cutoff:
            continue  # outside lookback window

        articles.append({
            "rns_article_id":  data.get("rns_article_id"),
            "headline":        data.get("headline"),  # optional field, None if absent
            "published_at":    pub_at,
            "article_subtype": data.get("article_subtype"),
            "sentiment":       data.get("sentiment"),
            "summary":         data.get("summary"),
            "key_topics":      data.get("key_topics") or [],
        })

    # Sort descending (most recent first)
    articles.sort(key=lambda a: a["published_at"], reverse=True)

    # --- Aggregate fields ---
    topics_seen = _compute_topics_seen(articles)
    topic_match = _compute_topic_match(current_topics, topics_seen)

    # Sentiment trend uses chronological order (oldest first)
    articles_chrono = list(reversed(articles))
    sentiment_trend = _classify_sentiment_trend(articles_chrono)

    data_maturity = _classify_data_maturity(len(articles))
    collection_age_days = _get_collection_age_days(db)

    return {
        "ticker":               ticker,
        "days_lookback":        days_lookback,
        "articles":             articles,
        "article_count":        len(articles),
        "topics_seen":          topics_seen,
        "topic_match":          topic_match,
        "sentiment_trend":      sentiment_trend,
        "data_maturity":        data_maturity,
        "collection_age_days":  collection_age_days,
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_published_at(raw) -> Optional[datetime]:
    """
    Parse published_at from a Firestore document to a timezone-aware datetime.

    Handles:
    - Firestore Timestamp (has a .to_datetime() or is a datetime)
    - datetime object (naive or aware)
    - ISO string ("2025-01-15T00:00:00+00:00", "2025-01-15 00:00:00+00:00")
    - date-only string ("2025-01-15")

    Returns None if unparseable.
    """
    if raw is None:
        return None

    # Firestore Timestamp has to_datetime()
    if hasattr(raw, "to_datetime"):
        try:
            dt = raw.to_datetime()
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            pass

    if isinstance(raw, datetime):
        if raw.tzinfo is None:
            return raw.replace(tzinfo=timezone.utc)
        return raw

    if isinstance(raw, date) and not isinstance(raw, datetime):
        return datetime(raw.year, raw.month, raw.day, tzinfo=timezone.utc)

    if isinstance(raw, str):
        raw = raw.strip()
        for fmt in (
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%S.%f%z",
        ):
            try:
                return datetime.strptime(raw, fmt)
            except ValueError:
                pass
        # Space-separated ISO variant
        raw_normalised = raw.replace(" ", "T", 1)
        try:
            dt = datetime.fromisoformat(raw_normalised)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            pass
        # Date only
        try:
            d = date.fromisoformat(raw[:10])
            return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
        except Exception:
            pass

    return None


def _compute_topics_seen(articles: list) -> list:
    """
    Return a deduplicated sorted list of all key_topics across all articles.
    Preserves original casing from the first occurrence of each topic.
    """
    seen = {}
    for article in articles:
        for topic in (article.get("key_topics") or []):
            key = topic.lower().strip()
            if key not in seen:
                seen[key] = topic  # preserve original form from first occurrence
    return sorted(seen.values())


def _compute_topic_match(
    current_topics: Optional[list],
    topics_seen: list,
) -> Optional[bool]:
    """
    Return True if any topic in current_topics appears in topics_seen.
    Comparison is case-insensitive.
    Returns None if current_topics is not provided.
    """
    if current_topics is None:
        return None

    topics_seen_lower = {t.lower().strip() for t in topics_seen}
    for topic in current_topics:
        if topic.lower().strip() in topics_seen_lower:
            return True
    return False


def _classify_sentiment_trend(articles_chronological: list) -> str:
    """
    Classify sentiment trend from articles in chronological order (oldest first).

    Returns:
      "insufficient_data"  — fewer than 3 articles
      "improving"          — majority (>50%) of articles are positive
      "deteriorating"      — majority (>50%) of articles are negative
      "stable"             — all articles are neutral
      "mixed"              — no clear majority and not all neutral
    """
    if len(articles_chronological) < _MIN_ARTICLES_FOR_TREND:
        return "insufficient_data"

    sentiments = [a.get("sentiment", "neutral") for a in articles_chronological]
    n = len(sentiments)

    pos = sum(1 for s in sentiments if s == "positive")
    neg = sum(1 for s in sentiments if s == "negative")
    neu = sum(1 for s in sentiments if s == "neutral")

    if pos / n > _MAJORITY_THRESHOLD:
        return "improving"
    if neg / n > _MAJORITY_THRESHOLD:
        return "deteriorating"
    if neu == n:
        return "stable"
    return "mixed"


def _classify_data_maturity(article_count: int) -> str:
    """Classify data maturity based on number of articles found."""
    if article_count == 0:
        return "empty"
    if article_count < _SUFFICIENT_THRESHOLD:
        return "sparse"
    return "sufficient"


def _get_collection_age_days(db) -> Optional[int]:
    """
    Return days since the company_news_summaries collection was first populated.
    Uses the oldest created_at timestamp across the entire collection.
    Returns None if the collection is empty or created_at is absent.
    """
    try:
        docs = list(
            db.collection(_COLLECTION)
            .order_by("created_at")
            .limit(1)
            .stream()
        )
        if not docs:
            return None
        data = docs[0].to_dict()
        created_at = _parse_published_at(data.get("created_at"))
        if created_at is None:
            return None
        now = datetime.now(timezone.utc)
        return max(0, (now - created_at).days)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Manual smoke-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("get_company_news_history — smoke test\n")
    print("(company_news_summaries collection starts empty in PoC)\n")

    result = get_company_news_history(
        ticker="LLOY",
        days_lookback=90,
        exclude_id=None,
        current_topics=["regulatory approval", "planning permission"],
    )
    print(f"  ticker:               {result['ticker']}")
    print(f"  article_count:        {result['article_count']}")
    print(f"  data_maturity:        {result['data_maturity']}")
    print(f"  sentiment_trend:      {result['sentiment_trend']}")
    print(f"  topic_match:          {result['topic_match']}")
    print(f"  topics_seen:          {result['topics_seen']}")
    print(f"  collection_age_days:  {result['collection_age_days']}")
    for i, a in enumerate(result["articles"]):
        print(
            f"  [{i}] {a['published_at'].date()} | {a['article_subtype']} | "
            f"{a['sentiment']} | topics={a['key_topics']}"
        )
