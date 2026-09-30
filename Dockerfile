# Deploy slim: griglia forestale (~MB), no pickle 450MB.
FROM python:3.12-slim

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    libexpat1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY vault.py boot.py ./
COPY secret/core.enc ./secret/core.enc
COPY static ./static
# cache dir vuota; formula e griglie si aprono al boot
RUN mkdir -p data/cache

ENV PORCINI_BUILD_GRID=0
ENV PYTHONUNBUFFERED=1
ENV HOST=0.0.0.0
EXPOSE 8765
CMD ["python3", "boot.py"]
