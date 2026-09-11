# PV Shadow Study

First application slice for generating annual solar-position and clear-sky
irradiance data from latitude and longitude.

## Current features

- Latitude and longitude inputs with coordinate validation
- Interactive satellite map with click-to-select coordinates
- 1, 5 or 15-minute annual time series
- Solar azimuth and apparent elevation calculated with pvlib
- Ineichen clear-sky GHI, DNI and DHI
- Configurable clear-sky GHI threshold
- Monthly summary and CSV download

All timestamps are currently calculated and exported in UTC. Local time-zone
handling will be added when the geographic/map workflow is expanded.

## Run locally

```bash
python -m pip install -r requirements.txt
streamlit run app.py
```
