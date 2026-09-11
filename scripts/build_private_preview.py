"""Build a local ceremony preview without putting private details in GitHub."""

import html
import json
import shutil

from guest_list import DATA_DIR, REPO_ROOT, private_path


def build_preview():
    content_path = private_path(DATA_DIR / "wedding-content.json")
    content = json.loads(content_path.read_text(encoding="utf-8-sig"))
    output = private_path(DATA_DIR / "preview")
    output.mkdir(parents=True, exist_ok=True)
    required = ("venue", "location", "date_label", "date_iso", "time_label")
    values = {key: html.escape(content[key], quote=True) for key in required}
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
    print(output / "index.html")


if __name__ == "__main__":
    build_preview()
