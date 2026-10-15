"""Build the fitcheck-api Lambda package: Python 3.12 on arm64, as a reproducible zip.

Installs the runtime dependencies pinned in api/pyproject.toml for the Lambda
platform (not the build machine's), adds the fitcheck_api source and the search
index, and zips the result with fixed timestamps and ordering, so the same
inputs always produce the same archive and source_code_hash.

The index is either a real one from scripts/build_index.py (--index), which
deploys need, or a placeholder (--placeholder-index) for pull request plans,
which have no Bedrock access. The Lambda refuses to search with a placeholder.

Usage:
  python scripts/build_lambda.py --index build/index.json [--output dist/fitcheck-api.zip]
  python scripts/build_lambda.py --placeholder-index [--output dist/fitcheck-api.zip]
"""

import argparse
import json
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
# Where fitcheck_api.handler looks for the index inside the package.
INDEX_ENTRY = Path("fitcheck_api") / "data" / "index.json"

LAMBDA_PLATFORM = "manylinux2014_aarch64"
LAMBDA_PYTHON = "3.12"
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


def runtime_dependencies() -> list[str]:
    pyproject = tomllib.loads((API_DIR / "pyproject.toml").read_text(encoding="utf-8"))
    return pyproject["project"]["dependencies"]


def embedding_config() -> dict:
    return json.loads((ROOT / "config" / "models.json").read_text(encoding="utf-8"))["embedding"]


def retrieval_config() -> dict:
    return json.loads((ROOT / "config" / "retrieval.json").read_text(encoding="utf-8"))


def placeholder_index() -> dict:
    config = embedding_config()
    return {
        "index_version": "placeholder",
        "model": config["model_id"],
        "dimensions": int(config["dimensions"]),
        "placeholder": True,
        "retrieval": retrieval_config(),
        "cards": [],
        "chunks": [],
        "vectors": [],
    }


def checked_index(path: Path) -> dict:
    """A real index that matches config/models.json; anything else stops the build."""
    index = json.loads(path.read_text(encoding="utf-8"))
    config = embedding_config()
    problems = []
    if index.get("placeholder"):
        problems.append("it is a placeholder")
    if index.get("model") != config["model_id"] or index.get("dimensions") != int(config["dimensions"]):
        problems.append(
            f"it was built with {index.get('model')} at {index.get('dimensions')} dimensions, "
            f"config/models.json says {config['model_id']} at {config['dimensions']}"
        )
    if index.get("retrieval") != retrieval_config():
        problems.append(f"its retrieval settings {index.get('retrieval')} differ from config/retrieval.json")
    cards, chunks, vectors = index.get("cards") or [], index.get("chunks") or [], index.get("vectors") or []
    if not cards or not chunks or len(chunks) != len(vectors):
        problems.append(f"it has {len(cards)} cards, {len(chunks)} chunks and {len(vectors)} vectors")
    if any(len(vector) != index.get("dimensions") for vector in vectors):
        problems.append("some vector has the wrong number of dimensions")
    if problems:
        sys.exit(f"refusing to package {path}: " + "; ".join(problems))
    return index


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


def check_native_wheels(output: Path) -> None:
    """pydantic-core is compiled; make sure the Lambda's build of it is the one packaged."""
    with zipfile.ZipFile(output) as archive:
        names = archive.namelist()
    if not any(name.startswith("pydantic_core/") and name.endswith("-aarch64-linux-gnu.so") for name in names):
        sys.exit(f"{output} has no pydantic_core build for {LAMBDA_PLATFORM}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--index", type=Path, help="index.json from scripts/build_index.py")
    source.add_argument("--placeholder-index", action="store_true", help="package a placeholder index (plans only)")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    index = placeholder_index() if args.placeholder_index else checked_index(args.index)

    with tempfile.TemporaryDirectory() as tmp:
        package = Path(tmp) / "package"
        install_dependencies(package)
        shutil.copytree(
            API_DIR / "src" / "fitcheck_api",
            package / "fitcheck_api",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "data"),
        )
        (package / INDEX_ENTRY).parent.mkdir(parents=True, exist_ok=True)
        (package / INDEX_ENTRY).write_text(
            json.dumps(index, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n", encoding="utf-8"
        )
        write_zip(package, args.output)

    check_native_wheels(args.output)
    print(f"{args.output} (index {index['index_version']})")


if __name__ == "__main__":
    main()
