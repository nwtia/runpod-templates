#!/usr/bin/env bash
set -Eeuo pipefail

COMFY_ROOT="${COMFY_ROOT:-/workspace/ComfyUI}"
if [[ ! -d "$COMFY_ROOT" && -d /workspace/runpod-slim/ComfyUI ]]; then
  COMFY_ROOT=/workspace/runpod-slim/ComfyUI
fi
[[ -d "$COMFY_ROOT" ]] || { echo "ComfyUI introuvable" >&2; exit 1; }

PYTHON_BIN="${PYTHON_BIN:-}"
if [[ -z "$PYTHON_BIN" ]]; then
  for candidate in /workspace/venv/bin/python "$COMFY_ROOT/venv/bin/python" python3; do
    if command -v "$candidate" >/dev/null 2>&1 || [[ -x "$candidate" ]]; then
      PYTHON_BIN="$candidate"
      break
    fi
  done
fi

download() {
  local url="$1" dest="$2" sha="$3"
  mkdir -p "$(dirname "$dest")"
  if [[ -f "$dest" ]] && echo "$sha  $dest" | sha256sum -c - >/dev/null 2>&1; then
    echo "OK: $(basename "$dest")"
    return
  fi
  echo "Download: $(basename "$dest")"
  curl -fL --retry 8 --retry-all-errors --connect-timeout 20 -C - -o "$dest.part" "$url"
  echo "$sha  $dest.part" | sha256sum -c -
  mv "$dest.part" "$dest"
}

install_node() {
  local repo="$1" dir="$2" ref="${3:-}"
  local target="$COMFY_ROOT/custom_nodes/$dir"
  if [[ ! -d "$target/.git" ]]; then
    git clone --filter=blob:none "$repo" "$target"
  fi
  git -C "$target" fetch --tags --quiet
  if [[ -n "$ref" ]]; then git -C "$target" checkout --quiet "$ref"; fi
  if [[ -f "$target/requirements.txt" ]]; then
    "$PYTHON_BIN" -m pip install --no-cache-dir -r "$target/requirements.txt"
  fi
}

mkdir -p "$COMFY_ROOT/models/diffusion_models" "$COMFY_ROOT/models/text_encoders" \
  "$COMFY_ROOT/models/vae" "$COMFY_ROOT/user/default/workflows"

download \
  "https://huggingface.co/evag3/pornmaster-flux/resolve/main/pornmasterFlux2Klein_v4TurboFp8.safetensors" \
  "$COMFY_ROOT/models/diffusion_models/pornmasterFlux2Klein_v4TurboFp8.safetensors" \
  "e90eeb50140a10806341b7521c340214c6f76cec2f8f8dae7a443c5806072df7"
download \
  "https://huggingface.co/Comfy-Org/vae-text-encorder-for-flux-klein-9b/resolve/main/split_files/text_encoders/qwen_3_8b_fp8mixed.safetensors" \
  "$COMFY_ROOT/models/text_encoders/qwen_3_8b_fp8mixed.safetensors" \
  "abad16806e0cbabc54e0325d6565847443fe396d5f0be38bb3cd3fe75a1201d6"
download \
  "https://huggingface.co/black-forest-labs/FLUX.2-small-decoder/resolve/main/full_encoder_small_decoder.safetensors" \
  "$COMFY_ROOT/models/vae/full_encoder_small_decoder.safetensors" \
  "ea4273f02d1fafbf8e1d1c2cf6018ed8748652eb0bf34f2dd91171f16f15ab62"

install_node "https://github.com/rgthree/rgthree-comfy.git" "rgthree-comfy"
install_node "https://github.com/willmiao/ComfyUI-Lora-Manager.git" "comfyui-lora-manager"
install_node "https://github.com/pythongosssss/ComfyUI-Custom-Scripts.git" "comfyui-custom-scripts"
install_node "https://github.com/yolain/ComfyUI-Easy-Use.git" "comfyui-easy-use"

for workflow in "/workspace/FLux2Klein_Edit.json" "/workspace/FLux2Klein Edit.json" "/workspace/bootstrap/FLux2Klein Edit.json"; do
  if [[ -f "$workflow" ]]; then
    cp "$workflow" "$COMFY_ROOT/user/default/workflows/FLux2Klein Edit.json"
    break
  fi
done

echo "Flux2 Klein prêt. Redémarre ComfyUI pour charger les nodes et le workflow."
