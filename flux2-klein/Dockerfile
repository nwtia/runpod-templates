FROM ghcr.io/ashleykleynhans/comfyui:cu124-py312-v0.38.0

RUN mv /pre_start.sh /pre_start-comfyui.sh
COPY --chmod=755 install.sh /opt/nwtia/install.sh
COPY workflow.json /opt/nwtia/workflow.json
COPY --chmod=755 pre_start.sh /pre_start.sh

LABEL org.opencontainers.image.title="NWTIA Flux.2 Klein Edit"
LABEL org.opencontainers.image.description="One-click ComfyUI environment for the NWTIA Flux.2 Klein Edit workflow"

