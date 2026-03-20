import asyncio
from telegram import Bot
from abstractions import NotificationProviderBase
from dotenv import load_dotenv, find_dotenv
import os

load_dotenv(find_dotenv())

_LENS_LABELS = {
    "regulatory_catalyst": ("🔔", "CATALYST"),
    "director_purchasing":  ("📈", "DIR PURCHASE"),
    "tr1_accumulation":     ("🏦", "TR-1 CROSSING"),
}


class TelegramNotifier(NotificationProviderBase):
    """Notification channel implementation using Telegram.
    Implements NotificationProviderBase so this can be swapped
    for email, SMS, or any other channel without changing the
    rest of the system."""

    def __init__(self):
        self.token = os.getenv("TELEGRAM_TOKEN")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID")
        if not self.token or not self.chat_id:
            raise ValueError(
                "TELEGRAM_TOKEN and TELEGRAM_CHAT_ID must be set in .env"
            )

    async def _send(self, message: str) -> None:
        async with Bot(token=self.token) as bot:
            await bot.send_message(
                chat_id=self.chat_id,
                text=message,
                parse_mode="Markdown",
            )

    def send(self, message: str, priority: str = "normal") -> bool:
        """Send a message via Telegram. Returns True on success."""
        try:
            asyncio.run(self._send(message))
            return True
        except Exception as e:
            print(f"Telegram send error: {e}")
            return False

    # ------------------------------------------------------------------
    # Unified entry point
    # ------------------------------------------------------------------

    def format_unified_signal(self, result: dict) -> str:
        """
        Build a complete Telegram message for any lens signal.

        The title is assembled from canonical fields that must be present
        in result before this is called:
          - lens_id        e.g. "director_purchasing"
          - ticker         e.g. "ESNT"
          - recommendation "act" | "monitor" | "ignore"
          - signal_strength "strong" | "substantial" | "moderate" | "weak" | "noise"

        The body is delegated to a per-lens method.
        """
        lens_id  = result.get("lens_id", "")
        emoji, label = _LENS_LABELS.get(lens_id, ("🔔", "SIGNAL"))
        ticker   = result.get("ticker", "Unknown")
        action   = (result.get("recommendation") or "unknown").upper()
        strength = (result.get("signal_strength") or "unknown").upper()
        title    = f"{emoji} *{label} | {ticker} | {action} | {strength}*\n\n"

        if lens_id == "director_purchasing":
            body = self._format_director_body(result)
        elif lens_id == "tr1_accumulation":
            body = self._format_tr1_body(result)
        else:
            body = self._format_catalyst_body(result)

        return title + body

    # ------------------------------------------------------------------
    # Per-lens body formatters (title excluded)
    # ------------------------------------------------------------------

    def _format_catalyst_body(self, result: dict) -> str:
        """Body for a regulatory catalyst signal."""
        analysis = result.get("llm_analysis", "")
        source_url = result.get("source_url", "")
        raw_headline = result.get("headline", "")[:100]

        def extract(field):
            for line in analysis.splitlines():
                if line.startswith(f"{field}:"):
                    return line.split(":", 1)[1].strip()
            return "Unknown"

        relevance        = extract("RELEVANCE")
        signal_type      = extract("SIGNAL_TYPE")
        action           = extract("RECOMMENDED_ACTION")
        summary          = extract("SUMMARY")
        source_reliability = extract("SOURCE_RELIABILITY")

        safe_headline = raw_headline.replace("[", "").replace("]", "").replace("*", "").replace("_", "")
        headline_display = f"[{safe_headline}]({source_url})" if source_url else safe_headline

        market_cap = result.get("market_cap_gbp")
        if market_cap:
            m = market_cap / 1_000_000
            market_cap_str = f"£{m / 1000:.1f}bn" if m >= 1000 else f"£{m:.0f}m"
        else:
            market_cap_str = "Unknown"

        price_pence  = result.get("price_pence")
        price_change = result.get("price_change")
        if price_pence is not None:
            price_str = f"{price_pence:.1f}p"
            if price_change:
                price_str += f" ({price_change})"
        else:
            price_str = "Unknown"

        return (
            f"*Company:* {result.get('company_name', 'Unknown')}\n"
            f"*Ticker:* {result.get('ticker', 'Unknown')}\n"
            f"*Headline:* {headline_display}\n"
            f"*Recommended Action:* {action}\n\n"
            f"*Relevance:* {relevance}\n"
            f"*Signal Type:* {signal_type}\n"
            f"*Source Reliability:* {source_reliability}\n\n"
            f"*Summary:* {summary}\n\n"
            f"*Market Cap:* {market_cap_str}\n"
            f"*Price:* {price_str}\n"
            f"*Published:* {result.get('published_at', 'Unknown')}"
        )

    def _format_director_body(self, result: dict) -> str:
        """Body for a director purchasing signal."""
        source_url   = result.get("source_url", "")
        raw_headline = (result.get("headline", "") or "")[:100]
        recommendation = result.get("simple_recommended_action", result.get("RECOMMENDED_ACTION", "Unknown"))
        summary      = (result.get("simple_summary", result.get("SUMMARY", "")) or "")[:400]
        strength_raw = result.get("simple_signal_strength", result.get("SIGNAL_STRENGTH"))
        nature       = result.get("simple_transaction_nature", result.get("TRANSACTION_NATURE", ""))
        pos_change   = result.get("simple_position_change_pct", result.get("POSITION_CHANGE_PCT"))
        published    = result.get("announcement_published_at") or result.get("published_at", "Unknown")

        safe_headline = raw_headline.replace("[", "").replace("]", "").replace("*", "").replace("_", "")
        headline_display = f"[{safe_headline}]({source_url})" if source_url else safe_headline

        strength_str = f"{strength_raw}/10" if strength_raw is not None else "?"
        change_str   = f" ({pos_change:+.0f}%)" if pos_change is not None else ""

        return (
            f"*Company:* {result.get('company_name', 'Unknown')}\n"
            f"*Ticker:* {result.get('ticker', 'Unknown')}\n"
            f"*Headline:* {headline_display}\n"
            f"*Recommendation:* {recommendation}\n"
            f"*Signal Strength:* {strength_str}\n\n"
            f"*Nature:* {nature}{change_str}\n"
            f"*Summary:* {summary}\n\n"
            f"*Published:* {published}"
        )

    def _format_tr1_body(self, result: dict) -> str:
        """Body for a TR-1 crossing signal."""
        source_url    = result.get("source_url", "")
        raw_headline  = (result.get("headline", "") or "")[:100]
        simple_result = result.get("simple_lens_result") or {}
        recommendation = simple_result.get("recommendation", "Unknown")
        rationale     = (simple_result.get("rationale", "") or "")[:400]
        strength      = simple_result.get("signal_strength", "?")
        notifier      = (result.get("notifier_name", "") or "Unknown")[:60]
        direction     = result.get("direction", "unknown")
        old_pct       = result.get("old_holding_pct")
        new_pct       = result.get("new_holding_pct")
        threshold     = result.get("threshold_crossed")
        published     = result.get("announcement_published_at") or result.get("published_at", "Unknown")

        safe_headline = raw_headline.replace("[", "").replace("]", "").replace("*", "").replace("_", "")
        headline_display = f"[{safe_headline}]({source_url})" if source_url else safe_headline

        holding_str   = f"\n*Holding:* {old_pct:.2f}% → {new_pct:.2f}%" if old_pct is not None and new_pct is not None else ""
        threshold_str = f"\n*Threshold:* {threshold:.0f}%" if threshold else ""

        return (
            f"*Company:* {result.get('company_name', 'Unknown')}\n"
            f"*Ticker:* {result.get('ticker', 'Unknown')}\n"
            f"*Headline:* {headline_display}\n"
            f"*Recommendation:* {recommendation} (strength: {strength})\n\n"
            f"*Notifier:* {notifier}\n"
            f"*Direction:* {direction}{holding_str}{threshold_str}\n\n"
            f"*Rationale:* {rationale}\n\n"
            f"*Published:* {published}"
        )

    # ------------------------------------------------------------------
    # Legacy public formatters — delegate to format_unified_signal
    # ------------------------------------------------------------------

    def format_signal(self, result: dict) -> str:
        """Format a regulatory catalyst signal. Delegates to format_unified_signal."""
        if "lens_id" not in result:
            result = {**result, "lens_id": "regulatory_catalyst"}
        return self.format_unified_signal(result)

    def format_director_signal(self, result: dict) -> str:
        """Format a director purchasing signal. Delegates to format_unified_signal."""
        if "lens_id" not in result:
            result = {**result, "lens_id": "director_purchasing"}
        return self.format_unified_signal(result)

    def format_tr1_signal(self, result: dict) -> str:
        """Format a TR-1 crossing signal. Delegates to format_unified_signal."""
        if "lens_id" not in result:
            result = {**result, "lens_id": "tr1_accumulation"}
        return self.format_unified_signal(result)

    def format_discovery(self, result: dict) -> str:
        """Format a discovery queue result as a Telegram message."""
        assessment = result.get("discovery_assessment", "")

        def extract(field):
            for line in assessment.splitlines():
                if line.startswith(f"{field}:"):
                    return line.split(":", 1)[1].strip()
            return "Unknown"

        company    = extract("COMPANY")
        small_cap  = extract("SMALL_CAP")
        thesis_fit = extract("THESIS_FIT")
        recommend  = extract("RECOMMEND_ADD")
        reason     = extract("REASON")

        return (
            f"🔍 *DISCOVERY: Consider Adding to Universe*\n\n"
            f"*Headline:* {result.get('headline', '')[:100]}\n\n"
            f"*Company Identified:* {company}\n"
            f"*Small Cap:* {small_cap}\n"
            f"*Thesis Fit:* {thesis_fit}\n"
            f"*Reason:* {reason}\n\n"
            f"*Recommendation:* {recommend}\n"
            f"*Source:* {result.get('source', 'Unknown')}"
        )


if __name__ == "__main__":
    notifier = TelegramNotifier()

    # Test message
    success = notifier.send(
        "✅ *Stock Research Bot is online*\n\nNotification channel working correctly."
    )
    print(f"Test message sent: {success}")
