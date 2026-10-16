"""Fail-closed image security policy and promotion of the image that passed checks."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

REQUIRED_CHECKS = ["pinned-dependencies", "pip-check", "vulnerabilities", "catalogues",
                   "full-suite", "production-startup", "browser-pdf"]


def command(*args):
    return subprocess.check_output(args, text=True).strip()


def inspect(image):
    return json.loads(command("docker", "image", "inspect", image))[0]


def identity(image):
    # Docker's containerd store may expose a manifest ID instead of a config ID.
    # Bind both runtime configuration and every ordered filesystem layer.
    return {key: runtime_config(image[key]) if key == "Config" else image[key]
            for key in ("Architecture", "Os", "Config", "RootFS")}


def runtime_config(config):
    return {key: value for key, value in config.items() if value not in (None, False, "", [], {})}


def check_security(report, image):
    metadata = report["Metadata"]
    if metadata.get("DiffIDs") != image["RootFS"]["Layers"]:
        raise ValueError("Security report belongs to different image layers")
    config = metadata["ImageConfig"]
    if config.get("architecture") != "arm64" or runtime_config(config.get("config", {})) != runtime_config(image["Config"]):
        raise ValueError("Security report runtime configuration does not match the tested ARM64 image")
    results = report["Results"]
    coverage = {(result.get("Class"), result.get("Type")) for result in results}
    if not {("os-pkgs", "debian"), ("lang-pkgs", "python-pkg")} <= coverage:
        raise ValueError("Security report is missing OS or installed Python coverage")
    counts = Counter()
    for result in results:
        for finding in result.get("Vulnerabilities", []):
            severity = finding["Severity"]
            counts[severity] += 1
            if severity in ("CRITICAL", "HIGH"):
                print(f"[security] {severity}: {finding['PkgName']} {finding['InstalledVersion']} "
                      f"{finding['VulnerabilityID']} fixed={finding.get('FixedVersion') or 'not available'}", flush=True)
    print(f"[security] All severities: {dict(counts)}. Blocking policy: any CRITICAL, including unfixed.", flush=True)
    if counts["CRITICAL"]:
        raise ValueError("Critical vulnerabilities found; upgrade dependencies/base image and rerun (no bypass)")
    return dict(counts)


def write_receipt(directory, image):
    directory = Path(directory)
    report_path = directory / "security.json"
    counts = check_security(json.loads(report_path.read_text()), image)
    if image["Architecture"] != "arm64":
        raise ValueError("The production dashboard requires an ARM64 image")
    receipt = {
        "schema": 2, "checks": REQUIRED_CHECKS, "image_id": image["Id"], "identity": identity(image),
        "commit": command("git", "rev-parse", "HEAD"),
        "dirty": bool(command("git", "status", "--porcelain", "--untracked-files=all")),
        "aws_live": os.environ.get("VI_LOCAL_AWS_LIVE") == "1",
        "groq_live": os.environ.get("VI_LOCAL_GROQ_LIVE") == "1",
        "run_id": os.environ.get("GITHUB_RUN_ID", "local"),
        "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT", "local"),
        "created": datetime.now(timezone.utc).isoformat(), "vulnerabilities": counts,
        "security_sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
    }
    (directory / "passed.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(f"[release] Checks passed for {image['Id']}; receipt written. Nothing published.", flush=True)


def verify_receipt(directory):
    directory = Path(directory)
    receipt = json.loads((directory / "passed.json").read_text())
    if receipt.get("schema") != 2 or receipt.get("checks") != REQUIRED_CHECKS:
        raise ValueError("Release receipt is missing required dependency/startup/application checks; rerun make run-local-dashboard")
    if receipt["dirty"] or command("git", "status", "--porcelain", "--untracked-files=all"):
        raise ValueError("Publishing requires a clean committed checkout; rerun checks after committing")
    if receipt["commit"] != command("git", "rev-parse", "HEAD"):
        raise ValueError("Receipt belongs to a different source commit")
    for field, variable in (("run_id", "GITHUB_RUN_ID"), ("run_attempt", "GITHUB_RUN_ATTEMPT")):
        if receipt[field] != os.environ.get(variable, "local"):
            raise ValueError("Receipt belongs to another workflow run/attempt")
    age = datetime.now(timezone.utc) - datetime.fromisoformat(receipt["created"])
    if not 0 <= age.total_seconds() <= 7200:
        raise ValueError("Receipt expired; rerun all checks (maximum age two hours)")
    security = directory / "security.json"
    if hashlib.sha256(security.read_bytes()).hexdigest() != receipt["security_sha256"]:
        raise ValueError("Security report changed after checks")
    image = inspect(receipt["image_id"])
    if identity(image) != receipt["identity"]:
        raise ValueError("Checked image identity changed")
    labels = image["Config"].get("Labels") or {}
    if labels.get("org.opencontainers.image.revision") != receipt["commit"]:
        raise ValueError("Image revision label does not match the checked source commit")
    check_security(json.loads(security.read_text()), image)
    return receipt


def publish(directory, repository):
    if not re.fullmatch(r"[a-z0-9][a-z0-9./_-]*", repository):
        raise ValueError("Provide an untagged Docker Hub repository")
    receipt = verify_receipt(directory)
    tag = f"{repository}:sha-{receipt['commit']}"
    print(f"[release] Publishing checked image {receipt['image_id']} as {tag}; no rebuild.", flush=True)
    subprocess.run(["docker", "tag", receipt["image_id"], tag], check=True)
    output = command("docker", "push", tag)
    print(output, flush=True)
    digests = re.findall(r"digest: (sha256:[a-f0-9]{64})", output)
    if len(set(digests)) != 1:
        raise ValueError("Registry push did not return exactly one immutable digest; no deployment permitted")
    digest = digests[0]
    reference = f"{repository}@{digest}"
    print(f"[release] Pulling {reference} to verify registry content matches the tested image.", flush=True)
    subprocess.run(["docker", "pull", "--platform", "linux/arm64", reference], check=True)
    if identity(inspect(reference)) != receipt["identity"]:
        raise ValueError("Published registry content differs from checked image; deployment blocked")
    print("[release] Registry content verified; updating compatibility main tag to the same checked image.", flush=True)
    subprocess.run(["docker", "tag", receipt["image_id"], f"{repository}:main"], check=True)
    print(command("docker", "push", f"{repository}:main"), flush=True)
    with open(os.environ["GITHUB_OUTPUT"], "a") as output_file:
        output_file.write(f"digest={digest}\n")
    print(f"[release] PASS: registry digest verified: {reference}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("security", "receipt", "verify", "publish"))
    parser.add_argument("--directory", default=".dashboard-check")
    parser.add_argument("--image")
    parser.add_argument("--repository")
    args = parser.parse_args()
    try:
        if args.action == "security":
            check_security(json.loads((Path(args.directory) / "security.json").read_text()), inspect(args.image))
        elif args.action == "receipt":
            write_receipt(args.directory, inspect(args.image))
        elif args.action == "verify":
            verify_receipt(args.directory)
        else:
            publish(args.directory, args.repository or "")
    except (ValueError, KeyError, TypeError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"[release] FAILED: {error}\n")


if __name__ == "__main__":
    main()
