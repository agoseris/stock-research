import asyncio
from telegram import Bot
from abstractions import NotificationProviderBase
from dotenv import load_dotenv, find_dotenv
import os

load_dotenv(find_dotenv())

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

    def format_signal(self, result: dict) -> str:
        """Format a signal queue result as a Telegram message."""
        analysis = result.get("llm_analysis", "")
        source_url = result.get("source_url", "")
        raw_headline = result.get("headline", "")[:100]

        # Extract key fields from structured LLM response
        def extract(field):
            for line in analysis.splitlines():
                if line.startswith(f"{field}:"):
                    return line.split(":", 1)[1].strip()
            return "Unknown"

        relevance = extract("RELEVANCE")
        signal_type = extract("SIGNAL_TYPE")
        action = extract("RECOMMENDED_ACTION")
        summary = extract("SUMMARY")
        source_reliability = extract("SOURCE_RELIABILITY")

        # Headline as clickable link — strip chars that break legacy Markdown link syntax
        safe_headline = raw_headline.replace("[", "").replace("]", "").replace("*", "").replace("_", "")
        if source_url:
            headline_display = f"[{safe_headline}]({source_url})"
        else:
            headline_display = safe_headline

        # Market cap: stored as raw GBP (e.g. 45_300_000), display in £m / £bn
        market_cap = result.get("market_cap_gbp")
        if market_cap:
            m = market_cap / 1_000_000
            market_cap_str = f"£{m / 1000:.1f}bn" if m >= 1000 else f"£{m:.0f}m"
        else:
            market_cap_str = "Unknown"

        # Price from LSEG index page (pence)
        price_pence = result.get("price_pence")
        price_change = result.get("price_change")
        if price_pence is not None:
            price_str = f"{price_pence:.1f}p"
            if price_change:
                price_str += f" ({price_change})"
        else:
            price_str = "Unknown"

        return (
            f"🔔 *SIGNAL DETECTED*\n\n"
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

    def format_discovery(self, result: dict) -> str:
        """Format a discovery queue result as a Telegram message."""
        assessment = result.get("discovery_assessment", "")

        def extract(field):
            for line in assessment.splitlines():
                if line.startswith(f"{field}:"):
                    return line.split(":", 1)[1].strip()
            return "Unknown"

        company = extract("COMPANY")
        small_cap = extract("SMALL_CAP")
        thesis_fit = extract("THESIS_FIT")
        recommend = extract("RECOMMEND_ADD")
        reason = extract("REASON")

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
