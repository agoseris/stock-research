# test_notifier.py (run from backend/)
from telegram_notifier import TelegramNotifier

n = TelegramNotifier.__new__(TelegramNotifier)  # bypass __init__ (no .env needed)

# Director
director = {
    "lens_id": "director_purchasing", "ticker": "ESNT",
    "recommendation": "act", "signal_strength": "substantial",
    "company_name": "Esoteric Ltd", "headline": "Director buys shares",
    "source_url": "", "announcement_published_at": "2026-03-20",
    "RECOMMENDED_ACTION": "Investigate", "SIGNAL_STRENGTH": 8,
    "SUMMARY": "Strong conviction buy.", "TRANSACTION_NATURE": "Open market",
    "POSITION_CHANGE_PCT": 12.5, "LIMITATIONS": "None",
}

# TR-1
tr1 = {
    "lens_id": "tr1_accumulation", "ticker": "GSK",
    "recommendation": "act", "signal_strength": "strong",
    "company_name": "GSK plc", "headline": "TR-1: Major holder crosses 5%",
    "source_url": "", "announcement_published_at": "2026-03-20",
    "notifier_name": "BlackRock Inc", "direction": "above",
    "old_holding_pct": 4.98, "new_holding_pct": 5.02, "threshold_crossed": 5,
    "simple_lens_result": {"recommendation": "Investigate", "signal_strength": "strong",
                           "rationale": "Large holder accumulating.", "limitations": ""},
}

# Catalyst
catalyst = {
    "lens_id": "regulatory_catalyst", "ticker": "AZN",
    "recommendation": "monitor", "signal_strength": "moderate",
    "company_name": "AstraZeneca", "headline": "Phase 3 trial results due",
    "source_url": "https://example.com", "published_at": "2026-03-20",
    "llm_analysis": "RECOMMENDED_ACTION: Monitor\nRELEVANCE: 6/10\nSIGNAL_TYPE: Clinical\nSUMMARY: Watch trial.\nSOURCE_RELIABILITY: High\n",
}

for payload in [director, tr1, catalyst]:
    msg = n.format_unified_signal(payload)
    first_line = msg.splitlines()[0]
    print(first_line)
    assert payload["ticker"] in first_line
    assert payload["recommendation"].upper() in first_line
    assert payload["signal_strength"].upper() in first_line

print("All assertions passed.")

