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
# 1. GOOGLE SHEETS CLOUD STORAGE (Targeting Crypto Tabs)
# ---------------------------------------------------------
scopes = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]


@st.cache_resource
def get_gspread_client():
    raw_json = st.secrets["google_credentials_json"]
    creds_dict = json.loads(raw_json)
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    return gspread.authorize(creds)


# REPLACE WITH YOUR ACTUAL SHEET ID
SHEET_ID = "YOUR_SPREADSHEET_ID_HERE"
client = get_gspread_client()
sheet = client.open_by_key(SHEET_ID)

# POINTING TO THE NEW CRYPTO TABS
state_tab = sheet.worksheet("Crypto_State")
ledger_tab = sheet.worksheet("Crypto_Ledger")


def load_cloud_state():
    try:
        raw_data = state_tab.acell('A1').value
        if raw_data:
            state_data = json.loads(raw_data)
            st.session_state.crypto_portfolio = state_data.get('portfolio', 1000000.0)
            loaded_states = {}
            for k, v in state_data.get('states', {}).items():
                a1, a2 = k.split('|')
                loaded_states[(a1, a2)] = v
            st.session_state.crypto_states = loaded_states
        else:
            raise ValueError("Empty cell")
    except Exception:
        st.session_state.crypto_portfolio = 1000000.0  # $1,000,000 USD
        st.session_state.crypto_states = {}

    try:
        st.session_state.crypto_trade_log = ledger_tab.get_all_records()
    except Exception:
        st.session_state.crypto_trade_log = []


def save_cloud_state():
    str_states = {f"{k[0]}|{k[1]}": v for k, v in st.session_state.crypto_states.items()}
    state_data = {'portfolio': st.session_state.crypto_portfolio, 'states': str_states}
    state_tab.update_acell('A1', json.dumps(state_data))


def append_to_cloud_ledger(trade_dict):
    row_data = [
        trade_dict['Time'], trade_dict['Pair'], trade_dict['Asset'],
        trade_dict['Action'], trade_dict['Price'], trade_dict['Qty'], trade_dict['P&L']
    ]
    ledger_tab.append_row(row_data)


# ---------------------------------------------------------
# 2. WATCHLIST & ADVANCED L/S CONSTANTS
# ---------------------------------------------------------
pairs = [
    ('BTC-USD', 'ETH-USD'), ('SOL-USD', 'AVAX-USD'),
    ('UNI-USD', 'AAVE-USD'), ('DOGE-USD', 'SHIB-USD'),
    ('ADA-USD', 'DOT-USD'), ('LTC-USD', 'BCH-USD'),
    ('LINK-USD', 'UNI-USD'), ('MATIC-USD', 'ARB-USD')
]

all_tickers = list(set([ticker for pair in pairs for ticker in pair]))

ENTRY_Z = 1.75
EXIT_Z = 0.0
# We allocate $50k total per setup ($25k for the Long leg, $25k for the Short leg)
LEG_ALLOCATION = 25000.0

if 'crypto_portfolio' not in st.session_state:
    load_cloud_state()
    if not st.session_state.crypto_states:
        # Notice the state now tracks TWO assets per pair
        st.session_state.crypto_states = {
            pair: {'position': 0, 'units_1': 0.0, 'entry_p1': 0.0, 'units_2': 0.0, 'entry_p2': 0.0}
            for pair in pairs
        }


# ---------------------------------------------------------
# 3. LIVE DATA & UI
# ---------------------------------------------------------
@st.cache_data
def calibrate_pairs():
    hist_data = yf.download(all_tickers, period="6mo", progress=False)['Close'].ffill().dropna()
    calibrated = {}
    for a1, a2 in pairs:
        model = sm.OLS(hist_data[a1], hist_data[a2]).fit()
        calibrated[(a1, a2)] = model.params.iloc[0]
    return calibrated


calibrated_pairs = calibrate_pairs()

utc_now = datetime.now(pytz.utc)
timestamp = utc_now.strftime("%Y-%m-%d %H:%M:%S UTC")
live_data = yf.download(all_tickers, period="40d", progress=False)['Close'].ffill()


def format_usd(number):
    return f"${number:,.2f}"


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

# ---------------------------------------------------------
# 4. LONG/SHORT NEUTRAL TRADING LOGIC
# ---------------------------------------------------------
dynamic_titles = []
for a1, a2 in pairs:
    state = st.session_state.crypto_states[(a1, a2)]
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

for asset1, asset2 in pairs:
    ratio = calibrated_pairs[(asset1, asset2)]
    spread_series = live_data[asset1] - (ratio * live_data[asset2])
    sma_series = spread_series.rolling(window=20).mean()
    std_series = spread_series.rolling(window=20).std().replace(0, np.nan)
    z_score_series = ((spread_series - sma_series) / std_series).dropna()

    if not z_score_series.empty:
        current_z = z_score_series.iloc[-1]
        live_p1 = float(live_data[asset1].iloc[-1])
        live_p2 = float(live_data[asset2].iloc[-1])

        pair_state = st.session_state.crypto_states[(asset1, asset2)]
        short_name1, short_name2 = asset1.replace('-USD', ''), asset2.replace('-USD', '')

        if pair_state['position'] == 0:
            if current_z < -ENTRY_Z:
                # ENTRY 1: Asset 1 is undervalued. BUY Asset 1, SHORT Asset 2
                units_1 = round(LEG_ALLOCATION / live_p1, 5)
                units_2 = round(LEG_ALLOCATION / live_p2, 5)
                cost = (units_1 * live_p1) + (units_2 * live_p2)

                if st.session_state.crypto_portfolio >= cost:
                    st.session_state.crypto_portfolio -= cost
                    st.session_state.crypto_states[(asset1, asset2)] = {
                        'position': 1, 'units_1': units_1, 'entry_p1': live_p1, 'units_2': units_2, 'entry_p2': live_p2
                    }

                    trade_dict = {'Time': timestamp, 'Pair': f"{short_name1}/{short_name2}",
                                  'Asset': "LONG A1 / SHORT A2", 'Action': 'ENTER',
                                  'Price': f"{live_p1:.2f}/{live_p2:.2f}", 'Qty': f"{units_1}/{units_2}", 'P&L': 0.0}
                    st.session_state.crypto_trade_log.append(trade_dict)
                    append_to_cloud_ledger(trade_dict)
                    alerts.append(f"🚨 ENTERED HEDGE: Long {short_name1} / Short {short_name2}")
                    state_changed = True

            elif current_z > ENTRY_Z:
                # ENTRY 2: Asset 1 is overvalued. SHORT Asset 1, BUY Asset 2
                units_1 = round(LEG_ALLOCATION / live_p1, 5)
                units_2 = round(LEG_ALLOCATION / live_p2, 5)
                cost = (units_1 * live_p1) + (units_2 * live_p2)

                if st.session_state.crypto_portfolio >= cost:
                    st.session_state.crypto_portfolio -= cost
                    st.session_state.crypto_states[(asset1, asset2)] = {
                        'position': 2, 'units_1': units_1, 'entry_p1': live_p1, 'units_2': units_2, 'entry_p2': live_p2
                    }

                    trade_dict = {'Time': timestamp, 'Pair': f"{short_name1}/{short_name2}",
                                  'Asset': "SHORT A1 / LONG A2", 'Action': 'ENTER',
                                  'Price': f"{live_p1:.2f}/{live_p2:.2f}", 'Qty': f"{units_1}/{units_2}", 'P&L': 0.0}
                    st.session_state.crypto_trade_log.append(trade_dict)
                    append_to_cloud_ledger(trade_dict)
                    alerts.append(f"🚨 ENTERED HEDGE: Short {short_name1} / Long {short_name2}")
                    state_changed = True

        elif pair_state['position'] == 1:
            if current_z > EXIT_Z:
                # EXITED HEDGE 1
                profit_1 = (live_p1 - pair_state['entry_p1']) * pair_state['units_1']  # Long Profit
                profit_2 = (pair_state['entry_p2'] - live_p2) * pair_state['units_2']  # Short Profit
                total_profit = profit_1 + profit_2

                st.session_state.crypto_portfolio += (pair_state['units_1'] * pair_state['entry_p1']) + (
                            pair_state['units_2'] * pair_state['entry_p2']) + total_profit

                trade_dict = {'Time': timestamp, 'Pair': f"{short_name1}/{short_name2}", 'Asset': "CLOSED HEDGE",
                              'Action': 'EXIT', 'Price': f"{live_p1:.2f}/{live_p2:.2f}", 'Qty': "-",
                              'P&L': round(total_profit, 2)}
                st.session_state.crypto_trade_log.append(trade_dict)
                append_to_cloud_ledger(trade_dict)

                st.session_state.crypto_states[(asset1, asset2)] = {'position': 0, 'units_1': 0.0, 'entry_p1': 0.0,
                                                                    'units_2': 0.0, 'entry_p2': 0.0}
                alerts.append(f"🔔 CLOSED HEDGE {short_name1}/{short_name2} | Profit: {format_usd(total_profit)}")
                state_changed = True

        elif pair_state['position'] == 2:
            if current_z < EXIT_Z:
                # EXITED HEDGE 2
                profit_1 = (pair_state['entry_p1'] - live_p1) * pair_state['units_1']  # Short Profit
                profit_2 = (live_p2 - pair_state['entry_p2']) * pair_state['units_2']  # Long Profit
                total_profit = profit_1 + profit_2

                st.session_state.crypto_portfolio += (pair_state['units_1'] * pair_state['entry_p1']) + (
                            pair_state['units_2'] * pair_state['entry_p2']) + total_profit

                trade_dict = {'Time': timestamp, 'Pair': f"{short_name1}/{short_name2}", 'Asset': "CLOSED HEDGE",
                              'Action': 'EXIT', 'Price': f"{live_p1:.2f}/{live_p2:.2f}", 'Qty': "-",
                              'P&L': round(total_profit, 2)}
                st.session_state.crypto_trade_log.append(trade_dict)
                append_to_cloud_ledger(trade_dict)

                st.session_state.crypto_states[(asset1, asset2)] = {'position': 0, 'units_1': 0.0, 'entry_p1': 0.0,
                                                                    'units_2': 0.0, 'entry_p2': 0.0}
                alerts.append(f"🔔 CLOSED HEDGE {short_name1}/{short_name2} | Profit: {format_usd(total_profit)}")
                state_changed = True

        # --- PLOTTING ---
        plot_data = z_score_series.tail(20)
        line_color = 'rgba(0, 200, 0, 1)' if st.session_state.crypto_states[(asset1, asset2)][
                                                 'position'] != 0 else 'rgba(0, 0, 255, 0.7)'

        fig.add_trace(
            go.Scatter(x=plot_data.index, y=plot_data.values, mode='lines', line=dict(color=line_color, width=2),
                       showlegend=False), row=row, col=col)
        fig.add_hline(y=ENTRY_Z, line_dash="dash", line_color="red", line_width=1, row=row, col=col)
        fig.add_hline(y=-ENTRY_Z, line_dash="dash", line_color="red", line_width=1, row=row, col=col)
        fig.add_hline(y=EXIT_Z, line_dash="dot", line_color="gray", line_width=1, row=row, col=col)
        fig.update_yaxes(range=[-3.5, 3.5], row=row, col=col)
        fig.update_xaxes(showticklabels=False, row=row, col=col)

    col += 1
    if col > 4:
        col = 1
        row += 1

if state_changed:
    save_cloud_state()

fig.update_layout(height=500, margin=dict(l=20, r=20, t=40, b=20), plot_bgcolor='rgba(240,240,240,0.5)')
st.plotly_chart(fig, use_container_width=True)

if alerts:
    for alert in alerts:
        if "ENTERED" in alert:
            st.warning(alert)
        else:
            st.success(alert)

time.sleep(60)
st.rerun()