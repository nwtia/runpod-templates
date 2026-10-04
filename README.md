# NWTIA Runpod templates

Two public, one-click ComfyUI images:

- `NWTIA Flux.2 Klein Edit`
- `NWTIA Krea2 Photoreal`

The images contain ComfyUI, the workflow, custom-node installers and verified download manifests. Model weights are downloaded into `/workspace` on first launch and are not redistributed inside the container image.

Krea2 requires the user to provide a valid `CIVITAI_TOKEN` environment variable for the IntoRealism Krea2 checkpoint. Never publish a personal token in a template.

Recommended Runpod configuration: NVIDIA GPU with 48 GB VRAM, 10 GB container disk, 80 GB persistent volume mounted at `/workspace`, and HTTP ports `2999`, `3000`, `7777`, `8000`, and `8888` plus TCP port `22`.
