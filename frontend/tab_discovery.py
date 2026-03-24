# Discovery tab renderer — companies surfaced by the pipeline that are not in the universe.

import streamlit as st

from firestore_helpers import dismiss_document, submit_universe_admit_job
from ui_helpers import recommend_add_badge, parse_analysis, get_field, format_timestamp
from constants import _COL_DISCOVERY_RESULTS


def _discovery_sort_key(item):
    val = get_field(item[1].get("discovery_assessment", ""), "RECOMMEND_ADD").lower()
    return {"yes": 0, "maybe": 1}.get(val, 2)


def render_discovery_tab(db, discoveries: list) -> None:
    """Render the Discovery tab contents."""
    if not discoveries:
        st.markdown('<div class="empty-state">NO DISCOVERY ITEMS</div>', unsafe_allow_html=True)
        st.caption(
            "Discovery results are created when the job runner processes a company that is "
            "not in the monitored universe. To generate one: upload an LSEG export in the "
            "Ingest tab, find a row with outcome DISCOVERY, click → Pass to move it to "
            "Passed, then submit it for analysis. The job runner routes non-universe "
            "submissions here automatically."
        )
        return

    for doc_id, result in sorted(discoveries, key=_discovery_sort_key):
        assessment = result.get("discovery_assessment", "")
        badge_html, card_class = recommend_add_badge(assessment)
        company    = get_field(assessment, "COMPANY")
        reason     = get_field(assessment, "REASON")
        thesis_fit = get_field(assessment, "THESIS_FIT")
        source_badge = f'<span class="badge badge-source">{result.get("source", "—")}</span>'
        thesis_badge = f'<span class="badge badge-source">Thesis fit {thesis_fit}</span>' if thesis_fit else ""

        st.markdown(f"""
<div class="signal-card {card_class}">
    <div>
        <span class="card-ticker">{company or result.get("company_name", "—")}</span>
    </div>
    <div class="card-headline">{result.get("headline", "—")}</div>
    <div class="card-meta">{format_timestamp(result.get("assessed_at", ""))}</div>
    <div>{badge_html}{source_badge}{thesis_badge}</div>
    {f'<div style="margin-top:0.7rem;font-size:0.82rem;color:#7a9ab8;font-family:IBM Plex Sans,sans-serif;line-height:1.5;">{reason}</div>' if reason else ''}
</div>
""", unsafe_allow_html=True)

        col1, col2 = st.columns([8, 1])
        with col1:
            with st.expander("Full assessment"):
                parsed = parse_analysis(assessment)
                formatted = "\n".join(
                    f"{k}: {v}" if k else v
                    for k, v in parsed
                )
                st.markdown(f'<div class="analysis-block">{formatted}</div>', unsafe_allow_html=True)

            with st.expander("Admit to Universe"):
                admit_ticker = st.text_input(
                    "Ticker", value=result.get("ticker", ""),
                    key=f"disc_admit_ticker_{doc_id}"
                )
                admit_name = st.text_input(
                    "Company Name", value=result.get("company_name", ""),
                    key=f"disc_admit_name_{doc_id}"
                )
                admit_exchange = st.selectbox(
                    "Exchange", ["AIM", "LSE Main"],
                    key=f"disc_admit_exchange_{doc_id}"
                )
                admit_mcap = st.number_input(
                    "Market Cap (£M)", min_value=0.0,
                    value=0.0, key=f"disc_admit_mcap_{doc_id}"
                )
                if st.button("Submit for admission", key=f"disc_admit_submit_{doc_id}"):
                    exch_code = "AIM" if admit_exchange == "AIM" else "LSE_MAIN"
                    mcap_gbp = admit_mcap * 1_000_000 if admit_mcap > 0 else None
                    submit_universe_admit_job(
                        db, admit_ticker, admit_name, mcap_gbp, exch_code,
                        not_of_interest=False, source_discovery_id=doc_id
                    )
                    st.success("Admission job submitted — CH lookup running on VM.")

        with col2:
            if st.button("Dismiss", key=f"dismiss_disc_{doc_id}"):
                dismiss_document(db, _COL_DISCOVERY_RESULTS, doc_id)
                st.rerun()
