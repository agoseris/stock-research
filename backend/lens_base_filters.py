"""
lens_base_filters.py

Shared filter signals and helper functions used across all strategy lenses.

These signals express the investment *universe* — LSE-listed small-caps —
rather than any specific strategy. Every lens should apply these checks
before applying its own strategy-specific positive signals.

Keeping them here means:
  - Adding a new lens is clean: import and call lse_context_check()
  - Updating the universe definition happens in one place
  - No duplication of LSE context logic across lens files
"""


# Universal negative signals — noise that no lens should ever pass.
# These are fast-exit checks that run before any strategy-specific logic.
UNIVERSAL_NEGATIVE_SIGNALS = [
    # Residential / home improvement noise
    "house price", "mortgage", "residential property",
    "garden", "decking", "home improvement",

    # Local authority planning decisions — private developers, not listed cos
    "local council", "local authority", "planning committee",
    "planning officer", "planning department", "parish council",
    "district council", "borough council", "county council",
    "city council", "town council", "national park authority",

    # Private project companies and SPVs — not investable
    "project company", "special purpose", " spv ",
    "limited liability partnership", " llp ",

    # Non-LSE market noise
    "sensex", "nifty", "nasdaq only", "nyse only",
    "s&p 500", "dow jones",

    # Religious / community planning (high false-positive rate)
    "mosque", "church", "temple", "community centre",
    "school", "hospital", "care home",
]


# LSE context signals — at least one must be present for an announcement
# to pass. This is the key gate against private company noise: a story
# about a planning application by a private project developer will almost
# never contain any of these terms.
LSE_CONTEXT_SIGNALS = [
    "plc", " aim ", "aim-listed", "aim listed",
    "lse:", "lse listed", "london stock exchange",
    "rns", "regulatory news", "companies house",
    "allotment", "placing", "fundrais", "equity raise",
    "capital raise", "subscription", "convertible",
    "investor", "shareholders", "shareholder",
    "annual report", "interim results", "full year results",
    "earnings", "dividend", "market cap",
]


def passes_universe_filter(text: str) -> bool:
    """
    Returns True if the normalised text passes the universal pre-filter.

    Checks:
      1. No universal negative signals present
      2. At least one LSE context signal present

    This should be called by every lens's pre_filter() method before
    applying strategy-specific positive signal checks.

    Args:
        text: Lowercased, concatenated announcement text
              (headline + body + company_name)

    Returns:
        True if the announcement could plausibly relate to an LSE-listed
        company and is not universal noise. False otherwise.
    """
    # Fast exit on any universal negative signal
    for neg in UNIVERSAL_NEGATIVE_SIGNALS:
        if neg in text:
            return False

    # Must have at least one LSE context signal
    if not any(sig in text for sig in LSE_CONTEXT_SIGNALS):
        return False

    return True


if __name__ == "__main__":
    tests = [
        # (description, text, expected)
        ("Altona fundraising (should pass)",
         "altona rare earths plc return of allotment of shares following fundraising capital raise exploration licence",
         True),

        ("Garden decking (should fail — negative signal)",
         "garden decking rules explained local council planning residential property",
         False),

        ("Private SPV — Net Zero Twenty Six (should fail — no LSE context)",
         "planning permission granted energy storage plant net zero twenty six limited tetra tech",
         False),

        ("Local authority decision (should fail — negative signal)",
         "district council planning committee approves solar farm development consent order",
         False),

        ("Generic news, no LSE context (should fail)",
         "planning permission granted for new battery storage facility approved by developer",
         False),

        ("National Grid plc (should pass)",
         "national grid plc development consent order application submitted planning inspectorate shareholders",
         True),
    ]

    print("Universe filter tests:")
    all_passed = True
    for description, text, expected in tests:
        result = passes_universe_filter(text)
        status = "PASS" if result == expected else "FAIL"
        if result != expected:
            all_passed = False
        print(f"  [{status}] {description}")

    print(f"\n{'All tests passed.' if all_passed else 'SOME TESTS FAILED.'}")
