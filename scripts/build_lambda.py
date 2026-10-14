"""Build the fitcheck-api Lambda package: Python 3.12 on arm64, as a reproducible zip.

Installs the runtime dependencies pinned in api/pyproject.toml for the Lambda
platform (not the build machine's), adds the fitcheck_api source and zips the
result with fixed timestamps and ordering, so the same inputs always produce
the same archive and source_code_hash.

Usage: python scripts/build_lambda.py [--output dist/fitcheck-api.zip]
"""

import argparse
import shutil
import subprocess
import sys
import tempfile
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API_DIR = ROOT / "api"
DEFAULT_OUTPUT = ROOT / "dist" / "fitcheck-api.zip"

LAMBDA_PLATFORM = "manylinux2014_aarch64"
LAMBDA_PYTHON = "3.12"
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


def runtime_dependencies() -> list[str]:
    pyproject = tomllib.loads((API_DIR / "pyproject.toml").read_text(encoding="utf-8"))
    return pyproject["project"]["dependencies"]


def install_dependencies(target: Path) -> None:
    subprocess.run(
        [
            sys.executable, "-m", "pip", "install",
            "--quiet",
            "--disable-pip-version-check",
            "--no-compile",
            "--only-binary=:all:",
            "--platform", LAMBDA_PLATFORM,
            "--implementation", "cp",
            "--python-version", LAMBDA_PYTHON,
            "--target", str(target),
            *runtime_dependencies(),
        ],
        check=True,
    )


def write_zip(source: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    files = sorted(path for path in source.rglob("*") if path.is_file())
    with zipfile.ZipFile(output, "w") as archive:
        for path in files:
            entry = zipfile.ZipInfo(path.relative_to(source).as_posix(), ZIP_TIMESTAMP)
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o644 << 16
            archive.writestr(entry, path.read_bytes())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        package = Path(tmp) / "package"
        install_dependencies(package)
        shutil.copytree(
            API_DIR / "src" / "fitcheck_api",
            package / "fitcheck_api",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        write_zip(package, args.output)

    print(args.output)


if __name__ == "__main__":
    main()
