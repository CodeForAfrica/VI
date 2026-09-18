"""Prepare the real model cache, then boot the local inference API.

This is only an end-to-end test entrypoint. Production receives the same model
cache from the host deployment and starts Gunicorn directly.
"""
import os
import shutil
import sys
import zipfile
from pathlib import Path

import boto3


CACHE = Path(os.environ.get("MODEL_CACHE_DIR", "/models"))
BUCKET = os.environ.get(
    "S3_MODELS_BUCKET", "cfa-vulnerability-index-sandbox-models"
)
ARCHIVE_KEY = "model_cache_archives/model_cache_local_working.zip"
REQUIRED = ("strategic_model", "tone_model")


def cache_ready():
    return all((CACHE / name).is_dir() for name in REQUIRED)


def safe_extract(archive, destination):
    destination = destination.resolve()
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            target = (destination / member.filename).resolve()
            if destination not in target.parents and target != destination:
                raise RuntimeError(f"unsafe model archive member: {member.filename}")
        bundle.extractall(destination)


def find_model(root, name):
    matches = [path for path in root.rglob(name) if path.is_dir()]
    if len(matches) != 1:
        raise RuntimeError(
            f"expected one {name} directory in the model archive, found {len(matches)}"
        )
    return matches[0]


def prepare_cache():
    CACHE.mkdir(parents=True, exist_ok=True)
    if cache_ready():
        print("E2E model cache ready; reusing local files", flush=True)
        return

    archive = CACHE / ".model-cache.zip"
    partial = CACHE / ".model-cache.zip.partial"
    extracted = CACHE / ".model-cache-extracted"
    shutil.rmtree(extracted, ignore_errors=True)
    partial.unlink(missing_ok=True)

    if not archive.exists():
        print(f"Downloading s3://{BUCKET}/{ARCHIVE_KEY}", flush=True)
        boto3.client("s3").download_file(BUCKET, ARCHIVE_KEY, str(partial))
        partial.replace(archive)

    print("Extracting model cache archive", flush=True)
    extracted.mkdir(parents=True)
    safe_extract(archive, extracted)
    for name in REQUIRED:
        source = find_model(extracted, name)
        destination = CACHE / name
        shutil.rmtree(destination, ignore_errors=True)
        shutil.move(str(source), str(destination))

    # Preserve a bundled Hugging Face cache when the archive contains one.
    hf_matches = [path for path in extracted.rglob("hf") if path.is_dir()]
    if hf_matches and not (CACHE / "hf").exists():
        shutil.move(str(hf_matches[0]), str(CACHE / "hf"))

    shutil.rmtree(extracted, ignore_errors=True)
    archive.unlink(missing_ok=True)
    if not cache_ready():
        raise RuntimeError("model cache preparation completed without required models")
    print("E2E model cache prepared", flush=True)


def main():
    prepare_cache()
    os.execvp(
        "gunicorn",
        [
            "gunicorn",
            "config.inference_wsgi:application",
            "--workers",
            "1",
            "--threads",
            "2",
            "--timeout",
            "180",
            "--bind",
            "0.0.0.0:8000",
        ],
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"E2E inference startup failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
