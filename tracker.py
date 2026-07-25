import os
import json
import math
import requests
import reverse_geocode as rg
from datetime import datetime, timezone

# ==========================================
# CONFIGURATION & CREDENTIALS
# ==========================================
OPENSKY_CLIENT_ID = "kenhunziker-api-client"
OPENSKY_CLIENT_SECRET = "bwj0ZSMBvZEb54QcG9Yf5A8h432OUCKr"

# Target ICAO Hex Codes (Uncomment one to track)
TARGET_ICAO = "a4b420"
# TARGET_ICAO = "a05598"
# TARGET_ICAO = "acb824"

DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/1530246683223396593/nBCv3avox9UetYvb31WnsYlfPKV8Q4QQTQeym2ixTfCGo4BNjBYcxznf9Nx7YD-4XpTQ"

# Location Settings (KSEA Airport Reference / Geofence Center)
KSEA_LAT = 47.4489
KSEA_LON = -122.3094
GEOFENCE_RADIUS_MILES = 30.0

# Alerting rules
HOURLY_HEARTBEAT_SECONDS = 3600  # 1 hour

STATE_FILE = "flight_state.json"


# ==========================================
# AUTHENTICATION HELPER
# ==========================================
def get_opensky_access_token():
    """Exchanges Client ID & Secret for an OAuth2 Bearer Token."""
    token_url = "https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token"
    payload = {
        "grant_type": "client_credentials",
        "client_id": OPENSKY_CLIENT_ID,
        "client_secret": OPENSKY_CLIENT_SECRET
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    
    try:
        response = requests.post(token_url, data=payload, headers=headers, timeout=10)
        if response.status_code == 200:
            return response.json().get("access_token")
        else:
            print(f"❌ OAuth Error {response.status_code}: Check Client ID / Secret.")
            return None
    except Exception as e:
        print(f"❌ Token Request Failed: {e}")
        return None


# ==========================================
# GEOGRAPHIC & CALCULATION HELPERS
# ==========================================
def calculate_haversine_distance(lat1, lon1, lat2, lon2):
    """Calculates distance between two lat/lon points in miles."""
    r = 3958.8  # Earth radius in miles
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = math.sin(delta_phi / 2)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return r * c


def get_cardinal_direction(degrees):
    """Converts heading degrees (0-360) into compass direction."""
    directions = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", 
                  "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
    idx = int((degrees + 11.25) / 22.5) % 16
    return directions[idx]


def get_location_description(lat, lon):
    """Returns a location anchored to the nearest US major city (pop >= 25,000)."""
    try:
        result = rg.get((lat, lon), min_population=25000)
    except Exception:
        return "Unknown location"

    if not result:
        return "Unknown location"
    
    city = result.get('city', 'Unknown')
    state = result.get('state', '')
    
    city_lat = float(result.get('latitude', lat))
    city_lon = float(result.get('longitude', lon))
    
    dist = calculate_haversine_distance(lat, lon, city_lat, city_lon)
    
    y = math.sin(math.radians(lon - city_lon)) * math.cos(math.radians(lat))
    x = math.cos(math.radians(city_lat)) * math.sin(math.radians(lat)) - \
        math.sin(math.radians(city_lat)) * math.cos(math.radians(lat)) * math.cos(math.radians(lon - city_lon))
    bearing = (math.degrees(math.atan2(y, x)) + 360) % 360
    direction = get_cardinal_direction(bearing)
    
    if dist < 1.0:
        return f"Over {city}, {state}"
    return f"{dist:.1f} mi {direction} of {city}, {state}"


# ==========================================
# STATE MANAGEMENT HELPERS
# ==========================================
def load_state():
    """Loads previous tracking state and timestamp from disk."""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {"status": "offline", "last_alert_time": 0}


def save_state(state):
    """Saves tracking state to disk."""
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


# ==========================================
# DISCORD NOTIFICATION SENDER
# ==========================================
def send_discord_alert(callsign, altitude_ft, speed_kts, heading_str, location_str, distance_to_ksea, current_status):
    """Formats and posts an embed alert to Discord."""
    
    title_prefix = "🚨 KSEA Proximity Alert" if current_status == "inside_geofence" else "✈️ Flight Tracking Update"
    
    embed = {
        "title": f"{title_prefix}: {callsign}",
        "color": 3447003 if current_status == "inside_geofence" else 15105570,  # Blue for Geofence, Orange for General
        "description": (
            f"**Status:** `{current_status.upper()}`\n"
            f"**Location:** {location_str}\n"
            f"**Distance to KSEA:** {distance_to_ksea:.1f} miles\n"
            f"**Heading:** {heading_str}\n"
            f"**Altitude:** {altitude_ft:,} ft\n"
            f"**Speed:** {speed_kts} kts\n"
            f"**Callsign:** {callsign}"
        ),
        "fields": [
            {
                "name": "Live Map Track",
                "value": f"[View on Flightradar24](https://www.flightradar24.com/{callsign})",
                "inline": False
            }
        ],
        "footer": {
            "text": "KSEA Flight Tracker"
        },
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    
    try:
        res = requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [embed]}, timeout=10)
        if res.status_code == 204:
            print("✅ Discord alert sent successfully!")
        else:
            print(f"⚠️ Discord Webhook returned status {res.status_code}")
    except Exception as e:
        print(f"❌ Failed to send Discord alert: {e}")


# ==========================================
# MAIN EXECUTION LOGIC
# ==========================================
def check_flight():
    print(f"Checking status for aircraft ICAO: {TARGET_ICAO}...")
    state = load_state()
    prev_status = state.get("status", "offline")
    last_alert_time = state.get("last_alert_time", 0)
    now_ts = datetime.now(timezone.utc).timestamp()
    
    # 1. Fetch OAuth2 Token
    token = get_opensky_access_token()
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    
    # 2. Query OpenSky API
    url = f"https://opensky-network.org/api/states/all?icao24={TARGET_ICAO}"
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code != 200:
            print(f"⚠️ API Error: Status Code {response.status_code}")
            return
        data = response.json()
    except Exception as e:
        print(f"❌ Network Request Failed: {e}")
        return

    states = data.get("states")
    
    # --- SCENARIO A: AIRCRAFT IS OFFLINE ---
    if not states:
        print("Aircraft is currently offline or inactive.")
        
        # If it was active previously and just landed/went offline, record status change
        if prev_status != "offline":
            print(f"🔄 State Change: {prev_status} -> offline")
            state["status"] = "offline"
            save_state(state)
        return

    # --- SCENARIO B: AIRCRAFT IS ONLINE ---
    flight = states[0]
    callsign = flight[1].strip() if flight[1] else "N/A"
    longitude, latitude = flight[5], flight[6]
    altitude_meters = flight[7]
    velocity_ms = flight[9]
    heading_deg = flight[10]
    
    if latitude is None or longitude is None:
        print(f"Active Flight: {callsign} | Position telemetry temporarily unavailable.")
        return

    # Calculations
    altitude_ft = int(altitude_meters * 3.28084) if altitude_meters is not None else 0
    speed_kts = int(velocity_ms * 1.94384) if velocity_ms is not None else 0
    heading_str = f"{int(round(heading_deg))}° ({get_cardinal_direction(heading_deg)})" if heading_deg is not None else "N/A"
    distance_to_ksea = calculate_haversine_distance(latitude, longitude, KSEA_LAT, KSEA_LON)
    location_str = get_location_description(latitude, longitude)

    # Determine status (Strictly 3-state system)
    if distance_to_ksea <= GEOFENCE_RADIUS_MILES:
        current_status = "inside_geofence"
    else:
        current_status = "in_flight"

    print(f"Active Flight: {callsign} | Status: {current_status} |Loc: {location_str} | Alt: {altitude_ft:,} ft | Speed: {speed_kts} kts | KSEA Dist: {distance_to_ksea:.1f} mi")

    # Evaluate Triggers: (1) State Changed OR (2) 1 Hour Elapsed
    state_changed = (current_status != prev_status)
    time_elapsed = now_ts - last_alert_time
    hourly_due = (time_elapsed >= HOURLY_HEARTBEAT_SECONDS)

    if state_changed or hourly_due:
        reason = "State Change" if state_changed else "1-Hour Heartbeat"
        print(f"🚀 Triggering Discord Alert (Reason: {reason})")
        
        send_discord_alert(callsign, altitude_ft, speed_kts, heading_str, location_str, distance_to_ksea, current_status)
        
        # Update persistent state
        state["status"] = current_status
        state["last_alert_time"] = now_ts
        save_state(state)
    else:
        mins_remaining = int((HOURLY_HEARTBEAT_SECONDS - time_elapsed) // 60)
        print(f"ℹ️ Status unchanged ({current_status}). Next heartbeat update in ~{mins_remaining} min.")


if __name__ == "__main__":
    check_flight()