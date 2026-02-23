from abstractions import StrategyLensBase, Announcement
from lens_base_filters import passes_universe_filter


class RegulatoryCatalystLens(StrategyLensBase):
    """
    Strategy Lens: Regulatory and Planning Catalyst

    Identifies LSE small-cap companies with funding in place that face
    a defined regulatory or planning hurdle, where the market may be
    mispricing the probability of a positive outcome.

    The informed confidence signal: committed funding alongside a binary
    upcoming event — permit, planning decision, licence, approval —
    that has significant upside if resolved positively.

    Pre-filter logic:
      1. Universal universe filter (LSE context, no noise) — via lens_base_filters
      2. Strategy-specific positive signals (regulatory/planning keywords)

    The separation keeps universe-level logic in one place and ensures
    this lens only contains what is specific to the regulatory catalyst strategy.
    """

    # Keywords specific to the regulatory and planning catalyst strategy.
    # At least one must be present after the universe filter passes.
    POSITIVE_SIGNALS = [
        # Planning and permission
        "planning permission", "planning consent", "planning approval",
        "planning application", "planning appeal", "planning inquiry",
        "planning inspectorate", "development consent", "development order",
        "permitted development",

        # Regulatory approvals
        "regulatory approval", "regulatory clearance", "regulatory decision",
        "fca approval", "fca clearance", "environment agency",
        "environmental permit", "environmental approval",
        "environmental impact", "eia", "habitat regulations",

        # Mining and resource permits
        "mining permit", "mining licence", "mining license",
        "exploration licence", "extraction permit", "mineral licence",
        "resource permit", "drilling permit", "production licence",

        # Energy specific
        "grid connection", "development consent order", "dco",
        "energy permit", "generation licence", "ofgem",
        "nuclear decommissioning", "oil licence", "gas licence",

        # Corporate events suggesting funding in place
        "allotment of shares", "placing", "fundraise", "fundraising",
        "equity raise", "capital raise", "subscription agreement",
        "convertible loan", "drawdown",

        # Outcome signals — permit granted or refused
        "permit granted", "approved", "consent granted",
        "licence awarded", "refused", "rejected", "dismissed",
        "appeal allowed", "appeal dismissed",
    ]

    # Sectors of particular interest to this lens.
    # Not required to pass — noted here for documentation and
    # potential future use in confidence scoring.
    RELEVANT_SECTORS = [
        "mining", "mineral", "resource", "energy", "renewable",
        "oil", "gas", "solar", "wind", "battery", "lithium",
        "rare earth", "gold", "copper", "silver", "zinc",
        "uranium", "coal", "iron", "steel", "exploration",
    ]

    def pre_filter(self, announcement: Announcement) -> bool:
        """
        Returns True if this announcement warrants LLM analysis.
        Fast, cheap, keyword-based — runs on every announcement.

        Two-stage check:
          1. Universe filter: rejects non-LSE noise (shared across all lenses)
          2. Strategy filter: requires at least one regulatory/planning signal
        """
        text = (
            (announcement.headline or "") + " " +
            (announcement.body or "") + " " +
            (announcement.company_name or "")
        ).lower()

        # Stage 1: Universal universe filter — LSE context and noise rejection.
        # Handles local authority decisions, private SPVs, residential noise etc.
        if not passes_universe_filter(text):
            return False

        # Stage 2: Strategy-specific positive signals.
        # Must contain at least one regulatory or planning catalyst keyword.
        if not any(sig in text for sig in self.POSITIVE_SIGNALS):
            return False

        return True

    def build_prompt(self, announcement: Announcement) -> str:
        """
        Constructs a structured LLM prompt for deep analysis.
        Called only for announcements that passed pre_filter.
        """
        # Companies House confidence note — included only when the CH number
        # was matched by name similarity rather than exact lookup.
        # None = source is not Companies House (not applicable).
        # 1.0 = exact match (no caveat needed).
        # 0.6–<1.0 = fuzzy match accepted above threshold — flag the uncertainty.
        ch_confidence = announcement.companies_house_confidence
        if ch_confidence is not None and ch_confidence < 1.0:
            ch_note = (
                f"\nDATA_QUALITY NOTE: The Companies House registration for this company "
                f"was matched by name similarity (confidence: {ch_confidence:.0%}). "
                f"The filing data may relate to a different corporate entity. "
                f"Factor this into your SOURCE_RELIABILITY assessment."
            )
        else:
            ch_note = ""

        return f"""You are an investment research analyst specialising in LSE-listed small-cap companies.

Your task is to assess whether the following announcement represents a meaningful catalyst opportunity consistent with this investment thesis:

INVESTMENT THESIS:
Identify LSE small-caps where informed or knowledgeable parties have expressed confidence in an upcoming increase in value. Specifically for this lens: companies that have funding in place but face a defined regulatory or planning hurdle, where the market may be mispricing the probability of a positive outcome.

ANNOUNCEMENT:
Company: {announcement.company_name}
Ticker: {announcement.ticker}
Source: {announcement.source_name}
Published: {announcement.published_at}
Headline: {announcement.headline}
Body: {announcement.body}{ch_note}

ASSESSMENT INSTRUCTIONS:
1. RELEVANCE: Is this announcement relevant to the regulatory/planning catalyst thesis? Score 0-10.
2. SIGNAL TYPE: What type of regulatory or planning event is described? (permit, planning permission, licence, approval, fundraising ahead of a catalyst, etc.)
3. CONFIDENCE SIGNAL: Is there evidence of informed confidence? (funding committed, insiders buying, strong institutional backing, etc.)
4. OUTCOME_PROBABILITY: If a regulatory decision is pending, what is your assessment of the probability of a positive outcome? What factors inform this?
5. MARKET MISPRICING: Is there any indication the market may be underestimating the probability or magnitude of a positive outcome?
6. SOURCE RELIABILITY: How reliable is this source? (Official filing = high, specialist financial press = medium, general news = lower)
7. RECOMMENDED ACTION: Should this be surfaced to the investor? Yes / No / Monitor
8. SUMMARY: In 2-3 sentences, explain your assessment clearly.

Respond in this exact format:
RELEVANCE: [score]/10
SIGNAL_TYPE: [type]
CONFIDENCE_SIGNAL: [description or "None identified"]
OUTCOME_PROBABILITY: [assessment]
MARKET_MISPRICING: [assessment or "Insufficient information"]
SOURCE_RELIABILITY: [High/Medium/Low] - [reason]
RECOMMENDED_ACTION: [Yes/No/Monitor]
SUMMARY: [2-3 sentences]
"""


if __name__ == "__main__":
    from datetime import datetime, timezone

    lens = RegulatoryCatalystLens()

    def make_announcement(headline, body, company_name, ticker="UNKNOWN"):
        return type('Announcement', (), {
            'headline': headline,
            'body': body,
            'company_name': company_name,
            'ticker': ticker,
            'source_name': 'Google News RSS',
            'published_at': datetime.now(timezone.utc)
        })()

    tests = [
        (make_announcement(
            'Altona Rare Earths PLC: Return of Allotment of Shares following fundraising',
            'The company has completed a capital raise to fund exploration licence application',
            'Altona Rare Earths PLC', 'REE'),
         True, "Fundraising + exploration licence (plc, should pass)"),

        (make_announcement(
            'Garden decking rules explained for homeowners',
            'Local council planning rules for residential garden improvements',
            'Unknown'),
         False, "Garden decking noise (should fail — universal negative)"),

        (make_announcement(
            'National Grid submits planning application to upgrade electricity network',
            'National Grid plc has submitted a development consent order application',
            'National Grid plc', 'NG'),
         True, "plc + planning application (should pass)"),

        (make_announcement(
            'Planning permission granted for energy storage plant scheme',
            'The application by Net Zero Twenty Six Limited was through the agent Natalie Wilson (Tetra Tech)',
            'Unknown'),
         False, "Private SPV — Net Zero Twenty Six (should fail — no LSE context)"),

        (make_announcement(
            'Local authority planning committee approves solar farm development consent',
            'The district council planning officer recommended approval for the renewable energy project',
            'Unknown'),
         False, "Local authority decision (should fail — universal negative)"),
    ]

    print("RegulatoryCatalystLens pre_filter tests:")
    all_passed = True
    for announcement, expected, description in tests:
        result = lens.pre_filter(announcement)
        status = "PASS" if result == expected else "FAIL"
        if result != expected:
            all_passed = False
        print(f"  [{status}] {description}")

    print(f"\n{'All tests passed.' if all_passed else 'SOME TESTS FAILED.'}")
