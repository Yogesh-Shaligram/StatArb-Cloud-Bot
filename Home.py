import streamlit as st

# --- PAGE CONFIGURATION ---
st.set_page_config(
    page_title="Quantitative Trading Desk",
    layout="wide",
    page_icon="🏛️" # You can remove this icon too if you prefer a default browser tab!
)

# --- HEADER ---
st.title("Welcome to the Quantitative Trading Desk")
st.markdown("---")

st.markdown("""
### Overview
This portal manages two fully autonomous statistical arbitrage engines operating across distinct asset classes. 
Use the sidebar on the left to navigate between the active trading desks.

#### 1. Indian Equities Desk (`1_Indian_Equities.py`)
* **Market:** National Stock Exchange (NSE)
* **Strategy:** Mean-Reversion Long-Only Proxy
* **Execution:** Trades whole units based on INR capital.
* **Operations:** Active only during standard IST market hours (09:15 - 15:30).

#### 2. Global Crypto Desk (`2_Crypto_Quant.py`)
* **Market:** Global Cryptocurrency Spot Markets
* **Strategy:** Market-Neutral Long/Short Hedging
* **Execution:** Fractional sizing based on USD capital.
* **Operations:** 24/7/365 Continuous Execution.

---
**Infrastructure Note:** Both engines utilize Google Sheets as an ephemeral state bypass, ensuring zero data loss during server reboots or automated deployments.
""")

# --- OPTIONAL: GLOBAL METRICS PLACEHOLDER ---
st.info("👈 Select a trading desk from the sidebar to view live charts and active positions.")