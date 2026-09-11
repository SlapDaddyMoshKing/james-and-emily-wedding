"use strict";

const openButton = document.querySelector("#open-lookup");
const panel = document.querySelector("#lookup-panel");
const form = document.querySelector("#guest-lookup");
const statusText = document.querySelector("#lookup-status");
const submitButton = form.querySelector('button[type="submit"]');
let lookupUrl = null;
let pending = false;
let configuring = true;
let focusTimer;

openButton.hidden = false;
panel.hidden = false;
openButton.addEventListener("click", (event) => {
  const opening = openButton.getAttribute("aria-expanded") !== "true";
  clearTimeout(focusTimer);
  openButton.setAttribute("aria-expanded", String(opening));
  panel.inert = !opening;
  panel.setAttribute("aria-hidden", String(!opening));
  panel.classList.toggle("is-open", opening);
  // Keyboard users move into the form after the reveal. Touch users can tap
  // a field when ready, so the mobile keyboard doesn't interrupt the animation.
  if (opening && event.detail === 0) {
    const delay = window.matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 280;
    focusTimer = setTimeout(() => {
      if (openButton.getAttribute("aria-expanded") === "true" && document.activeElement === openButton) {
        form.elements.first_name.focus({ preventScroll: true });
      }
    }, delay);
  }
});

async function configureLookup() {
  try {
    const response = await fetch(new URL("site-config.json", document.baseURI), {
      cache: "no-store",
      signal: AbortSignal.timeout(10000),
    });
    if (!response.ok) throw new Error("Configuration unavailable");
    const config = await response.json();
    if (typeof config.invitationLookupUrl !== "string" || !config.invitationLookupUrl.trim()) {
      throw new Error("Lookup not connected");
    }
    const endpoint = new URL(config.invitationLookupUrl, document.baseURI);
    const local = ["localhost", "127.0.0.1"].includes(endpoint.hostname);
    if (endpoint.protocol !== "https:" && !(local && endpoint.protocol === "http:")) {
      throw new Error("Unsupported lookup address");
    }
    lookupUrl = endpoint.href;
    statusText.textContent = "";
  } catch {
    // Keep the entry form quiet; explain availability only after a submission.
    lookupUrl = null;
  } finally {
    configuring = false;
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (pending || !form.reportValidity()) return;
  if (!lookupUrl) {
    statusText.textContent = configuring
      ? "The invitation check is still loading. Please try again in a moment."
      : "We couldn't check your invitation right now. Please try again later.";
    return;
  }
  const firstName = form.elements.first_name.value.trim();
  const lastName = form.elements.last_name.value.trim();
  if (!firstName || !lastName) {
    statusText.textContent = "Please enter both your first and last name.";
    return;
  }
  pending = true;
  submitButton.disabled = true;
  form.setAttribute("aria-busy", "true");
  statusText.textContent = "Checking your invitation…";
  try {
    const response = await fetch(lookupUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      cache: "no-store",
      body: JSON.stringify({ first_name: firstName, last_name: lastName }),
      signal: AbortSignal.timeout(10000),
    });
    if (response.status === 429) {
      statusText.textContent = "You've tried a few times. Please wait ten minutes before trying again.";
      return;
    }
    if (!response.ok) throw new Error("Lookup unavailable");
    const result = await response.json();
    if (typeof result.invited !== "boolean") throw new Error("Invalid response");
    // The local design walkthrough is separate from the real hosted lookup below.
    if (result.invited && result.next === "/welcome" && ["127.0.0.1", "localhost"].includes(location.hostname)) {
      location.assign("/welcome");
      return;
    }
    if (result.invited) {
      // The hosted lookup only confirms the name matched; it grants no session
      // and reveals no private details. The welcome page itself lives on this
      // same public site, not behind the lookup API. Relative, not "/welcome.html":
      // GitHub Pages serves this as a project page under a subpath, not the domain root.
      // sessionStorage carries the name across the page navigation so the welcome
      // page can look up the RSVP party without asking again; it re-verifies the
      // name itself rather than trusting this value.
      try {
        sessionStorage.setItem("guestFirstName", firstName);
        sessionStorage.setItem("guestLastName", lastName);
      } catch {
        // Private browsing or blocked storage: welcome.html falls back to asking again.
      }
      location.assign(new URL("welcome.html", document.baseURI));
      return;
    }
    statusText.textContent = "We couldn't find that name on our invitation list. Check the spelling on your invitation, or reach out to Emily or James.";
  } catch {
    statusText.textContent = "We couldn't check your invitation right now. Please try again later.";
  } finally {
    pending = false;
    submitButton.disabled = false;
    form.removeAttribute("aria-busy");
  }
});

configureLookup();
