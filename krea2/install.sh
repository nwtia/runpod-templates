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
  local args=(-fL --retry 8 --retry-all-errors --connect-timeout 20 -C - -o "$dest.part")
  curl "${args[@]}" "$url"
  echo "$sha  $dest.part" | sha256sum -c -
  mv "$dest.part" "$dest"
}

install_node() {
  local repo="$1" dir="$2"
  local target="$COMFY_ROOT/custom_nodes/$dir"
  if [[ ! -d "$target/.git" ]]; then
    git clone --filter=blob:none "$repo" "$target"
  fi
  if [[ -f "$target/requirements.txt" ]]; then
    "$PYTHON_BIN" -m pip install --no-cache-dir -r "$target/requirements.txt"
  fi
}

mkdir -p "$COMFY_ROOT/models/diffusion_models" "$COMFY_ROOT/models/text_encoders" \
  "$COMFY_ROOT/models/vae" "$COMFY_ROOT/models/loras" "$COMFY_ROOT/user/default/workflows"

download \
  "https://huggingface.co/Comfy-Org/Krea-2/resolve/main/text_encoders/qwen3vl_4b_fp8_scaled.safetensors" \
  "$COMFY_ROOT/models/text_encoders/qwen3vl_4b_fp8_scaled.safetensors" \
  "54bd5144df0bbc25dd6ccadfcb826b521445a1b06ae5a42570bdd2974ca87094"
download \
  "https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files/vae/wan_2.1_vae.safetensors" \
  "$COMFY_ROOT/models/vae/wan_2.1_vae.safetensors" \
  "2fc39d31359a4b0a64f55876d8ff7fa8d780956ae2cb13463b0223e15148976b"
download \
  "https://huggingface.co/lvladikov/Krea2-Turbo-Distill-4step-LoRA/resolve/main/krea2_turbo_4step_rank_64_lora.safetensors" \
  "$COMFY_ROOT/models/loras/krea2_turbo_4step_rank_64_lora.safetensors" \
  "20c1fb1bb66477fb3b19e501d69b55e1d2a25c7ce6550bb142529220838d80a4"
download \
  "https://huggingface.co/Kutches/Kr3a/resolve/main/snofs_krea_v1_1.safetensors" \
  "$COMFY_ROOT/models/loras/snofs_krea_v1_1.safetensors" \
  "af6f66910ab1c0ec158b83cdd551a6ccfd14d0f2f79ceee7485f42fddac90a65"
download \
  "https://huggingface.co/carlperez1933/my-loras/resolve/main/krea2/realcumk4.safetensors" \
  "$COMFY_ROOT/models/loras/realcumkrea4.safetensors" \
  "ed63d69a856c7b6bff0ce60e3a8539ff4758ee5327e808536635981de8eaada7"
download \
  "https://huggingface.co/Kutches/Kr3a/resolve/main/nicegirls_krea2.safetensors" \
  "$COMFY_ROOT/models/loras/nicegirls_krea2.safetensors" \
  "25847a032823dbd634e5b9f79866319b174557bce753367e4a08fddfea7eb57b"

download \
  "https://huggingface.co/enzinoai/IntoRealism-Krea-2/resolve/main/Krea2IntoRealismV1-Int8.safetensors" \
  "$COMFY_ROOT/models/diffusion_models/Krea2IntoRealismV1-Int8.safetensors" \
  "8b0a8dea2a7aeb62ddb195d430e86d77d3631f5a5cc68f765d4b3a945fdef4a5"

install_node "https://github.com/rgthree/rgthree-comfy.git" "rgthree-comfy"
install_node "https://github.com/yolain/ComfyUI-Easy-Use.git" "comfyui-easy-use"

for workflow in "/workspace/Krea2_workflow.json" "/workspace/Krea2.json" "/workspace/bootstrap/Krea2.json"; do
  if [[ -f "$workflow" ]]; then
    cp "$workflow" "$COMFY_ROOT/user/default/workflows/Krea2.json"
    break
  fi
done

echo "Krea2 prêt. Redémarre ComfyUI pour charger les nodes et le workflow."
