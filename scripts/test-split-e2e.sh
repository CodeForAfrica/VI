#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
readonly REPO_ROOT
readonly DEPLOYED_API_URL="https://vi-model-inference.codeforafrica.org"
log() {
  printf '[test-split-e2e] %s\n' "$*"
}

fail() {
  printf '[test-split-e2e] ERROR: %s\n' "$*" >&2
  exit 1
}

require_command() {
  local command_name="$1"
  local install_hint="$2"

  command -v "${command_name}" >/dev/null 2>&1 || fail "${command_name} is required. ${install_hint}"
}

unavailable_model_files() {
  local model_path

  while IFS= read -r model_path; do
    [[ -n "${model_path}" ]] || continue
    if [[ ! -f "${REPO_ROOT}/${model_path}" ]] ||
      git -C "${REPO_ROOT}" lfs pointer --check --file="${REPO_ROOT}/${model_path}" >/dev/null 2>&1; then
      printf '%s\n' "${model_path}"
    fi
  done <<<"${tracked_model_files}"
}

require_command git "Install Git before running this test."
git -C "${REPO_ROOT}" rev-parse --is-inside-work-tree >/dev/null 2>&1 ||
  fail "${REPO_ROOT} is not a Git worktree."

remote_mode=false
compose_file="${REPO_ROOT}/docker-compose.e2e.yml"
compose_project="vi-split-e2e-local"
if [[ -n "${VI_INFERENCE_API_KEY:-}" ]]; then
  remote_mode=true
  compose_file="${REPO_ROOT}/docker-compose.e2e.remote.yml"
  compose_project="vi-split-e2e-remote"
  export VI_INFERENCE_API_URL="${VI_INFERENCE_API_URL:-${DEPLOYED_API_URL}}"
  log "API key supplied: using deployed inference API at ${VI_INFERENCE_API_URL}."
  log "Remote mode skips Git LFS, local model loading, and the local inference/TLS containers."
else
  log "No API key supplied: running the inference API and all models locally."

  git -C "${REPO_ROOT}" lfs version >/dev/null 2>&1 ||
    fail "Git LFS is required to download the model artifacts. Install it from https://git-lfs.com, then rerun 'make test-split-e2e'."

  if ! tracked_model_files="$(git -C "${REPO_ROOT}" lfs ls-files --name-only)"; then
    fail "Could not inspect the Git LFS model files. Review the Git LFS error above, then rerun 'make test-split-e2e'."
  fi
  readonly tracked_model_files
  [[ -n "${tracked_model_files}" ]] ||
    fail "No Git LFS model artifacts are tracked in this checkout. Confirm that this is a complete VI repository checkout."

  tracked_model_count="$(printf '%s\n' "${tracked_model_files}" | wc -l | tr -d ' ')"
  log "Checking ${tracked_model_count} Git LFS model artifacts."

  missing_models=()
  while IFS= read -r model_path; do
    missing_models+=("${model_path}")
  done < <(unavailable_model_files)
  if (( ${#missing_models[@]} > 0 )); then
    log "Models not found locally: ${#missing_models[@]} Git LFS artifact(s) are missing or have not been downloaded."
  else
    log "Model files are present locally; Git LFS will verify they are current."
  fi

  log "Running 'git lfs pull' for model_cache. A fresh checkout downloads approximately 13 GB; cached objects are reused."
  if ! git -C "${REPO_ROOT}" lfs pull --include='model_cache/**'; then
    fail "Git LFS could not prepare the local models. Review its error above, check network access, GitHub authentication, and LFS quota, then rerun 'make test-split-e2e'."
  fi

  missing_models=()
  while IFS= read -r model_path; do
    missing_models+=("${model_path}")
  done < <(unavailable_model_files)
  if (( ${#missing_models[@]} > 0 )); then
    printf '[test-split-e2e] ERROR: Git LFS finished, but %d model artifact(s) are still unavailable:\n' "${#missing_models[@]}" >&2
    printf '  - %s\n' "${missing_models[@]:0:10}" >&2
    (( ${#missing_models[@]} > 10 )) && printf '  - ... and %d more\n' "$(( ${#missing_models[@]} - 10 ))" >&2
    fail "The local model checkout is incomplete. Run 'git lfs pull --include=model_cache/**' for more detail."
  fi
  log "Git LFS model setup complete: all ${tracked_model_count} artifacts are available locally."
fi

require_command docker "Install Docker Desktop or Docker Engine before running this test."
log "Checking Docker and Docker Compose."
docker info >/dev/null 2>&1 ||
  fail "Docker is installed but the daemon is not reachable. Start Docker, then rerun 'make test-split-e2e'."
docker compose version >/dev/null 2>&1 ||
  fail "Docker Compose v2 is required. Install the Compose plugin, then rerun 'make test-split-e2e'."

cleanup_test_stack() {
  docker compose \
    --project-name "${compose_project}" \
    --project-directory "${REPO_ROOT}" \
    -f "${compose_file}" \
    down -v --remove-orphans >/dev/null 2>&1 || true
}

# Always start with a fresh isolated database and remove only this command's
# namespaced containers/volumes when it exits, whether it passes or fails.
cleanup_test_stack
trap cleanup_test_stack EXIT

if [[ "${remote_mode}" == false ]]; then
  docker_memory_bytes="$(docker info --format '{{.MemTotal}}')"
  readonly docker_memory_bytes
  [[ "${docker_memory_bytes}" =~ ^[0-9]+$ ]] ||
    fail "Could not determine the memory available to Docker. Check Docker Desktop resources, then rerun 'make test-split-e2e'."
  docker_memory_mib=$(( docker_memory_bytes / 1024 / 1024 ))
  readonly docker_memory_mib
  if (( docker_memory_mib < 19000 )); then
    fail "Docker exposes only ${docker_memory_mib} MiB to containers. Configure Docker Desktop with at least 20 GB of memory; 16 GB was not enough to load the complete strategic and tone ensembles."
  fi
  log "Docker memory check passed: ${docker_memory_mib} MiB available to containers."
fi

if [[ "${remote_mode}" == true ]]; then
  log "Spinning up isolated Postgres and the Lambda verifier; inference stays on the deployed server."
else
  log "Spinning up the production-shaped Docker stack (Postgres, inference API, local HTTPS, and Lambda verifier)."
  log "Building images and running ten sample articles end to end. Model loading can take several minutes."
fi

if ! docker compose \
  --project-name "${compose_project}" \
  --project-directory "${REPO_ROOT}" \
  -f "${compose_file}" \
  up --build --abort-on-container-exit --exit-code-from e2e; then
  fail "Docker could not build the images or complete the end-to-end test. The exact build or service error is shown immediately above. The temporary stack is cleaned automatically; fix the reported problem and rerun."
fi

log "End-to-end test passed: Lambda reached the inference API over HTTPS and all sample results were verified."
log "Additional verification passed: all 10 articles completed fixture -> pending queue -> HTTPS inference -> Lambda pipeline -> Postgres in order, and every API prediction matched the saved intent/tone/confidence."
