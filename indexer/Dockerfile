# Multi-stage production Dockerfile for RAHBAR Indexer Job
FROM python:3.12-slim AS builder

WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# Final production stage with system extraction & OCR binaries
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/home/indexeruser/.local/bin:$PATH" \
    TMPDIR="/tmp"

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-eng \
        poppler-utils && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* && \
    groupadd -g 10001 indexergroup && \
    useradd -u 10001 -g indexergroup -m -s /bin/bash indexeruser

WORKDIR /app
COPY --from=builder --chown=indexeruser:indexergroup /root/.local /home/indexeruser/.local
COPY --chown=indexeruser:indexergroup pipeline/ /app/pipeline/
COPY --chown=indexeruser:indexergroup corpus/ /corpus/

USER indexeruser

ENTRYPOINT ["python", "-m", "pipeline.run"]
