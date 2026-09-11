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
    ceremony = f'''
      <p class="message">We can't wait to celebrate with you.</p>
      <section class="ceremony" aria-labelledby="ceremony-title">
        <p class="section-label">The ceremony</p>
        <h2 id="ceremony-title">{values['venue']}</h2>
        <p class="details">{values['location']}</p>
        <p class="date"><time datetime="{values['date_iso']}">{values['date_label']}</time></p>
        <p class="time">{values['time_label']}</p>
      </section>
      '''
    source = (REPO_ROOT / "index.html").read_text(encoding="utf-8")
    before, remainder = source.split("<!-- wedding-content:start -->", 1)
    _, after = remainder.split("<!-- wedding-content:end -->", 1)
    rendered = before + ceremony + after
    rendered = rendered.replace("<main>", '<main>\n      <p class="preview-note">Private local preview</p>', 1)
    (output / "index.html").write_text(rendered, encoding="utf-8")
    shutil.copyfile(REPO_ROOT / "styles.css", output / "styles.css")
    shutil.copyfile(REPO_ROOT / "guest-lookup.js", output / "guest-lookup.js")
    shutil.copyfile(REPO_ROOT / "site-config.json", output / "site-config.json")
    photo = private_path(DATA_DIR / "assets" / "engagement.jpg")
    if photo.is_file():
        (output / "assets").mkdir(exist_ok=True)
        shutil.copyfile(photo, output / "assets" / "engagement.jpg")
        template = Template((REPO_ROOT / "templates" / "welcome.html").read_text(encoding="utf-8"))
        (output / "welcome.html").write_text(template.substitute(values), encoding="utf-8")
        shutil.copyfile(REPO_ROOT / "welcome.css", output / "welcome.css")
        shutil.copyfile(REPO_ROOT / "welcome.js", output / "welcome.js")
        print(output / "welcome.html")
    print(output / "index.html")


if __name__ == "__main__":
    build_preview()
