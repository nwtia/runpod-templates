FROM nvidia/cuda:13.0.2-runtime-ubuntu24.04

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends python3 python3-venv ca-certificates

ENV OPENJUTSU_CODE_ROOT=/opt/nwtia/openjutsu-runtime \
    NEXT_TELEMETRY_DISABLED=1 \
    HF_HUB_DISABLE_TELEMETRY=1 \
    HF_HUB_DISABLE_IMPLICIT_TOKEN=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    SAM2_BUILD_CUDA=0

COPY bootstrap.py overlay.zip sources.lock.json models.lock.json /opt/nwtia/openjutsu/
COPY validate_install.py /opt/nwtia/openjutsu/validate_install.py
RUN python3 -u /opt/nwtia/openjutsu/bootstrap.py --install-only \
    && python3 -u /opt/nwtia/openjutsu/validate_install.py \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

LABEL org.opencontainers.image.title="NWTIA OpenJutsu V3" \
      org.opencontainers.image.source="https://github.com/nwtia/runpod-templates" \
      org.opencontainers.image.description="Pinned OpenJutsu V3, Qwen 4B + ClipProj, LoRA and tracked foreground preservation"

EXPOSE 22
ENTRYPOINT ["python3", "-u", "/opt/nwtia/openjutsu/bootstrap.py"]
