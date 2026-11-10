FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b
RUN apt-get update \
 && apt-get install -y --no-install-recommends fonts-dejavu-core tzdata ca-certificates \
 && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --disable-pip-version-check -r /tmp/requirements.txt
WORKDIR /app
COPY smalltv ./smalltv
USER 1000:1000
ENV PYTHONDONTWRITEBYTECODE=1
CMD ["python", "-u", "-m", "smalltv", "--config", "/config/config.toml", "run"]
