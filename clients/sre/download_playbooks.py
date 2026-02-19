"""
Download K8s SRE playbooks from Scoutflo/Scoutflo-SRE-Playbooks to a local
directory so PlaybookRetriever can work offline.

Usage:
    python clients/sre/download_playbooks.py
    python clients/sre/download_playbooks.py --output-dir /path/to/playbooks
"""

import argparse
import time
from pathlib import Path

import requests

GITHUB_API_BASE = "https://api.github.com/repos/Scoutflo/Scoutflo-SRE-Playbooks/contents"

K8S_CATEGORIES = {
    "Control-Plane":        "K8s Playbooks/01-Control-Plane",
    "Nodes":                "K8s Playbooks/02-Nodes",
    "Pods":                 "K8s Playbooks/03-Pods",
    "Workloads":            "K8s Playbooks/04-Workloads",
    "Networking":           "K8s Playbooks/05-Networking",
    "Storage":              "K8s Playbooks/06-Storage",
    "RBAC":                 "K8s Playbooks/07-RBAC",
    "Configuration":        "K8s Playbooks/08-Configuration",
    "Resource-Management":  "K8s Playbooks/09-Resource-Management",
    "Monitoring":           "K8s Playbooks/10-Monitoring-Autoscaling",
    "Namespaces":           "K8s Playbooks/12-Namespaces",
}

# Default location: next to this file
DEFAULT_OUTPUT_DIR = Path(__file__).parent / "playbooks"


def list_category(category_path: str) -> list[dict]:
    """Return the list of .md file entries in a GitHub contents directory."""
    url = f"{GITHUB_API_BASE}/{category_path}"
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    return [e for e in resp.json() if e["name"].endswith(".md")]


def download_file(download_url: str, dest: Path) -> None:
    resp = requests.get(download_url, timeout=15)
    resp.raise_for_status()
    dest.write_text(resp.text, encoding="utf-8")


def download_playbooks(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    total = 0
    skipped = 0

    for category_name, category_path in K8S_CATEGORIES.items():
        dest_dir = output_dir / category_name
        dest_dir.mkdir(parents=True, exist_ok=True)

        print(f"  Fetching index: {category_path} ...", end=" ", flush=True)
        try:
            entries = list_category(category_path)
        except Exception as e:
            print(f"FAILED ({e})")
            continue
        print(f"{len(entries)} playbooks")

        for entry in entries:
            dest_file = dest_dir / entry["name"]
            if dest_file.exists():
                skipped += 1
                continue
            try:
                download_file(entry["download_url"], dest_file)
                total += 1
                # Stay within GitHub's unauthenticated rate limit (60 req/min)
                time.sleep(0.05)
            except Exception as e:
                print(f"    [WARN] Could not download {entry['name']}: {e}")

    print(f"\nDone. Downloaded {total} new playbooks, {skipped} already present.")
    print(f"Saved to: {output_dir.resolve()}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Download Scoutflo K8s SRE playbooks locally.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Directory to save playbooks (default: {DEFAULT_OUTPUT_DIR})",
    )
    args = parser.parse_args()

    print(f"Downloading K8s SRE playbooks to: {args.output_dir.resolve()}")
    download_playbooks(args.output_dir)
