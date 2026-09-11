# Emily & James's Wedding Website

A simple wedding website built with HTML and CSS. No dependencies or paid server required for the starter page.

## Preview locally

Open `index.html` in a browser, or run:

```powershell
python -m http.server 8080 --bind 127.0.0.1
```

Then visit http://localhost:8080. Stop the server with Ctrl+C.

## Hosting

Repository: https://github.com/SlapDaddyMoshKing/james-and-emily-wedding

Website: https://slapdaddymoshking.github.io/james-and-emily-wedding/

GitHub Pages publishes from the root directory of the `main` branch. To publish updates:

```powershell
git add .
git commit -m "Update wedding website"
git push
```

Pushing changes to `main` updates the live website automatically, usually within a few minutes. Hosting runs on GitHub, so your computer can be turned off.

The starter page requests that search engines avoid indexing it. This does not restrict access: anyone with its public URL can view it.

## Future features

Wedding details, travel information, photos, and registry links can be added here. RSVP submissions will need a separate form service or backend; this starter site does not collect responses. Keep guest lists and credentials out of the repository.

## Guest list

See [guest-list setup](docs/guest-list.md) for the private CSV format, local database importer, and invitation lookup. The live site is still public: guest authentication is not yet implemented, and the name lookup awaits hosted backend configuration. Keep real guest data outside this project folder.

To test name lookup locally after importing the guest CSV, run `python -m backend.server` and visit http://127.0.0.1:8080. Names are checked on the server; a match does not grant access to private content. Run backend checks with `python -m unittest discover -s tests -v`.

## Content and design

Always put Emily's name before James's in displayed names and copy. The palette is light pastel blue and orange, with green and yellow accents.

The public page contains a coming-soon message. Ceremony content is stored privately in `%LOCALAPPDATA%\WeddingSiteData\wedding-content.json` until authenticated hosting is ready. Rebuild the private local preview after styling or content changes:

```powershell
python scripts/build_private_preview.py
```

Open `%LOCALAPPDATA%\WeddingSiteData\preview\index.html` in a browser. This preview is local only; publishing the ceremony details requires the approved-guest access control described in the guest-list guide.

The guest welcome-page design is in `templates/welcome.html` and `welcome.css`. The builder substitutes private ceremony details and copies `%LOCALAPPDATA%\WeddingSiteData\assets\engagement.jpg` into the private preview. Open `%LOCALAPPDATA%\WeddingSiteData\preview\welcome.html` to review it. The photo is displayed at its full portrait aspect ratio on both desktop and mobile.

This is a design preview for the page after guest verification. It is not a live redirect after name matching: email authentication and protected photo delivery are still required. Real ceremony details and the photo remain outside the public repository.
