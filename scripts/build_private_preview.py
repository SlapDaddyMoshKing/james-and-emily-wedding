"""Build a local ceremony preview without putting private details in GitHub."""

import html
import json
import shutil
from string import Template
from urllib.parse import quote

from guest_list import DATA_DIR, REPO_ROOT, private_path


def build_preview():
    content_path = private_path(DATA_DIR / "wedding-content.json")
    content = json.loads(content_path.read_text(encoding="utf-8-sig"))
    output = private_path(DATA_DIR / "preview")
    output.mkdir(parents=True, exist_ok=True)
    required = ("venue", "location", "address", "date_label", "date_iso", "time_label")
    values = {key: html.escape(content[key], quote=True) for key in required}
    # Pre-encoded for use inside the map iframe/link src attributes in welcome.html.
    values["map_query"] = html.escape(quote(f"{content['venue']}, {content['address']}"), quote=True)
    photo = private_path(DATA_DIR / "assets" / "engagement.jpg")
    if not photo.is_file():
        raise FileNotFoundError(f"Add the engagement photo at {photo} before building the preview.")
    (output / "assets").mkdir(exist_ok=True)
    shutil.copyfile(photo, output / "assets" / "engagement.jpg")
    template = Template((REPO_ROOT / "templates" / "welcome.html").read_text(encoding="utf-8"))
    (output / "welcome.html").write_text(template.substitute(values), encoding="utf-8")
    shutil.copyfile(REPO_ROOT / "welcome.css", output / "welcome.css")
    shutil.copyfile(REPO_ROOT / "welcome.js", output / "welcome.js")
    print(output / "welcome.html")


if __name__ == "__main__":
    build_preview()
