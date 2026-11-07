"""Set the integration version in manifest.json during a release."""

import argparse
import json
import pathlib

MANIFEST = pathlib.Path("custom_components/toshiba_ac_link/manifest.json")


# ---------------------------
#   update_manifest_version
# ---------------------------
def update_manifest_version(version: str) -> None:
    """Write version into the manifest, keeping its formatting style."""
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest["version"] = version
    MANIFEST.write_text(json.dumps(manifest, indent=4) + "\n", encoding="utf-8")
    print(f"Set version {version} in {MANIFEST}")


# ---------------------------
#   main
# ---------------------------
def main() -> None:
    """Parse arguments and update the manifest.

    Errors are not caught on purpose: a missing or invalid manifest must
    fail the release job instead of shipping a "0.0.0" build.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True, help="Version to set, e.g. 1.5.0")
    args = parser.parse_args()
    update_manifest_version(args.version)


if __name__ == "__main__":
    main()
