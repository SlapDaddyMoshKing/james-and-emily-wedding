"""Publish the real welcome page into the public website.

Unlike build_private_preview.py, this writes files meant to be committed and
pushed: the real ceremony details and engagement photo become fetchable by
anyone who has (or guesses) the URL, once live on GitHub Pages. That's a
deliberate trade-off for this site (see docs/guest-list.md), not an
oversight -- but it is real, so this script only writes files; it does not
run git itself. Review the result, then commit and push when ready.
"""

import html
import json
import shutil
from string import Template
from urllib.parse import quote

from guest_list import DATA_DIR, REPO_ROOT, private_path


def publish():
    content_path = private_path(DATA_DIR / "wedding-content.json")
    content = json.loads(content_path.read_text(encoding="utf-8-sig"))
    required = ("venue", "location", "address", "date_label", "date_iso", "time_label")
    values = {key: html.escape(content[key], quote=True) for key in required}
    # Pre-encoded for use inside the map iframe/link src attributes below.
    values["map_query"] = html.escape(quote(f"{content['venue']}, {content['address']}"), quote=True)

    template_text = (REPO_ROOT / "templates" / "welcome.html").read_text(encoding="utf-8")
    # The "Private design preview" banner is only for the local walkthrough.
    template_text = template_text.replace('    <div class="preview-label">Private design preview</div>\n', "")
    (REPO_ROOT / "welcome.html").write_text(Template(template_text).substitute(values), encoding="utf-8")

    photo = private_path(DATA_DIR / "assets" / "engagement.jpg")
    (REPO_ROOT / "assets").mkdir(exist_ok=True)
    shutil.copyfile(photo, REPO_ROOT / "assets" / "engagement.jpg")

    print(REPO_ROOT / "welcome.html")
    print(REPO_ROOT / "assets" / "engagement.jpg")
    print("Not committed. Review, then `git add` + `git commit` + `git push` when ready to go live.")


if __name__ == "__main__":
    publish()
