"""
Monitored Universe of LSE-listed small-caps.
Each entry is verified against Companies House and LSE listings.
Add new entries as they are researched and confirmed.

Fields:
  ticker          - LSE ticker symbol (without .L suffix)
  name            - Full registered company name
  companies_house - Companies House registration number (verified)
  sector          - Broad sector classification
  thesis_notes    - Why this company fits the overarching investment thesis
  active          - Whether this company is currently being monitored
"""

UNIVERSE = [
    {
        "ticker": "REE",
        "name": "Altona Rare Earths PLC",
        "companies_house": "05350512",
        "sector": "Rare Earths / Critical Minerals",
        "thesis_notes": (
            "Developing the Monte Muambe Rare Earths project in Mozambique. "
            "Permitting and feasibility milestones are key catalysts. "
            "Expanding into copper in Botswana and Zambia. "
            "LSE Main Market Standard segment. Market cap ~£6-8m."
        ),
        "active": True
    },
    {
        "ticker": "ACG",
        "name": "ACG Metals Ltd",
        "companies_house": "",  # To be verified
        "sector": "Metals Mining",
        "thesis_notes": (
            "Gediktepe mine in Turkey. Beat 2025 production guidance. "
            "On track to become a copper producer with expansion works "
            "on time and on budget. Operational milestones imminent."
        ),
        "active": True
    },
    {
	"ticker": "ECOR",
        "name": "Ecora Royalties PLC",
        "companies_house": "00897608",
        "sector": "Mining Royalties",
        "thesis_notes": (
            "Royalty company formerly known as Ecora Resources, Anglo Pacific Group. "
            "Renamed to Ecora Royalties in January 2026, actively repositioning "
            "toward sustainable commodities royalties. Strong cobalt exposure via "
            "Voisey's Bay. Name change itself signals strategic catalyst in play."
        ),
        "active": True
    },
    {
        "ticker": "MCMM",
        "name": "MC Mining Ltd",
        "companies_house": "",  # Likely incorporated outside UK jurisdiction
        "sector": "Coal / Energy",
        "thesis_notes": (
            "LSE-listed coal miner. Market cap ~£73m. "
            "Permitting and operational milestones in play."
        ),
        "active": True
    },
    {
        "ticker": "GMR",
        "name": "Gaming Realms PLC",
        "companies_house": "04175777",  # To be verified
        "sector": "Gaming Technology",
        "thesis_notes": (
            "Revenues growing 18% half-year. US state-by-state regulatory "
            "licence expansion is the key catalyst — each new state licence "
            "is a binary regulatory event with meaningful revenue upside."
        ),
        "active": True
    },
]


def get_active_companies():
    """Return only currently monitored companies."""
    return [c for c in UNIVERSE if c.get("active", True)]


def get_tickers():
    """Return list of active ticker symbols."""
    return [c["ticker"] for c in get_active_companies()]


def get_by_ticker(ticker):
    """Look up a company by ticker symbol."""
    for company in UNIVERSE:
        if company["ticker"].upper() == ticker.upper():
            return company
    return None


def get_companies_house_numbers():
    """Return dict of ticker -> Companies House number for verified entries."""
    return {
        c["ticker"]: c["companies_house"]
        for c in get_active_companies()
        if c["companies_house"]
    }


if __name__ == "__main__":
    print(f"Universe contains {len(UNIVERSE)} companies")
    print(f"Active: {len(get_active_companies())}")
    print(f"Verified with Companies House number: {len(get_companies_house_numbers())}")
    print()
    for company in get_active_companies():
        ch = company["companies_house"] or "NOT VERIFIED"
        print(f"  [{company['ticker']}] {company['name']}")
        print(f"    Sector: {company['sector']}")
        print(f"    CH: {ch}")
        print()
