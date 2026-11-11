FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
COPY . /src
RUN pip install --no-cache-dir /src yt-dlp
WORKDIR /config
ENTRYPOINT ["starkeep"]
CMD ["run", "--config-dir", "/config"]
