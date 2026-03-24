# Config tab renderer — live editing of announcement type exclusions and company name keywords.

import streamlit as st

from firestore_helpers import (
    get_exclusion_list,
    save_exclusion_list,
    get_company_keywords,
    save_company_keywords,
)


def render_config_tab(db) -> None:
    """Render the Config tab contents."""

    # ── Announcement Type Exclusions ──────────────────────────────────────────

    st.markdown(
        '<div class="terminal-header" style="margin-bottom:0.6rem;">Announcement Type Exclusions</div>',
        unsafe_allow_html=True,
    )
    st.caption("Announcement types suppressed before LLM analysis. Match is case-insensitive substring.")

    current_excluded = get_exclusion_list(db)

    for entry in current_excluded:
        col_label, col_btn = st.columns([5, 1])
        col_label.caption(entry)
        if col_btn.button("×", key=f"remove_excl_{entry}"):
            updated = [e for e in current_excluded if e != entry]
            save_exclusion_list(db, updated)
            st.rerun()

    st.markdown("")
    new_type = st.text_input(
        "Add type",
        key="new_excl_type",
        placeholder="e.g. Result of EGM",
        label_visibility="collapsed",
    )
    if st.button("Add", key="add_excl_type_btn") and new_type.strip():
        entry = new_type.strip()
        if entry not in current_excluded:
            save_exclusion_list(db, current_excluded + [entry])
        st.rerun()

    st.markdown("---")

    # ── Company Name Keywords ─────────────────────────────────────────────────

    st.markdown(
        '<div class="terminal-header" style="margin-bottom:0.6rem;">Company Name Keywords</div>',
        unsafe_allow_html=True,
    )
    st.caption("Company names containing these substrings are suppressed (investment trusts / funds). Case-insensitive.")

    current_keywords = get_company_keywords(db)

    for kw in current_keywords:
        col_label, col_btn = st.columns([5, 1])
        col_label.caption(kw)
        if col_btn.button("×", key=f"remove_kw_{kw}"):
            updated = [k for k in current_keywords if k != kw]
            save_company_keywords(db, updated)
            st.rerun()

    st.markdown("")
    new_kw = st.text_input(
        "Add keyword",
        key="new_company_kw",
        placeholder="e.g. reit",
        label_visibility="collapsed",
    )
    if st.button("Add", key="add_company_kw_btn") and new_kw.strip():
        kw = new_kw.strip().lower()
        if kw not in current_keywords:
            save_company_keywords(db, current_keywords + [kw])
        st.rerun()
