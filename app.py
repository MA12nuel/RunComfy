import streamlit as st
import requests
import folium
from streamlit_folium import st_folium

st.set_page_config(page_title="RunComfy AI", page_icon="🏃")

st.title("🏃 RunComfy AI")
st.write("Find your perfect running route in Switzerland!")

# Schweizer Städte
cities = {
    "St. Gallen": (47.4245, 9.3767),
    "Zurich": (47.3769, 8.5417),
    "Chur": (46.8508, 9.5320),
    "Bern": (46.9480, 7.4474),
    "Lucerne": (47.0502, 8.3093),
}

city = st.selectbox("📍 Starting location", list(cities.keys()))
level = st.selectbox(
    "🏃 Fitness level",
    ["Beginner", "Intermediate", "Advanced"]
)
distance = st.slider("📏 Distance (km)", 2, 20, 5)

lat, lon = cities[city]

if st.button("🔍 Find my running routes"):

    try:
        # Wetter abrufen
        weather = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat,
                "longitude": lon,
                "current": "temperature_2m"
            },
            timeout=15
        ).json()

        temperature = weather["current"]["temperature_2m"]

        # Luftqualität abrufen
        air = requests.get(
            "https://air-quality-api.open-meteo.com/v1/air-quality",
            params={
                "latitude": lat,
                "longitude": lon,
                "current": "european_aqi"
            },
            timeout=15
        ).json()

        aqi = air["current"]["european_aqi"]

        st.subheader("🌤️ Current conditions")

        col1, col2 = st.columns(2)
        col1.metric("Temperature", f"{temperature} °C")
        col2.metric("Air Quality Index", aqi)

        # OpenRouteService
        api_key = st.secrets["ORS_API_KEY"]

        url = "https://api.openrouteservice.org/v2/directions/foot-walking/geojson"

        # Drei unterschiedliche Rundlaufstrecken
        routes = []

        for seed in [1, 2, 3]:

            response = requests.post(
                url,
                headers={
                    "Authorization": api_key,
                    "Content-Type": "application/json"
                },
                json={
                    "coordinates": [[lon, lat]],
                    "options": {
                        "round_trip": {
                            "length": distance * 1000,
                            "points": 4,
                            "seed": seed
                        }
                    }
                },
                timeout=30
            )

            response.raise_for_status()
            data = response.json()

            feature = data["features"][0]
            properties = feature["properties"]["summary"]

            route_distance = properties["distance"] / 1000

            routes.append({
                "geometry": feature["geometry"],
                "distance": route_distance,
                "seed": seed
            })

        # Karte
        st.subheader("🗺️ Your running routes")

        m = folium.Map(
            location=[lat, lon],
            zoom_start=13
        )

        colors = ["blue", "green", "purple"]

        for i, route in enumerate(routes):

            coordinates = [
                [point[1], point[0]]
                for point in route["geometry"]["coordinates"]
            ]

            folium.PolyLine(
                coordinates,
                color=colors[i],
                weight=5,
                tooltip=f"Route {i+1}: {route['distance']:.1f} km"
            ).add_to(m)

        folium.Marker(
            [lat, lon],
            tooltip="Start / Finish",
            icon=folium.Icon(color="red")
        ).add_to(m)

        st_folium(m, width=700, height=450)

        # Einfache Bewertung nach Distanz
        st.subheader("🏆 Route recommendations")

        for i, route in enumerate(routes):

            difference = abs(route["distance"] - distance)

            score = max(0, 100 - difference * 10)

            st.write(
                f"**Route {i+1}:** "
                f"{route['distance']:.1f} km | "
                f"RunComfy Score: {score:.0f}/100"
            )

        st.info(
            "This first version scores routes by distance only. "
            "Elevation, shade and personalized fitness scoring "
            "will be added next."
        )

    except requests.exceptions.RequestException as e:
        st.error(f"API request failed: {e}")

    except KeyError as e:
        st.error(f"Missing data or API key: {e}")

    except Exception as e:
        st.error(f"Something went wrong: {e}")
