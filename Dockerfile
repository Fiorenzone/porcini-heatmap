# Deploy slim: griglia forestale (~MB), no pickle 450MB.
FROM python:3.12-slim

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    libexpat1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py ./
COPY static ./static
COPY data/forest/forest_grid.bin data/forest/forest_grid.json ./data/forest/
COPY data/soil/soil_grid.bin data/soil/soil_grid.json data/soil/jja_heat.json ./data/soil/
COPY data/cover/cover_grid.bin data/cover/cover_grid.json ./data/cover/
COPY data/arpa/erg5_cells.csv ./data/arpa/
# cache dir vuota; meteo/bollettini al primo boot
RUN mkdir -p data/cache

ENV PORCINI_BUILD_GRID=0
ENV PYTHONUNBUFFERED=1
ENV HOST=0.0.0.0
EXPOSE 8765
CMD ["python3", "server.py"]
