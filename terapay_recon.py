import streamlit as st
import pandas as pd
import tempfile
import os
from datetime import datetime

# =========================
# Streamlit Page Config
# =========================
st.set_page_config(
    page_title="Terapay Reconciliation Dashboard",
    page_icon="💸",
    layout="wide",
)

# =========================
# Helper: save uploaded file to temp
# =========================
def save_upload_to_temp(uploaded_file, suffix):
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp.write(uploaded_file.getbuffer())
    tmp.close()
    return tmp.name


# =========================
# BACKEND LOGIC (your working script, parameterized)
# =========================
def terapay_reconciliation_backend(sp_path, operator_path, excel_output_path):
    """
    This is your backend logic (Working fine Terapay Reconciliation with SP summary, stuck sheet & SP remitted discrepancies),
    only parameterized for input/output paths. Internal logic is unchanged.
    """

    import pandas as pd

    # =========================
    # 2. READ OPERATOR FILE
    # =========================
    operator_df = pd.read_csv(
        operator_path,
        dtype={"Transaction Id": str, "Partner TxnId": str}
    )

    # CLEAN IDs (remove apostrophe)
    operator_df["Transaction Id"] = operator_df["Transaction Id"].astype(str).str.strip().str.replace(r"^'+", "", regex=True)
    operator_df["Partner TxnId"] = operator_df["Partner TxnId"].astype(str).str.strip().str.replace(r"^'+", "", regex=True)

    # Convert DR/CR to numbers
    operator_df["DR"] = pd.to_numeric(operator_df["DR"], errors="coerce").fillna(0)
    operator_df["CR"] = pd.to_numeric(operator_df["CR"], errors="coerce").fillna(0)

    # Normalized remarks column (for reuse)
    remarks_norm = operator_df["Remarks"].astype(str).str.strip().str.upper()

    # =========================
    # 3. BASIC OPERATOR CHECKS + STATUS COUNTS
    # =========================
    total_records = len(operator_df)

    # COMMIT
    commit_df = operator_df[remarks_norm == "COMMIT"].copy()
    total_commit_records = len(commit_df)

    # ROLLBACK
    rollback_df = operator_df[remarks_norm == "ROLLBACK"].copy()
    total_rollback_records = len(rollback_df)

    # REVERSAL SUCCESS
    reversal_success_df = operator_df[remarks_norm == "REVERSAL SUCCESS"].copy()
    total_reversal_success_records = len(reversal_success_df)

    # Duplicate check on Transaction Id
    duplicates_mask = operator_df["Transaction Id"].duplicated(keep=False)
    duplicate_records = operator_df[duplicates_mask]

    print("========= OPERATOR FILE SUMMARY =========")
    print("Total records in operator file      :", total_records)
    print("Total COMMIT records                :", total_commit_records)
    print("Total ROLLBACK records              :", total_rollback_records)
    print("Total REVERSAL SUCCESS records      :", total_reversal_success_records)

    if len(duplicate_records) == 0:
        print("No duplicate Transaction Id found in operator file.")
    else:
        print("Duplicate Transaction Ids found:", len(duplicate_records))
        print(duplicate_records["Transaction Id"].unique()[:10])
    print("========================================")

    # =========================
    # 4. READ SP FILE + SP SUMMARY
    # =========================
    # Read full SP file (all columns)
    sp_full_df = pd.read_excel(
        sp_path,
        dtype={"Integration Id": str}
    )

    # Clean Integration ID in full SP data
    sp_full_df["Integration Id"] = sp_full_df["Integration Id"].astype(str).str.strip().str.replace(r"^'+", "", regex=True)

    # ---- SP FILE SUMMARY ----
    total_sp_records = len(sp_full_df)

    # clean state for grouping/filtering
    state_clean = sp_full_df["State"].astype(str).str.strip()
    state_clean_lower = state_clean.str.lower()

    # State-wise distribution (original casing in output)
    state_distribution = state_clean.value_counts(dropna=False)

    print("\n========= SP FILE SUMMARY =========")
    print("Total records in SP file      :", total_sp_records)
    print("\nState-wise distribution:")
    print(state_distribution)

    # Focus statuses
    focus_statuses = ["aml_review", "on_hold", "published", "stuck", "in process"]
    focus_mask = state_clean_lower.isin(focus_statuses)
    sp_focus_df = sp_full_df[focus_mask].copy()  # all columns

    print("\nFocus Status Counts (aml_review, on_hold, published, stuck, In process):")
    for status in focus_statuses:
        count_status = (state_clean_lower == status).sum()
        print(f"  {status}: {count_status}")

    print("========================================")

    # ---- SP DATA FOR RECONCILIATION (ONLY REMITTED) ----
    sp_df = sp_full_df[
        state_clean_lower == "remitted"
    ].copy()

    sp_df = sp_df[["Integration Id", "Remitted"]].drop_duplicates()

    # =========================
    # 5. RECONCILIATION (COMMIT vs SP) - FIXED MERGE
    # =========================
    commit_df = commit_df.drop_duplicates(subset=["Transaction Id"])

    merged = pd.merge(
        commit_df,
        sp_df,
        left_on="Transaction Id",
        right_on="Integration Id",
        how="inner"
    )

    merged["DR"] = pd.to_numeric(merged["DR"], errors="coerce")
    merged["Remitted"] = pd.to_numeric(merged["Remitted"], errors="coerce")
    merged["Difference"] = merged["DR"] - merged["Remitted"]

    result = merged[["Transaction Id", "DR", "Remitted", "Difference"]]

    print("\nSample Reconciliation output:")
    print(result.head())

    # =========================
    # 5a. DISCREPANCY COMMIT TRANSACTIONS (in operator, not in SP)
    # =========================
    matched_commit_ids = set(result["Transaction Id"])
    discrepancy_commit_df = commit_df[~commit_df["Transaction Id"].isin(matched_commit_ids)].copy()

    print("\nTotal COMMIT records               :", total_commit_records)
    print("Matched COMMIT in SP (Reconciliation):", len(result))
    print("Discrepancy COMMIT transactions in operator (Present in operator but not in SP remit)      :",
          len(discrepancy_commit_df))
    if not discrepancy_commit_df.empty:
        print("\nSample discrepancy COMMIT record(s):")
        print(discrepancy_commit_df.head())

    # =========================
    # 5b. DISCREPANCY REMITTED TRANSACTIONS (in SP, not in operator)
    # =========================
    # Take all remitted SP rows with full columns
    remitted_sp_full = sp_full_df[state_clean_lower == "remitted"].copy()
    sp_remitted_ids = set(remitted_sp_full["Integration Id"])

    # IDs present in SP (remitted) but not matched with any COMMIT in operator
    sp_unmatched_ids = sp_remitted_ids - matched_commit_ids

    sp_remitted_discrepancy_df = remitted_sp_full[
        remitted_sp_full["Integration Id"].isin(sp_unmatched_ids)
    ].copy()

    print("\nDiscrepancy REMITTED transactions in SP (present in SP but not in operator COMMIT):",
          len(sp_remitted_discrepancy_df))

    if not sp_remitted_discrepancy_df.empty:
        print("\nSample SP remitted discrepancy record(s):")
        print(sp_remitted_discrepancy_df.head())

    # ======================================================
    # 6. TASK 2 — ROLLBACK ANALYSIS (Partner TxnId DR/CR)
    # ======================================================

    print("\nTotal ROLLBACK records:", len(rollback_df))

    # Group by Partner TxnId (check debit/credit pairs)
    rollback_summary = (
        rollback_df
        .groupby("Partner TxnId", as_index=False)
        .agg(
            total_DR=("DR", "sum"),
            total_CR=("CR", "sum"),
            rows=("Partner TxnId", "size")
        )
    )

    # Flags
    rollback_summary["has_debit"] = rollback_summary["total_DR"] > 0
    rollback_summary["has_credit"] = rollback_summary["total_CR"] > 0
    rollback_summary["is_balanced"] = rollback_summary["total_DR"] == rollback_summary["total_CR"]

    # PROBLEM CASES
    problem_cases = rollback_summary[
        (~rollback_summary["has_debit"]) |
        (~rollback_summary["has_credit"]) |
        (~rollback_summary["is_balanced"])
    ]

    print("\nSample Rollback Summary:")
    print(rollback_summary.head())

    print("\nProblematic ROLLBACK entries:", len(problem_cases))
    print(problem_cases.head())

    # =========================
    # 7. OPTIONAL — SPECIFIC ID CHECK
    # =========================
    test_id = "9077312362642872"
    check = rollback_df[rollback_df["Partner TxnId"] == test_id]
    print(f"\nDetails for Partner TxnId = {test_id}:")
    print(check[["Partner TxnId", "DR", "CR", "Remarks"]])

    # =========================
    # 8. SAVE ALL OUTPUTS TO SINGLE EXCEL FILE (MULTIPLE SHEETS)
    # =========================

    # Build SP state summary DataFrame for Excel
    sp_state_summary_df = state_distribution.rename_axis("State").reset_index(name="Count")

    with pd.ExcelWriter(excel_output_path, engine="xlsxwriter") as writer:
        # Sheet 1: COMMIT Reconciliation
        result.to_excel(writer, sheet_name="Reconciliation", index=False)
        
        # Sheet 2: Full Rollback Summary (per Partner TxnId)
        rollback_summary.to_excel(writer, sheet_name="Rollback_Summary", index=False)
        
        # Sheet 3: Only problematic rollback cases
        problem_cases.to_excel(writer, sheet_name="Rollback_Problem_Cases", index=False)
        
        # Sheet 4: Discrepancy COMMIT transactions (full records from operator file)
        discrepancy_commit_df.to_excel(writer, sheet_name="Commit_Discrepancies", index=False)
        
        # Sheet 5: REVERSAL SUCCESS — full records from operator file
        reversal_success_df.to_excel(writer, sheet_name="Reversal_Success", index=False)

        # Sheet 6: SP State-wise Summary
        sp_state_summary_df.to_excel(writer, sheet_name="SP_State_Summary", index=False)

        # Sheet 7: SP records with specific statuses (aml_review, on_hold, published, stuck, In process)
        sp_focus_df.to_excel(writer, sheet_name="SP_Stucks", index=False)

        # Sheet 8: SP remitted transactions that are NOT in operator COMMIT
        sp_remitted_discrepancy_df.to_excel(writer, sheet_name="SP_Remitted_Discrepancies", index=False)

    print("\nAll outputs saved to single Excel file:")
    print(excel_output_path)

    # Return key DataFrames for UI as well
    return {
        "operator_df": operator_df,
        "commit_df": commit_df,
        "rollback_df": rollback_df,
        "reversal_success_df": reversal_success_df,
        "sp_full_df": sp_full_df,
        "sp_state_distribution": state_distribution,
        "sp_focus_df": sp_focus_df,
        "reconciliation_df": result,
        "discrepancy_commit_df": discrepancy_commit_df,
        "sp_remitted_discrepancy_df": sp_remitted_discrepancy_df,
        "rollback_summary": rollback_summary,
        "problem_cases": problem_cases,
        "excel_output_path": excel_output_path,
    }


# =========================
# STREAMLIT FRONTEND UI (Dashboard)
# =========================

# Global CSS for pretty cards
st.markdown(
    """
    <style>
    .card {
        background: linear-gradient(135deg, #0f172a, #1f2937);
        border-radius: 16px;
        padding: 16px 20px;
        color: #e5e7eb;
        box-shadow: 0 10px 25px rgba(15,23,42,0.35);
    }
    .card-light {
        background: #ffffff;
        border-radius: 16px;
        padding: 16px 20px;
        color: #111827;
        box-shadow: 0 8px 20px rgba(0,0,0,0.08);
        border: 1px solid #e5e7eb;
    }
    .card-title {
        font-size: 14px;
        font-weight: 600;
        color: #9ca3af;
        margin-bottom: 4px;
        text-transform: uppercase;
        letter-spacing: .08em;
    }
    .card-value {
        font-size: 24px;
        font-weight: 700;
        margin-bottom: 4px;
    }
    .card-sub {
        font-size: 12px;
        color: #9ca3af;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# Beautiful header
st.markdown(
    """
    <div style="background: radial-gradient(circle at top left, #22c55e33, #0f172a 55%); 
                padding: 22px 26px; border-radius: 18px; margin-bottom: 20px; border:1px solid #1f2937;">
        <h1 style="color: #e5e7eb; margin: 0; font-size: 30px;">💸 Terapay Reconciliation Dashboard</h1>
        <p style="color: #9ca3af; margin: 6px 0 0 0; font-size: 14px;">
            Analyze Operator vs TerraPay SP data end-to-end: operator status, SP status, matching, and discrepancies,
            with a downloadable Excel report.
        </p>
    </div>
    """,
    unsafe_allow_html=True,
)

# File inputs
with st.container():
    col1, col2 = st.columns(2)
    with col1:
        st.subheader("1️⃣ SP File (TerraPay Logs)")
        sp_file = st.file_uploader(
            "Upload SP Logs (Excel)",
            type=["xlsx", "xls"],
            accept_multiple_files=False,
            help="Upload the SP Logs file (e.g., 'SP Logs 27-11-2025.xlsx').",
        )
    with col2:
        st.subheader("2️⃣ Operator File (Simpaisa Ledger)")
        operator_file = st.file_uploader(
            "Upload Operator Ledger CSV",
            type=["csv"],
            accept_multiple_files=False,
            help="Upload the simpaisa_ledger_daily CSV file.",
        )

st.markdown("---")

run_button = st.button("▶ Run Terapay Reconciliation", type="primary")

if run_button:
    if not sp_file:
        st.error("Please upload the **SP file**.")
    elif not operator_file:
        st.error("Please upload the **Operator CSV file**.")
    else:
        with st.spinner("Processing files and running reconciliation..."):
            # Save uploads to temp paths
            sp_temp_path = save_upload_to_temp(sp_file, ".xlsx")
            operator_temp_path = save_upload_to_temp(operator_file, ".csv")

            # Prepare output Excel path
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            excel_output_path = os.path.join(
                tempfile.gettempdir(),
                f"terapay_recon_results_{timestamp}.xlsx"
            )

            backend_outputs = terapay_reconciliation_backend(
                sp_temp_path,
                operator_temp_path,
                excel_output_path,
            )

            # For download
            final_path = backend_outputs["excel_output_path"]
            with open(final_path, "rb") as f:
                excel_bytes = f.read()

        st.success("✅ Reconciliation completed successfully!")

        # Extract DataFrames
        operator_df = backend_outputs["operator_df"]
        commit_df = backend_outputs["commit_df"]
        rollback_df = backend_outputs["rollback_df"]
        reversal_success_df = backend_outputs["reversal_success_df"]
        sp_full_df = backend_outputs["sp_full_df"]
        sp_state_distribution = backend_outputs["sp_state_distribution"]
        sp_focus_df = backend_outputs["sp_focus_df"]
        reconciliation_df = backend_outputs["reconciliation_df"]
        discrepancy_commit_df = backend_outputs["discrepancy_commit_df"]
        sp_remitted_discrepancy_df = backend_outputs["sp_remitted_discrepancy_df"]
        rollback_summary = backend_outputs["rollback_summary"]
        problem_cases = backend_outputs["problem_cases"]

        # =========================
        # Compute high-level metrics
        # =========================
        total_operator_records = len(operator_df)
        total_commit_records = len(commit_df)
        total_rollback_records = len(rollback_df)
        total_reversal_success_records = len(reversal_success_df)

        total_sp_records = len(sp_full_df)

        # SP states
        state_clean = sp_full_df["State"].astype(str).str.strip()
        state_clean_lower = state_clean.str.lower()
        total_sp_remitted = int((state_clean_lower == "remitted").sum())

        # Focus counts
        focus_statuses = ["aml_review", "on_hold", "published", "stuck", "in process"]
        focus_counts = {
            s: int((state_clean_lower == s).sum())
            for s in focus_statuses
        }

        matched_commit_in_sp = len(reconciliation_df)
        discrepancy_commit_count = len(discrepancy_commit_df)
        sp_remitted_discrepancy_count = len(sp_remitted_discrepancy_df)

        # =========================
        # TOP SUMMARY ROW
        # =========================
        st.markdown("## 🔍 High-Level Summary")

        col_summary1, col_summary2, col_summary3, col_summary4 = st.columns(4)

        with col_summary1:
            st.markdown(
                f"""
                <div class="card">
                    <div class="card-title">Total Operator Records</div>
                    <div class="card-value">{total_operator_records}</div>
                    <div class="card-sub">Rows in operator ledger (all statuses)</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        with col_summary2:
            st.markdown(
                f"""
                <div class="card">
                    <div class="card-title">Total SP Records</div>
                    <div class="card-value">{total_sp_records}</div>
                    <div class="card-sub">Rows in SP logs (all states)</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        with col_summary3:
            st.markdown(
                f"""
                <div class="card">
                    <div class="card-title">Matched COMMIT ↔ SP Remitted</div>
                    <div class="card-value">{matched_commit_in_sp}</div>
                    <div class="card-sub">COMMIT entries that successfully match SP remitted records</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        with col_summary4:
            st.markdown(
                f"""
                <div class="card">
                    <div class="card-title">SP Remitted Discrepancies</div>
                    <div class="card-value">{sp_remitted_discrepancy_count}</div>
                    <div class="card-sub">SP remitted records not found in operator COMMIT</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        # =========================
        # OPERATOR & SP CARDS
        # =========================
        st.markdown("## 🏦 Operator vs 🧾 SP Overview")

        col_left, col_right = st.columns(2)

        # Operator card group
        with col_left:
            st.markdown(
                """
                <div class="card-light">
                    <div class="card-title">Operator Summary</div>
                """,
                unsafe_allow_html=True,
            )
            c1, c2 = st.columns(2)
            with c1:
                st.metric("Total COMMIT", total_commit_records)
                st.metric("Total ROLLBACK", total_rollback_records)
            with c2:
                st.metric("Total REVERSAL SUCCESS", total_reversal_success_records)
                st.metric("COMMIT Discrepancies (Operator → SP)", discrepancy_commit_count)

            st.markdown(
                """
                    <div style="font-size:12px; color:#6b7280; margin-top:8px;">
                        • COMMIT discrepancies represent transactions present in Operator COMMIT 
                        but missing in SP remitted.<br>
                        • Zero indicates a clean mapping from Operator to SP side.
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        # SP card group
        with col_right:
            st.markdown(
                """
                <div class="card-light">
                    <div class="card-title">SP (TerraPay) Summary</div>
                """,
                unsafe_allow_html=True,
            )
            s1, s2, s3 = st.columns(3)
            with s1:
                st.metric("Total SP Records", total_sp_records)
            with s2:
                st.metric("Remitted", total_sp_remitted)
            with s3:
                st.metric("Distinct States", len(sp_state_distribution))

            # Focus statuses mini-cards
            st.markdown("<div style='margin-top:10px; font-size:13px; font-weight:600;'>Focus States</div>", unsafe_allow_html=True)
            f1, f2, f3, f4, f5 = st.columns(5)
            label_map = {
                "aml_review": "AML Review",
                "on_hold": "On Hold",
                "published": "Published",
                "stuck": "Stuck",
                "in process": "In Process",
            }
            cols = [f1, f2, f3, f4, f5]
            for col, key in zip(cols, focus_statuses):
                with col:
                    st.metric(label_map[key], focus_counts.get(key, 0))

            st.markdown(
                """
                    <div style="font-size:12px; color:#6b7280; margin-top:8px;">
                        • These counts show SP rows in critical or non-final states.<br>
                        • Use the detailed tabs below to inspect them transaction-wise.
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        # =========================
        # MATCHING & DISCREPANCIES SECTION
        # =========================
        st.markdown("## ⚖️ Matching & Discrepancies")

        col_m1, col_m2 = st.columns(2)
        with col_m1:
            st.markdown(
                f"""
                <div class="card-light">
                    <div class="card-title">Matching Summary</div>
                    <div class="card-value">{matched_commit_in_sp}</div>
                    <div class="card-sub">
                        COMMIT records with a matching SP remitted (based on Transaction Id ↔ Integration Id).
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        with col_m2:
            st.markdown(
                f"""
                <div class="card-light">
                    <div class="card-title">Discrepancy Summary</div>
                    <div style="display:flex; gap:16px; margin-top:6px;">
                        <div>
                            <div style="font-size:12px; color:#6b7280;">Operator → SP</div>
                            <div style="font-size:20px; font-weight:700;">{discrepancy_commit_count}</div>
                            <div style="font-size:11px; color:#9ca3af;">COMMIT present in Operator, missing in SP remit</div>
                        </div>
                        <div>
                            <div style="font-size:12px; color:#6b7280;">SP → Operator</div>
                            <div style="font-size:20px; font-weight:700;">{sp_remitted_discrepancy_count}</div>
                            <div style="font-size:11px; color:#9ca3af;">Remitted in SP, missing in Operator COMMIT</div>
                        </div>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        # =========================
        # TABS WITH DETAILS
        # =========================
        st.markdown("## 📑 Detailed Views")

        tab1, tab2, tab3, tab4, tab5 = st.tabs(
            [
                "Reconciliation (Sample)",
                "SP State Summary",
                "SP Focus Records",
                "SP Remitted Discrepancies",
                "Rollback Analysis",
            ]
        )

        with tab1:
            st.markdown("#### 🧾 COMMIT vs SP Remitted (Sample)")
            if not reconciliation_df.empty:
                st.dataframe(reconciliation_df.head(20), use_container_width=True)
            else:
                st.info("No matched COMMIT ↔ SP remitted records found.")

        with tab2:
            st.markdown("#### 📊 SP State-wise Distribution")
            sp_state_summary_df = sp_state_distribution.rename_axis("State").reset_index(name="Count")
            if not sp_state_summary_df.empty:
                st.dataframe(sp_state_summary_df, use_container_width=True)
            else:
                st.info("No SP records to summarize.")

        with tab3:
            st.markdown("#### ⚠ SP Focus Status Records")
            st.caption("Includes states: aml_review, on_hold, published, stuck, in process")
            if not sp_focus_df.empty:
                st.dataframe(sp_focus_df, use_container_width=True)
            else:
                st.info("No SP records found with focus statuses.")

        with tab4:
            st.markdown("#### 🧯 SP Remitted Discrepancy Records")
            st.caption("Remitted in SP but not found in Operator COMMIT (full SP row).")
            if not sp_remitted_discrepancy_df.empty:
                st.dataframe(sp_remitted_discrepancy_df, use_container_width=True)
            else:
                st.success("No SP remitted discrepancies detected. All remitted SP records are present in COMMIT.")

        with tab5:
            st.markdown("#### 🔁 Rollback Summary")
            if not rollback_summary.empty:
                st.dataframe(rollback_summary.head(50), use_container_width=True)
            else:
                st.info("No ROLLBACK records in the operator file.")

            st.markdown("#### ⚠ Problematic Rollback Entries")
            if not problem_cases.empty:
                st.dataframe(problem_cases.head(50), use_container_width=True)
            else:
                st.success("No problematic rollback entries detected. DR and CR appear balanced where present.")

        # =========================
        # DOWNLOAD SECTION
        # =========================
        st.markdown("## 📥 Download Reconciliation Report")

        st.download_button(
            label="Download Reconciliation Excel",
            data=excel_bytes,
            file_name=os.path.basename(final_path),
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

        st.info(
            "The Excel file includes sheets: **Reconciliation**, **Rollback_Summary**, "
            "**Rollback_Problem_Cases**, **Commit_Discrepancies**, **Reversal_Success**, "
            "**SP_State_Summary**, **SP_Stucks**, and **SP_Remitted_Discrepancies**."
        )

# Optional footer
st.markdown(
    """
    <div style="text-align: center; color: #9ca3af; font-size: 12px; margin-top: 30px;">
        Terapay Reconciliation • Built with ❤️ using Streamlit
    </div>
    """,
    unsafe_allow_html=True,
)