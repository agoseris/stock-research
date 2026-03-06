# Shared constants for the LSE Research Terminal Streamlit app.
# Zero dependencies — import freely from any module.

EMOJI_MUTE = "🔇"

_CONFIG_COLLECTION = "app_config"
_LSEG_FILTERS_DOC = "lseg_filters"

_DEFAULT_EXCLUDED_TYPES = [
    "Notice of AGM",
    "Notice of Results",
    "Annual Report",
    "Half-Year Report",
    "Interim Report",
    "Confirmation Statement",
    "Change of Registered Office",
    "Change of Nominated Adviser",
    "Change of Broker",
    "Total Voting Rights",
    "Blocklisting Interim Review",
    "Publication of Prospectus",
    "Result of AGM",
    "Director Declaration",
    "Conversion of B Shares",
]

_DEFAULT_COMPANY_KEYWORDS = ["trust", "trst", "income", "growth", "grwth", "fund"]

_OUTCOME_ORDER = {"passed": 0, "muted": 1, "suppressed": 2}
_OUTCOME_STYLE = {
    "passed":     ("PASSED",     "#27ae60"),
    "muted":      ("MUTED",      "#7f8c8d"),
    "suppressed": ("SUPPRESSED", "#2980b9"),
}
