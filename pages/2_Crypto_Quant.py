import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import statsmodels.api as sm
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import time
from datetime import datetime
import pytz
import warnings
import json
import gspread
from google.oauth2.service_account import Credentials

warnings.filterwarnings("ignore")

st.set_page_config(page_title="24/7 Crypto Quant", layout="wide", page_icon="🪙")

# ---------------------------------------------------------
# 1. CONSTANTS & WATCHLIST
# ---------------------------------------------------------
SHEET_ID = "1Xlf5f1cH0jYSnDweXQmj7tuuuKrPRkA9xlZm4wl8ZWs"
ENTRY_Z = 1.15
EXIT_Z = 0.0
LEG_ALLOCATION = 25000.0

PAIRS = [
    ('BTC-USD', 'ETH-USD'), ('SOL-USD', 'AVAX-USD'),
    ('LINK-USD', 'AAVE-USD'), ('DOGE-USD', 'SHIB-USD'),
    ('ADA-USD', 'DOT-USD'), ('LTC-USD', 'BCH-USD'),
    ('HBAR-USD', 'ALGO-USD'), ('XRP-USD', 'XLM-USD')
]

ALL_TICKERS = list(set([ticker for pair in PAIRS for ticker in pair]))


# ---------------------------------------------------------
# 2. GOOGLE SHEETS CLOUD STORAGE (Wrapped in Try/Except)
# ---------------------------------------------------------
@st.cache_resource
def get_gspread_client():
    try:
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive"
        ]
        raw_json = st.secrets["google_credentials_json"]
        creds_dict = json.loads(raw_json)
        creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        return gspread.authorize(creds)
    except Exception as e:
        st.error(f"🚨 API Vault Error: Could not authenticate with Google Cloud. Details: {e}")
        return None


def get_worksheets(client):
    try:
        if client:
            sheet = client.open_by_key(SHEET_ID)
            return sheet.worksheet("Crypto_State"), sheet.worksheet("Crypto_Ledger")
    except Exception as e:
        st.error(f"🚨 Database Error: Could not open the Google Sheet. Details: {e}")
    return None, None


# Initialize Database Connections Safely
db_client = get_gspread_client()
state_tab, ledger_tab = get_worksheets(db_client)


def load_cloud_state():
    # 1. Set safe empty defaults first so the app never crashes
    st.session_state.crypto_portfolio = 1000.0
    st.session_state.crypto_states = {
        pair: {'position': 0, 'units_1': 0.0, 'entry_p1': 0.0, 'units_2': 0.0, 'entry_p2': 0.0}
        for pair in PAIRS
    }
    st.session_state.crypto_trade_log = []

    if not state_tab or not ledger_tab:
        st.warning("⚠️ Running in offline/read-only mode. Database connection failed.")
        return

    # 2. Try to pull existing memory
    try:
        raw_data = state_tab.acell('A1').value
        if raw_data:
            state_data = json.loads(raw_data)
            st.session_state.crypto_portfolio = state_data.get('portfolio', 1000.0)
            loaded_states = {}
            for k, v in state_data.get('states', {}).items():
                a1, a2 = k.split('|')
                loaded_states[(a1, a2)] = v
            # Prevent KeyError if new pairs were added to the watchlist
            for pair in PAIRS:
                if pair in loaded_states:
                    st.session_state.crypto_states[pair] = loaded_states[pair]
    except Exception as e:
        st.warning(f"⚠️ Could not load state from Cloud. Starting fresh. Details: {e}")

    try:
        st.session_state.crypto_trade_log = ledger_tab.get_all_records()
    except Exception as e:
        st.warning(f"⚠️ Could not load trade ledger from Cloud. Details: {e}")


def save_cloud_state():
    if not state_tab: return
    try:
        str_states = {f"{k[0]}|{k[1]}": v for k, v in st.session_state.crypto_states.items()}
        state_data = {'portfolio': st.session_state.crypto_portfolio, 'states': str_states}
        state_tab.update_acell('A1', json.dumps(state_data))
    except Exception as e:
        st.error(f"⚠️ Failed to save state to Google Sheets! Details: {e}")


def append_to_cloud_ledger(trade_dict):
    if not ledger_tab: return
    try:
        row_data = [
            trade_dict['Time'], trade_dict['Pair'], trade_dict['Asset'],
            trade_dict['Action'], trade_dict['Price'], trade_dict['Qty'], trade_dict['P&L']
        ]
        ledger_tab.append_row(row_data)
    except Exception as e:
        st.error(f"⚠️ Failed to log trade to Google Sheets ledger! Details: {e}")


# ---------------------------------------------------------
# 3. ROBUST DATA PIPELINE (yFinance Isolations)
# ---------------------------------------------------------
@st.cache_data(ttl=3600)  # Automatically wipes cache every hour to prevent ghost data
def calibrate_pairs_v2(current_pairs, current_tickers):
    try:
        hist_data = yf.download(current_tickers, period="6mo", progress=False)['Close'].ffill()
    except Exception as e:
        st.error(f"🚨 Failed to download historical data for calibration. Details: {e}")
        return {}

    calibrated = {}
    for a1, a2 in current_pairs:
        try:
            pair_data = hist_data[[a1, a2]].dropna()
            if len(pair_data) > 50:
                model = sm.OLS(pair_data[a1], pair_data[a2]).fit()
                calibrated[(a1, a2)] = model.params.iloc[0]
            else:
                calibrated[(a1, a2)] = 1.0
        except Exception:
            calibrated[(a1, a2)] = 1.0  # Safety fallback
    return calibrated


def fetch_live_data(tickers):
    try:
        live_data = yf.download(tickers, period="40d", progress=False)['Close'].ffill()
        if live_data.empty:
            raise ValueError("Yahoo Finance returned an empty dataframe.")
        return live_data
    except Exception as e:
        st.error(f"🚨 Live Data API Error: Could not fetch current prices. Details: {e}")
        return None


def format_usd(number):
    return f"${number:,.2f}"


# ---------------------------------------------------------
# 4. MAIN EXECUTION LOOP (Blast Radius Containment)
# ---------------------------------------------------------
def main():
    if 'crypto_portfolio' not in st.session_state:
        load_cloud_state()

    calibrated_pairs = calibrate_pairs_v2(PAIRS, ALL_TICKERS)

    if not calibrated_pairs:
        st.stop()  # Gracefully halt UI if calibration completely fails

    utc_now = datetime.now(pytz.utc)
    timestamp = utc_now.strftime("%Y-%m-%d %H:%M:%S UTC")

    # Live Data Fetch with Retry Logic
    live_data = fetch_live_data(ALL_TICKERS)
    if live_data is None:
        st.warning("⏳ Market data feed down. Retrying in 60 seconds...")
        time.sleep(60)
        st.rerun()

    with st.sidebar:
        st.header("⚡ 24/7 Crypto Hedge Fund")
        st.success("🟢 LIVE - Market Neutral Mode")
        st.markdown("---")

    st.title("🪙 Multi-Asset Cloud Quant Dashboard")
    st.caption(f"Last updated: {timestamp} | Executing Long/Short Hedges")

    col1, col2, col3 = st.columns(3)
    col1.metric("Cloud Capital (USD)", format_usd(st.session_state.crypto_portfolio))
    col2.metric("Active Hedges", sum(1 for state in st.session_state.crypto_states.values() if state['position'] != 0))
    col3.metric("Total Completed Trades", len([t for t in st.session_state.crypto_trade_log if t['Action'] == 'EXIT']))

    st.markdown("---")

    dynamic_titles = []
    for a1, a2 in PAIRS:
        state = st.session_state.crypto_states.get((a1, a2), {'position': 0})
        base_title = f"{a1.replace('-USD', '')} / {a2.replace('-USD', '')}"
        if state['position'] == 1:
            base_title += f" | [LONG {a1.replace('-USD', '')} / SHORT {a2.replace('-USD', '')}]"
        elif state['position'] == 2:
            base_title += f" | [SHORT {a1.replace('-USD', '')} / LONG {a2.replace('-USD', '')}]"
        dynamic_titles.append(base_title)

    fig = make_subplots(rows=2, cols=4, subplot_titles=dynamic_titles)
    alerts = []
    row, col = 1, 1
    state_changed = False

    for asset1, asset2 in PAIRS:
        # --- BLAST RADIUS CONTAINMENT ---
        # Wrapping individual pairs in a try/except so one bad coin doesn't kill the loop
        try:
            if (asset1, asset2) not in calibrated_pairs:
                continue

            ratio = calibrated_pairs[(asset1, asset2)]

            if asset1 not in live_data.columns or asset2 not in live_data.columns:
                st.warning(f"⚠️ Live data missing for {asset1} or {asset2}. Skipping...")
                continue

            spread_series = live_data[asset1] - (ratio * live_data[asset2])
            sma_series = spread_series.rolling(window=20).mean()
            std_series = spread_series.rolling(window=20).std().replace(0, np.nan)
            z_score_series = ((spread_series - sma_series) / std_series).dropna()

            if not z_score_series.empty:
                current_z = float(z_score_series.iloc[-1])
                live_p1 = float(live_data[asset1].iloc[-1])
                live_p2 = float(live_data[asset2].iloc[-1])

                # Check for corrupted math (NaNs)
                if np.isnan(current_z) or np.isnan(live_p1) or np.isnan(live_p2):
                    continue

                pair_state = st.session_state.crypto_states.get((asset1, asset2),
                                                                {'position': 0, 'units_1': 0.0, 'entry_p1': 0.0,
                                                                 'units_2': 0.0, 'entry_p2': 0.0})
                short_name1, short_name2 = asset1.replace('-USD', ''), asset2.replace('-USD', '')
                print(
                    f"[{timestamp}] 🔎 SCAN: {short_name1}/{short_name2} | Z: {current_z:.2f} | {short_name1}: {format_usd(live_p1)} | {short_name2}: {format_usd(live_p2)}")

                if pair_state['position'] == 0:
                    if current_z < -ENTRY_Z:
                        units_1 = round(LEG_ALLOCATION / live_p1, 5)
                        units_2 = round(LEG_ALLOCATION / live_p2, 5)
                        cost = (units_1 * live_p1) + (units_2 * live_p2)

                        if st.session_state.crypto_portfolio >= cost:
                            st.session_state.crypto_portfolio -= cost
                            st.session_state.crypto_states[(asset1, asset2)] = {
                                'position': 1, 'units_1': units_1, 'entry_p1': live_p1, 'units_2': units_2,
                                'entry_p2': live_p2
                            }

                            trade_dict = {'Time': timestamp, 'Pair': f"{short_name1}/{short_name2}",
                                          'Asset': "LONG A1 / SHORT A2", 'Action': 'ENTER',
                                          'Price': f"{live_p1:.2f}/{live_p2:.2f}", 'Qty': f"{units_1}/{units_2}",
                                          'P&L': 0.0}
                            st.session_state.crypto_trade_log.append(trade_dict)
                            append_to_cloud_ledger(trade_dict)
                            alerts.append(f"🚨 ENTERED HEDGE: Long {short_name1} / Short {short_name2}")
                            state_changed = True

                    elif current_z > ENTRY_Z:
                        units_1 = round(LEG_ALLOCATION / live_p1, 5)
                        units_2 = round(LEG_ALLOCATION / live_p2, 5)
                        cost = (units_1 * live_p1) + (units_2 * live_p2)

                        if st.session_state.crypto_portfolio >= cost:
                            st.session_state.crypto_portfolio -= cost
                            st.session_state.crypto_states[(asset1, asset2)] = {
                                'position': 2, 'units_1': units_1, 'entry_p1': live_p1, 'units_2': units_2,
                                'entry_p2': live_p2
                            }

                            trade_dict = {'Time': timestamp, 'Pair': f"{short_name1}/{short_name2}",
                                          'Asset': "SHORT A1 / LONG A2", 'Action': 'ENTER',
                                          'Price': f"{live_p1:.2f}/{live_p2:.2f}", 'Qty': f"{units_1}/{units_2}",
                                          'P&L': 0.0}
                            st.session_state.crypto_trade_log.append(trade_dict)
                            append_to_cloud_ledger(trade_dict)
                            alerts.append(f"🚨 ENTERED HEDGE: Short {short_name1} / Long {short_name2}")
                            state_changed = True

                elif pair_state['position'] == 1:
                    if current_z > EXIT_Z:
                        profit_1 = (live_p1 - pair_state['entry_p1']) * pair_state['units_1']
                        profit_2 = (pair_state['entry_p2'] - live_p2) * pair_state['units_2']
                        total_profit = profit_1 + profit_2

                        st.session_state.crypto_portfolio += (pair_state['units_1'] * pair_state['entry_p1']) + (
                                pair_state['units_2'] * pair_state['entry_p2']) + total_profit

                        trade_dict = {'Time': timestamp, 'Pair': f"{short_name1}/{short_name2}",
                                      'Asset': "CLOSED HEDGE",
                                      'Action': 'EXIT', 'Price': f"{live_p1:.2f}/{live_p2:.2f}", 'Qty': "-",
                                      'P&L': round(total_profit, 2)}
                        st.session_state.crypto_trade_log.append(trade_dict)
                        append_to_cloud_ledger(trade_dict)

                        st.session_state.crypto_states[(asset1, asset2)] = {'position': 0, 'units_1': 0.0,
                                                                            'entry_p1': 0.0,
                                                                            'units_2': 0.0, 'entry_p2': 0.0}
                        alerts.append(
                            f"🔔 CLOSED HEDGE {short_name1}/{short_name2} | Profit: {format_usd(total_profit)}")
                        state_changed = True

                elif pair_state['position'] == 2:
                    if current_z < EXIT_Z:
                        profit_1 = (pair_state['entry_p1'] - live_p1) * pair_state['units_1']
                        profit_2 = (live_p2 - pair_state['entry_p2']) * pair_state['units_2']
                        total_profit = profit_1 + profit_2

                        st.session_state.crypto_portfolio += (pair_state['units_1'] * pair_state['entry_p1']) + (
                                pair_state['units_2'] * pair_state['entry_p2']) + total_profit

                        trade_dict = {'Time': timestamp, 'Pair': f"{short_name1}/{short_name2}",
                                      'Asset': "CLOSED HEDGE",
                                      'Action': 'EXIT', 'Price': f"{live_p1:.2f}/{live_p2:.2f}", 'Qty': "-",
                                      'P&L': round(total_profit, 2)}
                        st.session_state.crypto_trade_log.append(trade_dict)
                        append_to_cloud_ledger(trade_dict)

                        st.session_state.crypto_states[(asset1, asset2)] = {'position': 0, 'units_1': 0.0,
                                                                            'entry_p1': 0.0,
                                                                            'units_2': 0.0, 'entry_p2': 0.0}
                        alerts.append(
                            f"🔔 CLOSED HEDGE {short_name1}/{short_name2} | Profit: {format_usd(total_profit)}")
                        state_changed = True

            # --- PLOTTING ---
            plot_data = z_score_series.tail(20)
            line_color = 'rgba(0, 200, 0, 1)' if st.session_state.crypto_states.get((asset1, asset2), {}).get(
                'position', 0) != 0 else 'rgba(0, 0, 255, 0.7)'

            fig.add_trace(
                go.Scatter(x=plot_data.index, y=plot_data.values, mode='lines', line=dict(color=line_color, width=2),
                           showlegend=False), row=row, col=col)
            fig.add_hline(y=ENTRY_Z, line_dash="dash", line_color="red", line_width=1, row=row, col=col)
            fig.add_hline(y=-ENTRY_Z, line_dash="dash", line_color="red", line_width=1, row=row, col=col)
            fig.add_hline(y=EXIT_Z, line_dash="dot", line_color="gray", line_width=1, row=row, col=col)
            fig.update_yaxes(range=[-3.5, 3.5], row=row, col=col)
            fig.update_xaxes(showticklabels=False, row=row, col=col)

        except Exception as e:
            # If a single pair math fails, show a silent warning but DO NOT crash the app
            st.warning(f"⚠️ Calculation error on {asset1}/{asset2}. Skipping this cycle. Details: {e}")

        # Grid Logic
        col += 1
        if col > 4:
            col = 1
            row += 1

    if state_changed:
        save_cloud_state()

    fig.update_layout(height=500, margin=dict(l=20, r=20, t=40, b=20), plot_bgcolor='rgba(240,240,240,0.5)')
    st.plotly_chart(fig, width='stretch')

    if alerts:
        for alert in alerts:
            if "ENTERED" in alert:
                st.warning(alert)
            else:
                st.success(alert)

    time.sleep(60)
    st.rerun()


# ---------------------------------------------------------
# 5. THE GLOBAL SAFETY NET
# ---------------------------------------------------------
if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # If the absolute worst happens, catch it, display the error gracefully, and retry in 60s
        st.error(f"🚨 FATAL APPLICATION ERROR: {e}")
        time.sleep(60)
        st.rerun()