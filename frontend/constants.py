# Shared constants for the LSE Research Terminal Streamlit app.
# Zero dependencies — import freely from any module.

EMOJI_MUTE = "🔇"

_CONFIG_COLLECTION = "app_config"
_LSEG_FILTERS_DOC = "lseg_filters"

_DEFAULT_EXCLUDED_TYPES = [
    "Holding(s) in Company",
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

_OUTCOME_ORDER = {"passed": 0, "discovery": 1, "muted": 2, "suppressed": 3}
_OUTCOME_STYLE = {
    "passed":     ("PASSED",     "#27ae60"),
    "discovery":  ("DISCOVERY",  "#f39c12"),
    "muted":      ("MUTED",      "#7f8c8d"),
    "suppressed": ("SUPPRESSED", "#2980b9"),
}
