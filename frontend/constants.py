# Shared constants for the LSE Research Terminal Streamlit app.
# Zero dependencies — import freely from any module.

EMOJI_MUTE = "🔇"


# ── Signal states ─────────────────────────────────────────────────────────────
# Canonical values for the signal_state field on universe_companies documents.
# System-managed; transitions driven by signal_state.py in the backend.

class SignalState:
    WATCHING          = "watching"           # No signals detected; company monitored
    MONITOR           = "monitor"            # Weak/moderate signals; below action threshold
    SIGNAL_ACTIVE     = "signal_active"      # Strong signal; review and decide
    SIGNAL_REINFORCED = "signal_reinforced"  # Second strong signal confirmed the first
    SIGNAL_MIXED      = "signal_mixed"       # Counter-signal while positive was active
    SIGNAL_NEGATIVE   = "signal_negative"    # Strong counter-signal; positives suppressed


# ── Position states ───────────────────────────────────────────────────────────
# Canonical values for the position_state field on universe_companies documents.
# Human-managed; set explicitly via UI buttons.

class PositionState:
    ACTED    = "acted"    # Investor has taken a position
    DEFERRED = "deferred" # Decision deferred; signal still active
    DECLINED = "declined" # Investor reviewed and declined to act
    CLOSED   = "closed"   # Position opened previously is now closed


# ── Unified recommendation values ─────────────────────────────────────────────
# Values for the recommendation field on signals_unified documents.
# Written by the backend lens (e.g. regulatory_catalyst, tr1_accumulation).

class Rec:
    ACT     = "act"     # Strong catalyst; worth investigating immediately
    MONITOR = "monitor" # Weak signal; track but below action threshold
    IGNORE  = "ignore"  # No meaningful catalyst signal found


# ── Legacy recommendation values ──────────────────────────────────────────────
# Used by the director buying lens (signals collection) and earlier schema versions.
# The director lens may return multi-word phrases (e.g. "Investigate Further"),
# so substring checks ("investigate" in rec) are intentional for that lens.

class LegacyRec:
    INVESTIGATE = "investigate"  # Maps to Rec.ACT in the unified schema
    YES         = "yes"          # Maps to Rec.ACT (old regulatory catalyst schema)
    MONITOR     = "monitor"      # Equivalent to Rec.MONITOR
    NO          = "no"           # Maps to Rec.IGNORE
    IGNORE      = "ignore"       # Maps to Rec.IGNORE

# ── Firestore collection names ────────────────────────────────────────────────
# All collection names used by the frontend are declared here.
# Never use inline string literals — reference these constants instead.

_CONFIG_COLLECTION     = "app_config"
_LSEG_FILTERS_DOC      = "lseg_filters"

# ── Firestore subcollection names ────────────────────────────────────────────
_SUBCOL_SIGNAL_HISTORY   = "signal_history"    # Append-only signal state transition log
_SUBCOL_POSITION_HISTORY = "position_history"  # Append-only position state transition log

# ── Top-level collection names ────────────────────────────────────────────────
_COL_SIGNALS_UNIFIED   = "signals_unified"     # Regulatory catalyst + TR-1 signals
_COL_SIGNALS           = "signals"             # Director buying signals (legacy collection)
_COL_DISCOVERY_RESULTS = "discovery_results"   # Non-universe companies surfaced by pipeline
_COL_UNIVERSE          = "universe_companies"  # Monitored company list
_COL_PENDING_JOBS      = "pending_jobs"        # Interactive ingestion job queue
_COL_ANNOUNCEMENTS     = "announcements"       # SHA-256 deduplication fingerprints
_COL_SIGNAL_PERF       = "signal_performance"  # Price-movement snapshots (backend cron)

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
