import streamlit as st
import requests
import folium
import math
import time

from streamlit_folium import st_folium


# =====================================
# 1. APP SETTINGS
# =====================================

st.set_page_config(
    page_title="RunComfy AI",
    page_icon="🏃",
    layout="wide"
)

st.title("🏃 RunComfy AI")
st.write("Find your perfect running route in Switzerland 🇨🇭")

ORS_KEY = st.secrets["ORS_API_KEY"]

# The overpass.ch key is saved for future use.
# Forest data below uses the public OSM Overpass API.

HEADERS = {
    "User-Agent": "RunComfySchoolProject/1.0 (educational prototype)"
}


# =====================================
# 2. SWISS LOCATION SEARCH
# =====================================

@st.cache_data(ttl=86400)
def search_location(address):

    url = "https://nominatim.openstreetmap.org/search"

    response = requests.get(
        url,
        params={
            "q": address,
            "format": "json",
            "countrycodes": "ch",
            "limit": 1
        },
        headers=HEADERS,
        timeout=15
    )

    response.raise_for_status()
    results = response.json()

    if results:
        return (
            float(results[0]["lat"]),
            float(results[0]["lon"])
        )

    return None


def in_switzerland(lat, lon):
    # Rough bounding box; exact border check below.
    if not (45.8 <= lat <= 47.9 and 5.9 <= lon <= 10.6):
        return False

    # Check country using reverse geocoding
    response = requests.get(
        "https://nominatim.openstreetmap.org/reverse",
        params={
            "lat": lat,
            "lon": lon,
            "format": "json",
            "zoom": 3
        },
        headers=HEADERS,
        timeout=15
    )

    response.raise_for_status()

    return (
        response.json()
        .get("address", {})
        .get("country_code", "")
        .lower() == "ch"
    )


# =====================================
# 3. WEATHER API
# =====================================

@st.cache_data(ttl=1800)
def get_weather(lat, lon):

    response = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": lat,
            "longitude": lon,
            "current": "temperature_2m"
        },
        timeout=15
    )

    response.raise_for_status()

    return response.json()["current"]["temperature_2m"]


# =====================================
# 4. AIR QUALITY API
# =====================================

@st.cache_data(ttl=1800)
def get_air_quality(lat, lon):

    response = requests.get(
        "https://air-quality-api.open-meteo.com/v1/air-quality",
        params={
            "latitude": lat,
            "longitude": lon,
            "current": "european_aqi"
        },
        timeout=15
    )

    response.raise_for_status()

    return response.json()["current"]["european_aqi"]


# =====================================
# 5. RUNNING ROUTES
# =====================================

def get_route(lat, lon, distance, seed):

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
                "round_trip": {
                    "length": distance * 1000,
                    "points": 5,
                    "seed": seed
                }
            }
        },
        timeout=45
    )

    response.raise_for_status()

    feature = response.json()["features"][0]

    coordinates = feature["geometry"]["coordinates"]

    actual_distance = (
        feature["properties"]["summary"]["distance"] / 1000
    )

    # Calculate uphill elevation gain
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
        "elevation": elevation_gain
    }


# =====================================
# 6. FOREST / SHADE ESTIMATION
# =====================================

@st.cache_data(ttl=86400)
def get_forests(lat, lon):

    query = f"""
    [out:json][timeout:15];
    (
      way["landuse"="forest"](around:2500,{lat},{lon});
      way["natural"="wood"](around:2500,{lat},{lon});
    );
    out geom;
    """

    response = requests.post(
        "https://overpass.kumi.systems/api/interpreter",
        data={"data": query},
        headers=HEADERS,
        timeout=25
    )

    response.raise_for_status()

    return response.json()["elements"]


def estimate_shade(route, forests):

    if not forests:
        return None

    forest_points = []

    for forest in forests:
        for point in forest.get("geometry", []):
            forest_points.append(
                (point["lat"], point["lon"])
            )

    if not forest_points:
        return None

    # Sample route points
    coordinates = route["coordinates"]
    step = max(1, len(coordinates) // 60)
    samples = coordinates[::step]

    shaded = 0

    for lon, lat, *_ in samples:

        for forest_lat, forest_lon in forest_points:

            # Approximate distance in metres
            dx = (
                (lon - forest_lon)
                * 111000
                * math.cos(math.radians(lat))
            )

            dy = (lat - forest_lat) * 111000

            if math.sqrt(dx**2 + dy**2) < 60:
                shaded += 1
                break

    return round(100 * shaded / len(samples))


# =====================================
# 7. RUNCOMFY SCORE
# =====================================

def calculate_score(
    route,
    target_distance,
    level,
    hill_preference,
    shade_preference,
    temperature,
    aqi,
    shade
):

    # Distance score
    distance_error = abs(
        route["distance"] - target_distance
    )

    distance_score = max(
        0, 100 - distance_error * 20
    )

    # Fitness / elevation score
    limits = {
        "Beginner": 15,
        "Intermediate": 30,
        "Advanced": 55
    }

    limit = limits[level]

    if hill_preference == "Flat":
        limit *= 0.5

    elif hill_preference == "Hilly":
        limit *= 2

    elevation_per_km = (
        route["elevation"] / max(route["distance"], 0.1)
    )

    elevation_score = max(
        0,
        100 - max(0, elevation_per_km - limit) * 2
    )

    # Temperature score
    temperature_score = max(
        0,
        100 - abs(temperature - 15) * 5
    )

    # Air quality score
    air_score = max(
        0,
        100 - max(0, aqi - 20) * 2
    )

    # Shade score
    if shade is None:
        shade_score = None
    elif shade_preference == "High":
        shade_score = shade
    elif shade_preference == "Low":
        shade_score = 100 - shade
    else:
        shade_score = 100

    # Weighted score
    components = [
        (distance_score, 0.30),
        (elevation_score, 0.30),
        (temperature_score, 0.15),
        (air_score, 0.15)
    ]

    if shade_score is not None:
        components.append((shade_score, 0.10))

    total_weight = sum(weight for _, weight in components)

    score = sum(
        value * weight for value, weight in components
    ) / total_weight

    return round(score)


# =====================================
# 8. USER INPUT
# =====================================

st.subheader("📍 Choose your starting point")

address = st.text_input(
    "Enter a Swiss address or location",
    placeholder="e.g. Bahnhof St. Gallen, Chur, Zurich"
)

if "start" not in st.session_state:
    st.session_state.start = (47.4245, 9.3767)

if st.button("🔎 Search address"):

    if address.strip():

        try:
            location = search_location(address)

            if location:
                st.session_state.start = location
                st.success("Location found!")
            else:
                st.warning("Address not found in Switzerland.")

        except Exception as e:
            st.error(f"Location search failed: {e}")


st.write("Or click on the map to choose your start:")

lat, lon = st.session_state.start

start_map = folium.Map(
    location=[lat, lon],
    zoom_start=13
)

folium.Marker(
    [lat, lon],
    tooltip="Current starting point"
).add_to(start_map)

map_result = st_folium(
    start_map,
    height=300,
    width=700,
    key="start_map"
)

if map_result and map_result.get("last_clicked"):

    clicked = map_result["last_clicked"]

    new_lat = clicked["lat"]
    new_lon = clicked["lng"]

    if st.button("📍 Use selected map location"):

        try:
            if in_switzerland(new_lat, new_lon):
                st.session_state.start = (new_lat, new_lon)
                st.rerun()
            else:
                st.warning("Please select a location in Switzerland.")

        except Exception as e:
            st.error(f"Location check failed: {e}")


st.subheader("🏃 Your running preferences")

col1, col2 = st.columns(2)

with col1:

    level = st.selectbox(
        "Fitness level",
        ["Beginner", "Intermediate", "Advanced"]
    )

    distance = st.slider(
        "Running distance (km)",
        2, 30, 5
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


# =====================================
# 9. GENERATE ROUTES
# =====================================

if st.button("🚀 Find my perfect running route"):

    lat, lon = st.session_state.start

    with st.spinner("Finding your running routes..."):

        try:

            temperature = get_weather(lat, lon)
            aqi = get_air_quality(lat, lon)

            st.subheader("🌤️ Current conditions")

            c1, c2 = st.columns(2)

            c1.metric(
                "Temperature",
                f"{temperature} °C"
            )

            c2.metric(
                "European Air Quality Index",
                aqi
            )

            # Forest data is optional
            try:
                forests = get_forests(lat, lon)
            except Exception:
                forests = []

            routes = []

            # Try multiple seeds
            for seed in [1, 2, 3, 4, 5, 6]:

                if len(routes) == 3:
                    break

                try:
                    route = get_route(
                        lat, lon, distance, seed
                    )

                    # Avoid nearly identical routes
                    if any(
                        route["coordinates"] == r["coordinates"]
                        for r in routes
                    ):
                        continue

                    route["shade"] = estimate_shade(
                        route, forests
                    )

                    route["score"] = calculate_score(
                        route,
                        distance,
                        level,
                        hill_preference,
                        shade_preference,
                        temperature,
                        aqi,
                        route["shade"]
                    )

                    routes.append(route)

                except requests.exceptions.RequestException:
                    continue

            if not routes:
                st.error(
                    "No routes found. Try another starting "
                    "point or a different distance."
                )
                st.stop()

            # Best route first
            routes.sort(
                key=lambda r: r["score"],
                reverse=True
            )

            # =====================================
            # 10. RESULTS MAP
            # =====================================

            st.subheader("🗺️ Your running routes")

            route_map = folium.Map(
                location=[lat, lon],
                zoom_start=13
            )

            colors = ["green", "blue", "purple"]

            for i, route in enumerate(routes):

                points = [
                    [point[1], point[0]]
                    for point in route["coordinates"]
                ]

                folium.PolyLine(
                    points,
                    color=colors[i],
                    weight=6,
                    tooltip=f"Route {i+1}"
                ).add_to(route_map)

            folium.Marker(
                [lat, lon],
                tooltip="Start / Finish",
                icon=folium.Icon(color="red")
            ).add_to(route_map)

            st_folium(
                route_map,
                height=500,
                width=900,
                key="results_map"
            )

            # =====================================
            # 11. ROUTE RECOMMENDATIONS
            # =====================================

            st.subheader("🏆 Your recommendations")

            for i, route in enumerate(routes):

                st.markdown(
                    f"### {'🥇' if i == 0 else '🏃'} "
                    f"Route {i+1}"
                )

                c1, c2, c3 = st.columns(3)

                c1.metric(
                    "Distance",
                    f"{route['distance']:.1f} km"
                )

                c2.metric(
                    "Elevation gain",
                    f"{route['elevation']:.0f} m"
                )

                c3.metric(
                    "RunComfy Score",
                    f"{route['score']}/100"
                )

                if route["shade"] is None:
                    st.write("🌳 Shade: No estimate available")
                else:
                    st.write(
                        f"🌳 Estimated forest proximity: "
                        f"{route['shade']}%"
                    )

                if i == 0:
                    st.success(
                        "⭐ This is your best matching route!"
                    )

                st.divider()

            st.caption(
                "RunComfy scores are rule-based estimates. "
                "Shade uses proximity to mapped forest points, "
                "not actual sunlight measurements. "
                "Weather and air quality are approximate "
                "conditions near the starting point."
            )

        except Exception as e:

            st.error(f"Something went wrong: {e}")
