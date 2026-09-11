# PV Shadow Study

First application slice for generating annual solar-position and clear-sky
irradiance data from latitude and longitude.

## Current features

- Latitude and longitude inputs with coordinate validation
- Interactive satellite map with click-to-select and draggable coordinates
- 1, 5 or 15-minute annual time series
- Solar azimuth and apparent elevation calculated with pvlib
- Ineichen clear-sky GHI, DNI and DHI
- Configurable clear-sky GHI threshold
- Monthly-by-hour average clear-sky GHI heatmap in local standard time
- Annual summary and CSV download

The app uses a fixed 365-day reference year because the current calculation is
a representative clear-sky study rather than historical weather data. All
timestamps are currently calculated and exported in UTC. Local time-zone
handling will be added when the geographic/map workflow is expanded.

## Run locally

```bash
python -m pip install -r requirements.txt
streamlit run app.py
```
