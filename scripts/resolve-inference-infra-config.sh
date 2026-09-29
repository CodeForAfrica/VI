#!/usr/bin/env bash

set -euo pipefail

log() { echo "[resolve] $*"; }
fail() { echo "[resolve] ERROR - $*" >&2; exit 1; }

[[ -n "${VI_MODEL_INFERENCE_PULUMI_STACK:-}" ]] || fail "VI_MODEL_INFERENCE_PULUMI_STACK is not set."
[[ -n "${PULUMI_ACCESS_TOKEN:-}" ]] || fail "PULUMI_ACCESS_TOKEN is not set."
command -v pulumi >/dev/null 2>&1 || fail "pulumi CLI is not installed."
command -v jq >/dev/null 2>&1 || fail "jq is not installed."

github_output="${GITHUB_OUTPUT:-/dev/stdout}"
pulumi_error="$(mktemp)"
trap 'rm -f "$pulumi_error"' EXIT

log "Reading deployment values from ${VI_MODEL_INFERENCE_PULUMI_STACK}."
if ! outputs="$(pulumi stack output --json --stack "$VI_MODEL_INFERENCE_PULUMI_STACK" 2>"$pulumi_error")"; then
  sed 's/^/[resolve] /' "$pulumi_error" >&2
  fail "Pulumi stack outputs could not be read."
fi

missing=0
emit() {
  local output_name="$1" stack_key="$2" value
  value="$(printf '%s' "$outputs" | jq -r --arg key "$stack_key" '.[$key] // ""')"
  if [[ -z "$value" || "$value" == "null" ]]; then
    echo "[resolve] ERROR - required Pulumi output '$stack_key' is missing." >&2
    missing=1
    return
  fi
  printf '%s=%s\n' "$output_name" "$value" >> "$github_output"
  log "Resolved ${output_name} from ${stack_key}."
}

emit deployment-mode viModelInferenceDeploymentMode
emit aws-region awsRegion
emit aws-role-to-assume viModelInferenceDeployGithubRoleArn
emit ecr-repository viModelInferenceRepositoryName
emit dokku-app-name viModelInferenceDokkuAppName
emit dokku-app-url viModelInferenceDokkuAppUrl
emit dokku-host-name-tag viModelInferenceDokkuHostNameTag

[[ "$missing" -eq 0 ]] || fail "Required infrastructure outputs are incomplete."
[[ "$(printf '%s' "$outputs" | jq -r '.viModelInferenceDeploymentMode // ""')" == "ec2-dokku" ]] || fail "The stack is not an EC2 Dokku deployment."
log "All deployment values resolved successfully."
