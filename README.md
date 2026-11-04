# NWTIA Runpod templates

NWTIA one-click RunPod images:

- `NWTIA Flux.2 Klein Edit`
- `NWTIA Krea2 Photoreal`
- [`NWTIA OpenJutsu V3`](openjutsu-v3/README.md) — H3, Qwen 4B + ClipProj, LoRA and tracked foreground protection

The images contain ComfyUI, the workflow, custom-node installers and verified download manifests. Model weights are downloaded into `/workspace` on first launch and are not redistributed inside the container image. Krea2 uses the public `enzinoai/IntoRealism-Krea-2` Hugging Face repository and requires no CivitAI token.

Recommended Runpod configuration: NVIDIA GPU with 48 GB VRAM, 10 GB container disk, 80 GB persistent volume mounted at `/workspace`, and HTTP ports `2999`, `3000`, `7777`, `8000`, and `8888` plus TCP port `22`.

OpenJutsu V3 uses a 20 GB container disk and a 160 GB persistent volume. It exposes only SSH (TCP 22); access the password-free local UI through an SSH tunnel. Model weights are downloaded and SHA256-verified on first launch. No cloud generation benchmark has been run.
