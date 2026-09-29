#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
readonly REPO_ROOT
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
  log "Pulling model artifacts via Git LFS. A fresh checkout downloads approximately 13 GB; this can take a while."

  if ! git -C "${REPO_ROOT}" lfs pull --include='model_cache/**'; then
    fail "Git LFS could not download the models. Review its error above, check network access, GitHub authentication, and LFS quota, then rerun 'make test-split-e2e'."
  fi

  missing_models=()
  while IFS= read -r model_path; do
    missing_models+=("${model_path}")
  done < <(unavailable_model_files)
  if (( ${#missing_models[@]} > 0 )); then
    printf '[test-split-e2e] ERROR: Git LFS finished, but %d model artifact(s) are still unavailable:\n' "${#missing_models[@]}" >&2
    printf '  - %s\n' "${missing_models[@]:0:10}" >&2
    (( ${#missing_models[@]} > 10 )) && printf '  - ... and %d more\n' "$(( ${#missing_models[@]} - 10 ))" >&2
    fail "The model checkout is incomplete. Run 'git lfs pull --include=model_cache/**' for more detail."
  fi

  log "Finished downloading and verifying the model artifacts."
else
  log "All Git LFS model artifacts are already available locally; no download is needed."
fi

require_command docker "Install Docker Desktop or Docker Engine before running this test."
log "Models are ready. Checking Docker and Docker Compose."
docker info >/dev/null 2>&1 ||
  fail "Docker is installed but the daemon is not reachable. Start Docker, then rerun 'make test-split-e2e'."
docker compose version >/dev/null 2>&1 ||
  fail "Docker Compose v2 is required. Install the Compose plugin, then rerun 'make test-split-e2e'."

log "Spinning up the production-shaped Docker stack (Postgres, inference API, local HTTPS, and Lambda verifier)."
log "Building images and running ten sample articles end to end. Model loading can take several minutes."

if ! docker compose \
  --project-directory "${REPO_ROOT}" \
  -f "${REPO_ROOT}/docker-compose.e2e.yml" \
  up --build --abort-on-container-exit --exit-code-from e2e; then
  fail "Docker could not build the images or complete the end-to-end test. The exact build or service error is shown immediately above. Fix it and rerun; use 'docker compose -f docker-compose.e2e.yml down -v' first if you want a clean database."
fi

log "End-to-end test passed: Lambda reached the inference API over HTTPS and all sample results were verified."
