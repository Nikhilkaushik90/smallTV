FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b
WORKDIR /app
COPY collector.py statserver.py ./
USER 1000:1000
ENV PYTHONDONTWRITEBYTECODE=1
CMD ["python", "-u", "collector.py"]
