"""
Monitored Universe of LSE-listed small-caps.

Primary source: docs/AIM_data_complete_*.csv and docs/FTSE_AllShare_complete_*.csv
These static files are updated manually (weekly or monthly) and committed to git,
which builds a version history of universe composition over time. The most recent
file matching each name pattern is used automatically.

Fallback: the hand-picked UNIVERSE list below is used only if no CSV files are
found in docs/ — this allows the pipeline to run on environments where the repo
has not been fully deployed with the data files.

The static CSV approach via import_universe_csv.py is the settled
universe management method.
"""

import csv
import glob
import os

# Path to docs/ relative to this file (backend/../docs/)
_DOCS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "docs")


def _load_from_docs_csvs() -> list:
    """
    Load universe from the most recent AIM and FTSE All-Share CSV files in docs/.

    Looks for files matching:
      docs/AIM_data_complete_*.csv
      docs/FTSE_AllShare_complete_*.csv

    The alphabetically last file from each pattern is used (the YYYYMMDD suffix
    makes alphabetical order equivalent to chronological order). Both lists are
    merged and deduplicated on ticker symbol.

    Returns a list of {"ticker": ..., "name": ...} dicts, or [] if no files found.
    """
    aim_files = sorted(glob.glob(os.path.join(_DOCS_DIR, "AIM_data_complete_*.csv")))
    ftse_files = sorted(glob.glob(os.path.join(_DOCS_DIR, "FTSE_AllShare_complete_*.csv")))

    sources = []
    if aim_files:
        sources.append(aim_files[-1])
    if ftse_files:
        sources.append(ftse_files[-1])

    if not sources:
        return []

    companies = []
    seen: set = set()

    for filepath in sources:
        try:
            with open(filepath, encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    ticker = (row.get("Code") or "").strip()
                    name = (row.get("Name") or "").strip()
                    if ticker and name and ticker not in seen:
                        seen.add(ticker)
                        companies.append({"ticker": ticker, "name": name})
        except Exception as e:
            print(f"  Warning: could not read {os.path.basename(filepath)}: {e}")

    return companies


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
    """
    Return the hand-picked fallback company list.

    NOTE: this is no longer used by pipeline.py. The pipeline requires a
    Firestore-backed universe loaded via import_universe_csv.py. This list
    is retained for reference and as a last-resort manual debugging aid.
    """
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
    # Show CSV universe status
    csv_companies = _load_from_docs_csvs()
    if csv_companies:
        print(f"CSV universe: {len(csv_companies)} companies loaded from docs/")
        print(f"  Sample (first 5): {[c['ticker'] for c in csv_companies[:5]]}")
        print(f"  Sample (last 5):  {[c['ticker'] for c in csv_companies[-5:]]}")
        print()
        print("To import this universe into Firestore, run:")
        print("  python import_universe_csv.py")
    else:
        print("No CSV files found in docs/ — showing static fallback only.")
    print()

    # Show static fallback
    print(f"Static fallback: {len(UNIVERSE)} hand-picked companies")
    print(f"  Active: {len(get_active_companies())}")
    print(f"  Verified with Companies House number: {len(get_companies_house_numbers())}")
    print()
    for company in UNIVERSE:
        ch = company["companies_house"] or "NOT VERIFIED"
        print(f"  [{company['ticker']}] {company['name']}")
        print(f"    Sector: {company['sector']}")
        print(f"    CH: {ch}")
        print()
