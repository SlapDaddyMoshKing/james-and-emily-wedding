# James & Emily's Wedding Website

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
