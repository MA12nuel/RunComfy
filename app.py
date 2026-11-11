import streamlit as st
import requests
import folium
import math

from streamlit_folium import st_folium
from shapely.geometry import Point, Polygon

# ==========================================
# 1. RUNCOMFY AI SETTINGS
# ==========================================

st.set_page_config(
    page_title="RunComfy AI",
    page_icon="🏃",
    layout="wide"
)

# Web design
# Simple, readable design
# Bright, readable design
# Bright, balanced design
st.markdown("""
<style>
html, body, .stApp, [data-testid="stAppViewContainer"],
[data-testid="stMain"] {
    background: #ffffff !important;
    color: #26372e !important;
}

h1, h2, h3, p, label { color: #26372e !important; }

.runcomfy-banner {
    padding: 1.2rem 1.5rem;
    margin-bottom: 1.5rem;
    border: 1px solid #c8dfca;
    border-radius: 14px;
    background: #e7f2e8;
}
.runcomfy-banner h1 { margin: 0; }
.runcomfy-banner p { margin: .35rem 0 0; }

h2 {
    padding: .55rem .8rem;
    border-left: 5px solid #397451;
    border-radius: 8px;
    background: #eef6ee;
}

[data-testid="stMetric"],
[data-testid="stTextInput"],
[data-testid="stSelectbox"],
[data-testid="stSlider"] {
    background: #ffffff !important;
    border: 1px solid #dce5dc;
    border-radius: 12px;
}
[data-baseweb="select"] > div, input {
    background: #ffffff !important;
    color: #26372e !important;
}

.stButton > button {
    background: #397451 !important;
    color: #ffffff !important;
    border: 0;
    border-radius: 8px;
}


</style>

<div class="runcomfy-banner">
    <h1>🏃 RunComfy</h1>
    <p>Find a comfortable running route in Switzerland 🇨🇭</p>
</div>
""", unsafe_allow_html=True)


# API keys from Streamlit Secrets
ORS_KEY = st.secrets.get("ORS_API_KEY")
OVERPASS_KEY = st.secrets.get("OVERPASS_API_KEY")

if not ORS_KEY or not OVERPASS_KEY:
    st.error(
        "Missing API keys. Please add ORS_API_KEY and OVERPASS_API_KEY "
        "to your Streamlit secrets."
    )
    st.stop()

HEADERS = {
    "User-Agent": "RunComfySchoolProject/1.0 (contact: hello@runcomfy.app)"
}

# Default location: St. Gallen
if "start" not in st.session_state:
    st.session_state.start = (47.4245, 9.3767)

if "routes" not in st.session_state:
    st.session_state.routes = []


# ==========================================
# 2. SWISS LOCATION SEARCH
# ==========================================

@st.cache_data(ttl=86400)
def search_location(address):

    response = requests.get(
        "https://api3.geo.admin.ch/rest/services/api/SearchServer",
        params={
            "searchText": address,
            "type": "locations",
            "origins": "address",
            "limit": 5,
            "sr": 4326
        },
        timeout=20
    )

    response.raise_for_status()

    results = response.json().get("results", [])

    if not results:
        return None

    attributes = results[0]["attrs"]

    # Coordinates in WGS84
    lat = float(attributes["lat"])
    lon = float(attributes["lon"])

    return lat, lon
def is_swiss_location(lat, lon):

    response = requests.get(

        "https://api3.geo.admin.ch/rest/services/api/MapServer/identify",

        params={

            "geometry": f"{lon},{lat}",

            "geometryType": "esriGeometryPoint",

            "imageDisplay": "500,500,96",

            "mapExtent": f"{lon-0.01},{lat-0.01},{lon+0.01},{lat+0.01}",

            "tolerance": 1,

            "layers": "all:ch.swisstopo.swissboundaries3d-land-flaeche.fill",

            "sr": 4326,

            "returnGeometry": "false"

        },

        timeout=20

    )

    response.raise_for_status()

    results = response.json().get("results", [])

    return len(results) > 0

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

    actual_distance = (
        feature["properties"]["summary"]["distance"] / 1000
    )

    # Calculate elevation gain
    elevation_gain = 0

    for i in range(1, len(coordinates)):

        previous = coordinates[i - 1]
        current = coordinates[i]

        if len(previous) > 2 and len(current) > 2:

            difference = current[2] - previous[2]

            if difference > 0:
                elevation_gain += difference

    return {
        "coordinates": coordinates,
        "distance": actual_distance,
        "elevation": round(elevation_gain)
    }


# ==========================================
# 6. OVERPASS.CH FOREST DATA
# ==========================================

@st.cache_data(ttl=86400)
def get_forests(south, west, north, east):

    query = f"""
    [out:json][timeout:25];
    (
      way["landuse"="forest"]({south},{west},{north},{east});
      way["natural"="wood"]({south},{west},{north},{east});
    );
    out geom;
    """

    response = requests.post(
        "https://api.overpass.ch/v1/interpreter",
        headers={
            "X-API-Key": OVERPASS_KEY,
            "User-Agent": HEADERS["User-Agent"]
        },
        data={"data": query},
        timeout=45
    )

    response.raise_for_status()

    return response.json()["elements"]


# ==========================================
# 7. SHADE ESTIMATION
# ==========================================

def estimate_shade(route, forests):
    polygons = []

    for forest in forests:
        geometry = forest.get("geometry", [])

        if not geometry or len(geometry) < 3:
            continue

        coordinates = []
        for point in geometry:
            if not isinstance(point, dict):
                continue
            if "lon" in point and "lat" in point:
                try:
                    coordinates.append((float(point["lon"]), float(point["lat"])))
                except (TypeError, ValueError):
                    continue

        if len(coordinates) < 3:
            continue

        if coordinates[0] != coordinates[-1]:
            coordinates.append(coordinates[0])

        polygon = Polygon(coordinates)

        if polygon.is_valid and not polygon.is_empty:
            polygons.append(polygon)

    if not polygons:
        return None

    route_points = route["coordinates"]

    step = max(1, len(route_points) // 100)
    samples = route_points[::step]

    if not samples:
        return None

    inside_forest = 0

    for coordinate in samples:
        if len(coordinate) < 2:
            continue

        point = Point(float(coordinate[0]), float(coordinate[1]))

        if any(polygon.covers(point) for polygon in polygons):
            inside_forest += 1

    shade = 100 * inside_forest / len(samples)
    return round(shade)


# ==========================================
# 8. RUNCOMFY SCORE
# ==========================================

def calculate_score(
    route,
    target_distance,
    fitness,
    hill_preference,
    shade_preference,
    temperature,
    aqi,
    shade
):

    # Distance: maximum 30 points
    distance_difference = abs(
        route["distance"] - target_distance
    )

    distance_score = max(
        0,
        30 - distance_difference * 6
    )

    # Elevation: maximum 30 points
    elevation_per_km = (
        route["elevation"] / max(route["distance"], 0.1)
    )

    fitness_limits = {
        "Beginner": 10,
        "Intermediate": 25,
        "Advanced": 45
    }

    limit = fitness_limits[fitness]

    if hill_preference == "Flat":
        limit *= 0.5

    elif hill_preference == "Hilly":
        limit *= 2

    elevation_score = max(
        0,
        30 - max(0, elevation_per_km - limit)
    )

    # Temperature: maximum 15 points
    temperature_score = max(
        0,
        15 - abs(temperature - 15)
    )

    # Air quality: maximum 15 points
    air_score = max(
        0,
        15 - max(0, aqi - 20) * 0.3
    )

    # Shade: maximum 10 points
    if shade is None:
        shade_score = None

    elif shade_preference == "High":
        shade_score = shade / 10

    elif shade_preference == "Low":
        shade_score = (100 - shade) / 10

    elif shade_preference == "Medium":
# prefer a moderate amount of shade
        shade_score = max(0, 10 - abs(shade - 50) / 5)

    else:
        shade_score = 10

    total = (
        distance_score
        + elevation_score
        + temperature_score
        + air_score
    )

    if shade_score is not None:
        total += shade_score
    else:
        # Rescale the available 90 points to 100
        total = total / 90 * 100

    return round(max(0, min(100, total)))


# ==========================================
# 9. USER INTERFACE
# ==========================================

st.header("📍 Choose your starting point")

address = st.text_input(
    "Enter any Swiss address or location",
    placeholder="e.g. Bahnhof Chur, St. Gallen, Zurich"
)

if st.button("🔎 Search location"):

    if address.strip():

        try:
            location = search_location(address)

            if location:
                st.session_state.start = location
                st.session_state.routes = []
                st.success("Location found!")
                st.rerun()

            else:
                st.warning("Location not found in Switzerland.")

        except Exception as error:
            st.error(f"Location search failed: {error}")


st.write("Or select your starting point on the map:")

lat, lon = st.session_state.start

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

    shade_preference = st.selectbox(
        "Shade preference",
        ["High", "Medium", "Low"]
    )


# ==========================================
# 11. GENERATE RUNNING ROUTES
# ==========================================

if st.button("🚀 Find my perfect running route"):

    lat, lon = st.session_state.start

    with st.spinner("Analysing running routes..."):

        try:

            # Weather
            weather = get_weather(lat, lon)

            temperature = weather["temperature_2m"]
            feels_like = weather["apparent_temperature"]
            wind = weather["wind_speed_10m"]
            precipitation = weather["precipitation"]

            # Air quality
            air = get_air_quality(lat, lon)

            aqi = air["european_aqi"]
            pm25 = air["pm2_5"]

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

            # Get forest data for route area
            all_coordinates = [
                point
                for route in routes
                for point in route["coordinates"]
            ]

            lons = [p[0] for p in all_coordinates]
            lats = [p[1] for p in all_coordinates]

            south = min(lats) - 0.002
            north = max(lats) + 0.002
            west = min(lons) - 0.002
            east = max(lons) + 0.002

            forests = []
            forest_error = None

            try:

                forests = get_forests(
                    south,
                    west,
                    north,
                    east
                )

            except Exception as error:
                forest_error = str(error)

            # Score routes
            for route in routes:

                if forest_error:
                    route["shade"] = None
                else:
                    route["shade"] = estimate_shade(
                        route,
                        forests
                    )

                route["score"] = calculate_score(
                    route,
                    distance,
                    fitness,
                    hill_preference,
                    shade_preference,
                    temperature,
                    aqi,
                    route["shade"]
                )

            routes.sort(
                key=lambda route: route["score"],
                reverse=True
            )

            # Save results
            st.session_state.routes = routes

            st.session_state.weather = weather
            st.session_state.air = air

            st.session_state.forest_error = forest_error

        except Exception as error:

            st.error(f"An error occurred: {error}")


# ==========================================
# 12. DISPLAY RESULTS
# ==========================================

if st.session_state.routes:

    routes = st.session_state.routes

    weather = st.session_state.weather
    air = st.session_state.air

    lat, lon = st.session_state.start

    st.header("🌤️ Current running conditions")

    c1, c2, c3, c4 = st.columns(4)

    c1.metric(
        "Temperature",
        f"{weather['temperature_2m']} °C"
    )

    c2.metric(
        "Feels like",
        f"{weather['apparent_temperature']} °C"
    )

    c3.metric(
        "Air Quality Index",
        air["european_aqi"]
    )

    c4.metric(
        "Wind",
        f"{weather['wind_speed_10m']} km/h"
    )

    st.write(
        f"🌧️ Current precipitation: "
        f"{weather['precipitation']} mm"
    )

    if st.session_state.get("forest_error"):

        st.warning(
            "Forest data could not be loaded. "
            "Shade scoring is unavailable."
        )

        with st.expander("Technical details"):
            st.code(st.session_state.forest_error)

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
                f"{route['distance']:.1f} km | "
                f"Score {route['score']}/100"
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
            f"{route['distance']:.1f} km"
        )

        col2.metric(
            "Elevation gain",
            f"{route['elevation']} m"
        )

        col3.metric(
            "RunComfy Score",
            f"{route['score']}/100"
        )

        if route["shade"] is not None:

            st.write(
                f"🌳 Estimated route through mapped forest: "
                f"{route['shade']}%"
            )

        else:

            st.write(
                "🌳 Forest coverage: unavailable"
            )

        if i == 0:

            st.success(
                "⭐ Best matching running route!"
            )

        st.divider()

    st.caption(
        "RunComfy AI uses real routing, weather and "
        "air-quality data. Scores are rule-based. "
        "Forest coverage is only a proxy for shade. "
        "Actual shade depends on sunlight, trees, "
        "buildings and time of day."
    )

    st.caption(
        "Map data © OpenStreetMap contributors. "
        "Routing: openrouteservice. "
        "Weather and air quality: Open-Meteo."
    )
