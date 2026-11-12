import streamlit as st
import requests
import folium
import math
import re
from pathlib import Path
from streamlit_searchbox import st_searchbox
from streamlit_folium import st_folium

# ==========================================
# 1. RUNCOMFY AI SETTINGS
# ==========================================

st.set_page_config(
    page_title="RunComfy AI",
    page_icon="🏃",
    layout="wide"
)
# Load RunComfy AI design
css_file = Path(__file__).parent / "style.css"

with open(css_file, encoding="utf-8") as f:
    st.markdown(
        f"<style>{f.read()}</style>",
        unsafe_allow_html=True
    )

st.markdown("""
<div class="main-title">RunComfy AI</div>
<p class="subtitle">Find a comfortable running route in Switzerland.</p>
""", unsafe_allow_html=True)


# API keys from Streamlit Secrets
ORS_KEY = st.secrets.get("ORS_API_KEY")

if not ORS_KEY:
    st.error(
        "Missing API key. Please add ORS_API_KEY to your Streamlit secrets."
    )
    st.stop()

HEADERS = {
    "User-Agent": "RunComfySchoolProject/1.0 (contact: hello@runcomfy.app)"
}

# Scoring limits are unchanged. Warning thresholds are configurable in the UI.
DEFAULT_START = (47.43150,9.37475)
FITNESS_LIMITS = {"Beginner": 10, "Intermediate": 25, "Advanced": 45}
HILL_MULTIPLIERS = {"Flat": 0.5, "Moderate": 1, "Hilly": 2}


def number(value):
    """Return a finite number, keeping missing data distinct from zero."""
    if value is None or isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return value if math.isfinite(value) else None


def metric_text(value, unit="", decimals=1):
    value = number(value)
    return "Unavailable" if value is None else f"{value:.{decimals}f}{unit}"


def elevation_limit(fitness, hill_preference):
    return FITNESS_LIMITS[fitness] * HILL_MULTIPLIERS[hill_preference]


# Default location: St. Gallen
if "start" not in st.session_state:
    st.session_state.start = DEFAULT_START

if "start_source" not in st.session_state:
    st.session_state.start_source = (
        "default" if tuple(st.session_state.start) == DEFAULT_START else "selected"
    )
if "start_label" not in st.session_state:
    st.session_state.start_label = (
        "St. Gallen" if st.session_state.start_source == "default"
        else "Previously selected location"
    )

if "routes" not in st.session_state:
    st.session_state.routes = []


# ==========================================
# 2. SWISS LOCATION SEARCH
# ==========================================

@st.cache_data(ttl=86400)
def search_locations(address):
    address = address.strip()
    if len(address) < 2:
        return []

    response = requests.get(
        "https://api3.geo.admin.ch/rest/services/api/SearchServer",
        params={
            "searchText": address,
            "type": "locations",
            "limit": 5,
            "sr": 4326,
        },
        timeout=20,
    )
    response.raise_for_status()

    locations = []
    for result in response.json().get("results", []):
        attributes = result.get("attrs", {})
        lat = number(attributes.get("lat"))
        lon = number(attributes.get("lon"))
        if lat is None or lon is None:
            continue

        label = attributes.get("label") or attributes.get("detail") or address
        label = re.sub(r"<[^>]+>", "", str(label)).strip()
        locations.append({"label": label, "lat": lat, "lon": lon})

    return locations


def location_suggestions(searchterm):
    if len(searchterm.strip()) < 2:
        return []

    return [
        (location["label"], location)
        for location in search_locations(searchterm)
    ]


def set_start_location(location):
    st.session_state.start = (location["lat"], location["lon"])
    st.session_state.start_source = "search"
    st.session_state.start_label = location["label"]
    st.session_state.routes = []
    st.session_state.location_notice = True


def on_location_selected(location):
    set_start_location(location)


def search_location(address):
    matches = search_locations(address)
    if not matches:
        return None
    return matches[0]["lat"], matches[0]["lon"]

def is_swiss_location(lat, lon):
    response = requests.get(
        "https://api3.geo.admin.ch/rest/services/api/MapServer/identify",
        params={
            "geometry": f"{lon},{lat}",
            "geometryType": "esriGeometryPoint",
            "imageDisplay": "500,500,96",
            "mapExtent": f"{lon - 0.01},{lat - 0.01},{lon + 0.01},{lat + 0.01}",
            "tolerance": 1,
            "layers": "all:ch.swisstopo.swissboundaries3d-land-flaeche.fill",
            "sr": 4326,
            "returnGeometry": "false",
        },
        timeout=20,
    )
    response.raise_for_status()
    return bool(response.json().get("results", []))


# ==========================================
# 3. WEATHER
# ==========================================

@st.cache_data(ttl=1800)
def get_weather(lat, lon):

    response = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": lat,
            "longitude": lon,
            "current": [
                "temperature_2m",
                "apparent_temperature",
                "precipitation",
                "wind_speed_10m"
            ]
        },
        timeout=20
    )

    response.raise_for_status()

    return response.json()["current"]


# ==========================================
# 4. AIR QUALITY
# ==========================================

@st.cache_data(ttl=1800)
def get_air_quality(lat, lon):

    response = requests.get(
        "https://air-quality-api.open-meteo.com/v1/air-quality",
        params={
            "latitude": lat,
            "longitude": lon,
            "current": [
                "european_aqi",
                "pm2_5"
            ]
        },
        timeout=20
    )

    response.raise_for_status()

    return response.json()["current"]


# ==========================================
# 5. OPENROUTESERVICE
# ==========================================

def get_running_route(lat, lon, distance, seed):

    url = (
        "https://api.openrouteservice.org"
        "/v2/directions/foot-walking/geojson"
    )

    response = requests.post(
        url,
        headers={
            "Authorization": ORS_KEY,
            "Content-Type": "application/json"
        },
        json={
            "coordinates": [[lon, lat]],
            "elevation": True,
            "options": {
                "avoid_features": ["ferries"],
                "round_trip": {
                    "length": distance * 1000,
                    "points": 5,
                    "seed": seed
                }
            }
        },
        timeout=60
    )

    response.raise_for_status()

    feature = response.json()["features"][0]

    coordinates = feature["geometry"]["coordinates"]

    distance_m = number(feature.get("properties", {}).get("summary", {}).get("distance"))
    actual_distance = distance_m / 1000 if distance_m is not None and distance_m > 0 else None

    # Calculate elevation gain
    heights = [number(point[2]) if len(point) > 2 else None for point in coordinates]
    elevation_gain = None
    if len(heights) >= 2 and all(height is not None for height in heights):
        elevation_gain = round(sum(max(0, b - a) for a, b in zip(heights, heights[1:])))

    return {
        "coordinates": coordinates,
        "distance": actual_distance,
        "elevation": elevation_gain
    }


# ==========================================
# 8. RUNCOMFY SCORE
# ==========================================

def calculate_score(
    route,
    target_distance,
    fitness,
    hill_preference,
    temperature,
    aqi,
    return_breakdown=False
):

    # Distance: maximum 30 points
    route_distance = number(route.get("distance"))
    if route_distance is not None and route_distance <= 0:
        route_distance = None
    gain = number(route.get("elevation"))
    if gain is not None and gain < 0:
        gain = None
    temperature, aqi = number(temperature), number(aqi)
    
    distance_score = (
        max(0, 30 - abs(route_distance - target_distance) * 6)
        if route_distance is not None else None
    )

    # Elevation: maximum 30 points
    elevation_per_km = (
        gain / max(route_distance, 0.1)
        if gain is not None and route_distance is not None else None
    )
    limit = elevation_limit(fitness, hill_preference)
    elevation_score = (
        max(0, 30 - max(0, elevation_per_km - limit))
        if elevation_per_km is not None else None
    )

    # Temperature: maximum 15 points
    temperature_score = max(
        0,
        15 - abs(temperature - 15)
    ) if temperature is not None else None

    # Air quality: maximum 15 points
    air_score = max(
        0,
        15 - max(0, aqi - 20) * 0.3
    ) if aqi is not None else None

   
    components = [
        {"Factor": "Distance", "Points": distance_score, "Maximum": 30,
         "Input": f"{metric_text(route_distance, ' km')} / target {target_distance:g} km",
         "Calculation": "max(0, 30 - abs(distance - target) × 6)"},
        {"Factor": "Elevation", "Points": elevation_score, "Maximum": 30,
         "Input": f"{metric_text(elevation_per_km, ' m/km')}; limit {limit:g} m/km ({fitness}, {hill_preference})",
         "Calculation": "max(0, 30 - max(0, gain per km - limit)); gain per km = gain / max(distance, 0.1)"},
        {"Factor": "Temperature", "Points": temperature_score, "Maximum": 15,
         "Input": metric_text(temperature, ' °C'),
         "Calculation": "max(0, 15 - abs(temperature - 15))"},
        {"Factor": "Air quality", "Points": air_score, "Maximum": 15,
         "Input": f"European AQI: {metric_text(aqi)}",
         "Calculation": "max(0, 15 - max(0, AQI - 20) × 0.3)"},
    ]

    available = [item for item in components if item["Points"] is not None]
    raw_total = sum(item["Points"] for item in available)
    available_max = sum(item["Maximum"] for item in available)
    # Preserve the existing shade-only rescaling; apply the same policy to
    # other missing factors instead of silently treating missing data as zero.
    total = raw_total
    if available_max and available_max != 100:
        total = total / available_max * 100
    score = round(max(0, min(100, total))) if available_max else None
    breakdown = {"score": score, "components": components,
                 "raw_total": raw_total, "available_max": available_max}
    return breakdown if return_breakdown else score


def preference_warnings(route, preferences):
    """Check saved generation preferences, independently of ranking."""
    warnings = []
    actual = number(route.get("distance"))
    if actual is None or actual <= 0:
        warnings.append("Distance unavailable — the distance and elevation preferences cannot be fully checked.")
    else:
        target = preferences["distance"]
        tolerance = target * preferences["distance_tolerance_pct"] / 100
        if abs(actual - target) > tolerance + 1e-9:
            warnings.append(
                f"Distance mismatch: {actual:.2f} km versus {target:g} km requested. "
                f"Allowed deviation: ±{preferences['distance_tolerance_pct']:g}% (±{tolerance:.2f} km)."
            )
    gain = number(route.get("elevation"))
    if gain is None or gain < 0:
        warnings.append("Elevation unavailable — the elevation preference could not be checked.")
    elif actual is not None and actual > 0:
        gain_per_km = gain / max(actual, 0.1)
        minimum, maximum = preferences["elevation_min"], preferences["elevation_max"]
        if gain_per_km < minimum or gain_per_km > maximum:
            warnings.append(
                f"Elevation mismatch: {gain_per_km:.1f} m gain/km; "
                f"{preferences['hill_preference']} accepts {minimum:g}–{maximum:g} m gain/km."
            )
    

# ==========================================
# 9. USER INTERFACE
# ==========================================

st.header("📍 Choose your starting point")

selected_location = st_searchbox(
    location_suggestions,
    label="Enter any Swiss address or location",
    placeholder="e.g. Bahnhof Chur, St. Gallen, Zurich",
    key="start_location_search",
    debounce=300,
    default_use_searchterm=True,
    clear_on_submit=True,
    submit_function=on_location_selected,
)

if st.session_state.get("location_search_error"):
    st.warning(
        f"Live location suggestions are unavailable: "
        f"{st.session_state.location_search_error}"
    )

if st.button("🔎 Search location"):
    if isinstance(selected_location, dict):
        set_start_location(selected_location)
    elif isinstance(selected_location, str) and selected_location.strip():
        try:
            matches = search_locations(selected_location)
            if matches:
                set_start_location(matches[0])
            else:
                st.warning("Location not found in Switzerland.")
        except Exception as error:
            st.error(f"Location search failed: {error}")

if st.session_state.pop("location_notice", False):
    st.success("Location found!")


st.write("Or select your starting point on the map:")

lat, lon = st.session_state.start

st.write("**Selected starting location:**", st.session_state.start_label)
st.caption(f"Latitude {lat:.5f}, longitude {lon:.5f}")
if st.session_state.start_source == "default":
    st.info("Using the default starting location: St. Gallen. Search or confirm a point on the map to change it.")

start_map = folium.Map(
    location=[lat, lon],
    zoom_start=13,
    min_zoom=7,
    max_bounds=True,
    min_lat=45.7,
    max_lat=47.9,
    min_lon=5.8,
    max_lon=10.7
)
folium.Marker(
    [lat, lon],
    tooltip="Starting point",
    icon=folium.Icon(color="green")
).add_to(start_map)


map_data = st_folium(
    start_map,
    height=800,
    use_container_width=True,
    key="start_location_map"
)

if map_data and map_data.get("last_clicked"):

    clicked = map_data["last_clicked"]

    selected_lat = clicked["lat"]
    selected_lon = clicked["lng"]

    st.write(
        f"Selected: {selected_lat:.5f}, "
        f"{selected_lon:.5f}"
    )

    if st.button("📍 Confirm starting point"):

        try:
            if is_swiss_location(
                selected_lat,
                selected_lon
            ):

                st.session_state.start = (
                    selected_lat,
                    selected_lon
                )
                st.session_state.start_source = "map"
                st.session_state.start_label = "Confirmed map location"

                st.session_state.routes = []
                st.rerun()

            else:
                st.warning(
                    "Please choose a location in Switzerland."
                )

        except Exception as error:
            st.error(f"Location check failed: {error}")


# ==========================================
# 10. RUNNING PREFERENCES
# ==========================================

st.header("🏃 Your running preferences")

col1, col2 = st.columns(2)

with col1:

    fitness = st.selectbox(
        "Fitness level",
        ["Beginner", "Intermediate", "Advanced"]
    )

    distance = st.slider(
        "Desired running distance (km)",
        min_value=2,
        max_value=30,
        value=5
    )

with col2:

    hill_preference = st.selectbox(
        "Elevation preference",
        ["Flat", "Moderate", "Hilly"]
    )


# ==========================================
# 11. GENERATE RUNNING ROUTES
# ==========================================


with st.expander("Preference warning thresholds"):
    st.caption(
        "These checks flag mismatches; they do not change the scoring formula. "
        "Distance defaults to ±10%. Elevation maxima default to the existing "
        "fitness limits (10 / 25 / 45 m gain per km), multiplied by "
        "0.5 for Flat, 1 for Moderate, or 2 for Hilly. "
        "Hilly additionally defaults to a minimum of 10 m gain per km."
    )
    distance_tolerance_pct = st.number_input(
        "Allowed distance deviation (%)", min_value=0.0, max_value=100.0, value=10.0, step=1.0
    )
    elevation_max = st.number_input(
        "Maximum elevation gain per km for warnings (m/km)",
        min_value=0.0, value=float(elevation_limit(fitness, hill_preference)), step=1.0,
        key=f"warning_elevation_max_{fitness}_{hill_preference}"
    )
    elevation_min = 0.0
    if hill_preference == "Hilly":
        elevation_min = st.number_input(
            "Minimum Hilly elevation gain per km (m/km)",
            min_value=0.0, value=10.0, step=1.0
        )
   
    preferences = dict(
    distance=distance, fitness=fitness, hill_preference=hill_preference,
    distance_tolerance_pct=distance_tolerance_pct,
    elevation_min=elevation_min, elevation_max=elevation_max,
)

thresholds_valid = elevation_min <= elevation_max
if not thresholds_valid:
    st.warning("Each minimum warning threshold must be less than or equal to its maximum.")

if st.button("🚀 Find my perfect running route", disabled=not thresholds_valid):

    lat, lon = st.session_state.start

    with st.spinner("Analysing running routes..."):

        try:

            # Weather
            weather = get_weather(lat, lon)

            temperature = number(weather.get("temperature_2m"))

            # Air quality
            air = get_air_quality(lat, lon)

            aqi = number(air.get("european_aqi"))

            if temperature is None or aqi is None:
                st.error(
                    "Weather or air-quality data is unavailable."
                )
                st.stop()

            # Generate routes
            routes = []
            errors = []

            for seed in range(1, 9):
                if len(routes) >= 3:
                    break

                try:
                    route = get_running_route(lat, lon, distance, seed)

                    duplicate = any(
                        route["coordinates"] == existing["coordinates"]
                        for existing in routes
                    )

                    if duplicate:
                        continue

                    routes.append(route)

                except Exception as error:
                    errors.append(f"Seed {seed}: {error}")
                    continue

            if not routes:
                st.error("No running routes found. Try another location or distance.")
                if errors:
                    st.code("\n".join(errors[:3]))
                st.stop()

        
            # Score routes
            for route in routes:

              
                route["score_breakdown"] = calculate_score(
                    route,
                    distance,
                    fitness,
                    hill_preference,
                    temperature,
                    aqi,
                    return_breakdown=True
                )
                route["score"] = route["score_breakdown"]["score"]
                route["preference_warnings"] = preference_warnings(route, preferences)

            routes.sort(
                key=lambda route: route["score"] if route["score"] is not None else -1,
                reverse=True
            )

            # Save results
            st.session_state.routes = routes
            st.session_state.result_preferences = preferences.copy()
            st.session_state.result_start = tuple(st.session_state.start)
            st.session_state.result_start_label = st.session_state.start_label
            st.session_state.result_start_source = st.session_state.start_source

            st.session_state.weather = weather
            st.session_state.air = air

            
        except Exception as error:

            st.error(f"An error occurred: {error}")


# ==========================================
# 12. DISPLAY RESULTS
# ==========================================

if st.session_state.routes:

    routes = st.session_state.routes

    weather = st.session_state.weather
    air = st.session_state.air

    lat, lon = st.session_state.get("result_start", st.session_state.start)
    saved_preferences = st.session_state.get("result_preferences")
    if saved_preferences is None:
        st.info("Generate routes again to see preference checks and score explanations.")
    elif saved_preferences != preferences:
        st.info("Preferences or warning thresholds have changed. These results use the settings below; generate routes again to apply your changes.")

    st.write("**Starting location used for these routes:**",
             st.session_state.get("result_start_label", st.session_state.start_label))
    st.caption(f"Latitude {lat:.5f}, longitude {lon:.5f}")
    if st.session_state.get("result_start_source", st.session_state.start_source) == "default":
        st.info("These routes use the default starting location: St. Gallen.")
    if saved_preferences:
        st.caption(
            f"Generated for {saved_preferences['distance']:g} km · {saved_preferences['fitness']} · "
            f"{saved_preferences['hill_preference']} elevation · {saved_preferences['shade_preference']} shade. "
            f"Warning thresholds: distance ±{saved_preferences['distance_tolerance_pct']:g}%; "
            f"elevation {saved_preferences['elevation_min']:g}–{saved_preferences['elevation_max']:g} m/km; "
        )

    st.header("🌤️ Current running conditions")

    c1, c2, c3, c4 = st.columns(4)

    c1.metric(
        "Temperature",
        metric_text(weather.get('temperature_2m'), ' °C')
    )

    c2.metric(
        "Feels like",
        metric_text(weather.get('apparent_temperature'), ' °C')
    )

    c3.metric(
        "Air Quality Index",
        metric_text(air.get("european_aqi"))
    )

    c4.metric(
        "Wind",
        metric_text(weather.get('wind_speed_10m'), ' km/h')
    )

    st.write(
        f"🌧️ Current precipitation: "
        f"{metric_text(weather.get('precipitation'), ' mm')}"
    )

   
    # ======================================
    # ROUTE MAP
    # ======================================

    st.header("🗺️ Your running routes")

    route_map = folium.Map(
        location=[lat, lon],
        zoom_start=13,
    )

    colors = ["green", "blue", "purple"]

    all_map_points = []

    for i, route in enumerate(routes):

        points = [
            [point[1], point[0]]
            for point in route["coordinates"]
        ]

        all_map_points.extend(points)

        folium.PolyLine(
            points,
            color=colors[i],
            weight=6,
            opacity=0.85,
            tooltip=(
                f"Route {i+1}: "
                f"{metric_text(route.get('distance'), ' km')} | "
                f"Score {metric_text(route.get('score'), '/100', 0)}"
            )
        ).add_to(route_map)

    folium.Marker(
        [lat, lon],
        tooltip="Start / Finish",
        icon=folium.Icon(color="red")
    ).add_to(route_map)

    if all_map_points:
        route_map.fit_bounds(all_map_points)

    
    st_folium(
        route_map,
        height=800,
        use_container_width=True,
        key="running_routes_map"
    )
    # ======================================
    # RECOMMENDATIONS
    # ======================================

    st.header("🏆 Your personalized recommendations")

    medals = ["🥇", "🥈", "🥉"]

    for i, route in enumerate(routes):

        st.subheader(
            f"{medals[i]} Route {i+1}"
        )

        col1, col2, col3 = st.columns(3)

        col1.metric(
            "Distance",
            metric_text(route.get('distance'), ' km')
        )

        col2.metric(
            "Elevation gain",
            metric_text(route.get('elevation'), ' m', 0)
        )

        col3.metric(
            "RunComfy Score",
            metric_text(route.get('score'), '/100', 0)
        )

       
        for message in route.get("preference_warnings", []):
            st.warning(message)

        with st.expander(f"How Route {i+1} was scored"):
            breakdown = route.get("score_breakdown")
            if not breakdown:
                st.info("Score details unavailable. Generate this route again to see its calculation.")
            else:
                for component in breakdown["components"]:
                    points = component["Points"]
                    label = "Unavailable — omitted" if points is None else f"{points:.2f} / {component['Maximum']} points"
                    st.write(f"**{component['Factor']}: {label}**")
                    st.caption(component["Input"])
                    st.code(component["Calculation"], language="text")
                maximum = breakdown["available_max"]
                if not maximum:
                    st.write("Score unavailable: no scoring inputs are available.")
                elif maximum == 100:
                    st.write(f"Total: {breakdown['raw_total']:.2f} / 100 → rounded score {breakdown['score']}/100.")
                else:
                    st.write(
                        f"Available total: {breakdown['raw_total']:.2f} / {maximum}. "
                        f"Rescaled to 100 and rounded: {breakdown['score']}/100."
                    )
                    st.caption("Missing factors are excluded, not treated as zero; the same policy applies to missing route metrics.")
                st.caption(
                    "Elevation scoring penalizes gain above its limit; it does not reward a minimum amount of hills. "
                    "Preference warnings are separate checks and do not deduct points. "
                    "Feels-like temperature, wind and precipitation are displayed but do not affect this score."
                )

        if i == 0:

            st.success(
                "⭐ Best matching running route!"
            )

        st.divider()

    st.caption(
        "RunComfy AI uses real routing, weather and "
        "air-quality data. Scores are rule-based. "
    )

    st.caption(
        "Map data © OpenStreetMap contributors. "
        "Routing: openrouteservice. "
        "Weather and air quality: Open-Meteo."
    )
