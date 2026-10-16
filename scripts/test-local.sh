#!/usr/bin/env bash
set -euo pipefail
set +x

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly REPO_ROOT
readonly project="vi-dashboard-local-check"
stage="preflight"
stage_started=$SECONDS
run_started=$SECONDS
live_mode=false
credentials_directory=""
readonly results_directory="${REPO_ROOT}/.dashboard-check"
readonly scanner='aquasec/trivy@sha256:be1190afcb28352bfddc4ddeb71470835d16462af68d310f9f4bca710961a41e'
compose_files=(-f "${REPO_ROOT}/docker-compose.local-test.yml")
base_compose() { docker compose --project-name "${project}" --project-directory "${REPO_ROOT}" -f "${REPO_ROOT}/docker-compose.local-test.yml" "$@"; }
compose() { docker compose --project-name "${project}" --project-directory "${REPO_ROOT}" "${compose_files[@]}" "$@"; }
log() { printf '[run-local-dashboard] %s\n' "$*"; }
fail() { printf '[run-local-dashboard] ERROR: %s\n' "$*" >&2; exit 1; }
set_stage() {
  log "TIMING: ${stage}: $((SECONDS - stage_started))s."
  stage="$1"
  stage_started=$SECONDS
}
cleanup() {
  local status=$?
  trap - EXIT
  if (( status != 0 )); then
    rm -f "${results_directory}/passed.json"
    printf '[run-local-dashboard] FAILED during %s. Review the error above; no deployment occurred.\n' "${stage}" >&2
    compose logs --no-color --tail=60 dashboard db redis >&2 || true
  fi
  log "Removing this test project's containers and fixture volumes (keeping cached images)."
  if ! compose down --volumes --remove-orphans; then
    printf '[run-local-dashboard] ERROR: Cleanup failed. Run docker compose -p %s -f docker-compose.local-test.yml down -v\n' "${project}" >&2
    status=1
    rm -f "${results_directory}/passed.json"
  fi
  if [[ -n "${credentials_directory}" ]]; then
    rm -f "${credentials_directory}/aws.json" "${credentials_directory}/groq.key"
    rmdir "${credentials_directory}"
    log "Temporary provider credentials removed."
  fi
  rm -f "${results_directory}/image.tar.gz"
  rmdir "${results_directory}/running"
  log "TIMING: total including cleanup: $((SECONDS - run_started))s (exit ${status})."
  exit "${status}"
}

mkdir -p "${results_directory}"
if [[ -d "${results_directory}/running" ]]; then
  fail "A dashboard check owns .dashboard-check/running. Wait for it to finish. If it was killed, first confirm its containers/processes stopped, then remove that empty lock directory."
fi
rm -f "${results_directory}/passed.json" "${results_directory}/security.json"
for tool in git python3 gzip; do
  command -v "$tool" >/dev/null || fail "$tool is missing. Install it before running the dashboard checks."
done
unset VI_DASHBOARD_IMAGE
export VI_SOURCE_REVISION
VI_SOURCE_REVISION="$(git -C "${REPO_ROOT}" rev-parse HEAD)"
command -v docker >/dev/null 2>&1 || fail "Docker is missing. Install Docker Desktop (or Docker Engine with Compose v2)."
docker info >/dev/null 2>&1 || fail "Docker is not reachable. Start Docker Desktop/Engine and rerun make run-local-dashboard."
docker compose version || fail "Docker Compose v2 is missing. Install the Compose plugin."
docker compose up --help | grep -- '--wait-timeout' >/dev/null || fail "Docker Compose is too old. Update Docker Desktop/Compose to a version supporting up --wait-timeout."
compose config --quiet || fail "The local test Compose configuration is invalid."
if [[ -n "$(compose ps --status running --quiet)" ]]; then
  fail "This test project is already running. Wait for the other make run-local-dashboard invocation to finish."
fi
mkdir "${results_directory}/running" || fail "Another dashboard check started; wait for it to finish."
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
export VI_LOCAL_AWS_LIVE=0 VI_LOCAL_GROQ_LIVE=0
if [[ -n "${AWS_PROFILE:-}" || -n "${GROQ_API_KEY:-}" ]]; then
  live_mode=true
  stage="preparing explicitly selected live provider credentials"
  log "Live provider checks selected: synthetic text only; provider charges may apply. Database remains disposable/local."
  umask 077
  credentials_directory="$(mktemp -d "${TMPDIR:-/tmp}/vi-local-provider-credentials.XXXXXX")"
  export VI_LOCAL_CREDENTIALS_DIR="${credentials_directory}"
  printf '{}\n' > "${credentials_directory}/aws.json"
  printf '%s' "${GROQ_API_KEY:-}" > "${credentials_directory}/groq.key"
  if [[ -n "${AWS_PROFILE:-}" ]]; then
    command -v aws >/dev/null 2>&1 || fail "AWS_PROFILE was supplied, but AWS CLI is missing. Install AWS CLI v2 and authenticate that profile."
    log "Resolving the selected AWS profile on this PC (no credential values are logged)."
    if ! aws --profile "${AWS_PROFILE}" configure export-credentials --format process > "${credentials_directory}/aws.json" 2>/dev/null; then
      fail "Cannot resolve AWS_PROFILE. Check the profile name, network and login/expiry (for SSO: aws sso login --profile <profile>). AWS CLI v2 with configure export-credentials is required."
    fi
    export VI_LOCAL_AWS_LIVE=1
    log "AWS profile credentials resolved; Amazon Translate will use region ${AWS_REGION:-eu-west-1}."
  fi
  if [[ -n "${GROQ_API_KEY:-}" ]]; then
    export VI_LOCAL_GROQ_LIVE=1
    log "Groq key supplied: chat and PDF analysis must succeed against Groq, not fallback text."
  fi
  # The containing directory stays 0700 on the host. Files are read-only
  # mounted as Compose secrets so the non-root app can read them in Docker.
  chmod 0644 "${credentials_directory}/aws.json" "${credentials_directory}/groq.key"
  compose_files+=(-f "${REPO_ROOT}/docker-compose.local-test.live.yml")
  compose config --quiet || fail "The live-provider Compose configuration is invalid."
else
  log "No provider credentials supplied: AWS Translate/Groq use controlled responses; runtime egress is disabled."
fi
if [[ -n "${VI_BUILD_CACHE_REPOSITORY:-}" ]]; then
  [[ "${VI_BUILD_CACHE_REPOSITORY}" =~ ^[a-z0-9][a-z0-9.-]*(:[0-9]+)?(/[a-z0-9][a-z0-9._-]*)+$ ]] \
    || fail "VI_BUILD_CACHE_REPOSITORY must be an untagged registry repository, without credentials, a URL scheme or cache options."
  compose_files+=(-f "${REPO_ROOT}/docker-compose.local-test.cache.yml")
  compose config --quiet || fail "The build-cache Compose configuration is invalid."
  log "Cross-run registry caching enabled for dashboard, catalogue and browser; only build layers are reused, never test results."
  log "Cache tags are separate from release tags. A first cache miss builds normally; cache export failures stop the run."
else
  log "Using this Docker builder's local cache; no registry cache will be read or written."
fi
log "Docker and Compose checks passed. Git LFS and ML models are not needed."
log "First build downloads dependencies/browser images; subsequent runs reuse Docker's cache."
set_stage "building the production dashboard, catalogue checker and browser images"
log "STEP 1/6: ${stage} (production platform linux/arm64)."
compose build dashboard checks browser
export VI_DASHBOARD_IMAGE
VI_DASHBOARD_IMAGE="$(docker image inspect vi-dashboard:local-test --format '{{.Id}}')"
[[ "${VI_DASHBOARD_IMAGE}" =~ ^sha256:[a-f0-9]{64}$ ]] || fail "Build did not produce an immutable local image ID."
log "Pinned subsequent checks to ${VI_DASHBOARD_IMAGE}; no test command will rebuild it."
set_stage "runtime dependency consistency and vulnerability scan"
log "STEP 2/6: Checking installed dependencies; scanning exact image OS/Python packages with a fresh Trivy database."
docker run --rm --network none "${VI_DASHBOARD_IMAGE}" python -m pip check
docker run --rm --network none "${VI_DASHBOARD_IMAGE}" python scripts/check_runtime_dependencies.py
docker image save "${VI_DASHBOARD_IMAGE}" | gzip > "${results_directory}/image.tar.gz"
docker run --rm -v "${results_directory}:/artifacts" -v vi-dashboard-trivy-cache:/root/.cache/trivy \
  "${scanner}" image --input /artifacts/image.tar.gz --scanners vuln --ignorefile /dev/null \
  --skip-version-check --format json --output /artifacts/security.json --timeout 10m
python3 "${REPO_ROOT}/scripts/dashboard_release.py" security --image "${VI_DASHBOARD_IMAGE}" --directory "${results_directory}"
rm -f "${results_directory}/image.tar.gz"
set_stage "French catalogue validation"
log "STEP 3/6: Checking all extracted UI messages for missing/fuzzy French translations and placeholder errors."
compose run --rm --no-deps --pull never checks python scripts/check_translations.py
set_stage "full automated suite"
log "STEP 4/6: Running the full suite inside the production dashboard image; external providers use controlled test responses."
log "Starting isolated Postgres/Redis so the three PostgreSQL locking tests run instead of being skipped."
compose up --detach --wait --wait-timeout 90 db redis
base_compose run --rm --no-deps --pull never dashboard python manage.py test --settings=inference_api.tests_settings --verbosity 1
set_stage "isolated database migration and fixture setup"
log "STEP 5/6: Starting isolated Postgres/Redis and loading synthetic saved article results (not model predictions)."
base_compose run --rm --no-deps --pull never dashboard python scripts/prepare_local_dashboard.py
set_stage "production WSGI startup and runtime imports"
log "Checking uninstrumented config.wsgi with the same Gunicorn executable and image as production; no external provider calls."
base_compose run --rm --no-deps --pull never dashboard python scripts/check_dashboard_startup.py
compose up --no-build --detach --wait --wait-timeout 120 dashboard
set_stage "English/French browser and PDF checks"
log "STEP 6/6: Testing real browser navigation, language switching, filters, articles, chat/CSRF and PDF responses."
compose run --rm --no-deps --pull never browser
set_stage "recording verified image identity"
python3 "${REPO_ROOT}/scripts/dashboard_release.py" receipt --image "${VI_DASHBOARD_IMAGE}" --directory "${results_directory}"
log "TIMING: ${stage}: $((SECONDS - stage_started))s."
log "PASS: Dashboard production image build, pinned runtime dependencies, production WSGI startup and French catalogue completeness."
log "PASS: Full automated suite; existing skips are reported above."
log "PASS: English/French browser checks and PDF generation."
if [[ "${live_mode}" == true ]]; then
  log "Provider modes and successful/failed call counts are reported above; supplied providers were verified live. Omitted providers stayed controlled."
else
  log "NOT RUN: Live Amazon Translate/Groq. Provider contracts/failures are tested with controlled responses."
fi
log "LOCAL APPLICATION CHECKS PASSED. Nothing deployed; ML inference/model accuracy are outside this dashboard-only run."
log "RELEASE CHECKS PASSED: live provider checks are optional. Publishing still requires a clean committed checkout and this exact checked image."
