"""Build the Lambda ZIP, including the Excel-writing dependency, outside the repo."""
import argparse
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.guest_list import DATA_DIR, REPO_ROOT, private_path

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DATA_DIR / "deploy" / "wedding-contact-intake.zip")
    args = parser.parse_args()
    output = private_path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        dependencies = Path(directory)
        subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "--target", str(dependencies),
            "-r", str(REPO_ROOT / "requirements-lambda.txt")], check=True)
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            for source in dependencies.rglob("*"):
                if source.is_file() and "__pycache__" not in source.parts:
                    archive.write(source, source.relative_to(dependencies).as_posix())
            for folder in ("backend", "scripts"):
                for source in (REPO_ROOT / folder).glob("*.py"):
                    archive.write(source, source.relative_to(REPO_ROOT).as_posix())
    print(output)

if __name__ == "__main__":
    main()
