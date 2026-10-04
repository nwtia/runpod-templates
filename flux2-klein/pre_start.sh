#!/usr/bin/env bash
set -Eeuo pipefail

export DISABLE_AUTOLAUNCH=1
bash /pre_start-comfyui.sh
unset DISABLE_AUTOLAUNCH

mkdir -p /workspace/bootstrap /workspace/logs
cp /opt/nwtia/workflow.json "/workspace/bootstrap/FLux2Klein Edit.json"

if [[ ! -f /workspace/.nwtia-flux2-klein-ready ]]; then
  if /opt/nwtia/install.sh 2>&1 | tee /workspace/logs/nwtia-install.log; then
    touch /workspace/.nwtia-flux2-klein-ready
  else
    echo "NWTIA setup failed. See /workspace/logs/nwtia-install.log" >&2
  fi
fi

/start_comfyui.sh ${EXTRA_ARGS:-}

