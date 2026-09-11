"use strict";

const openButton = document.querySelector("#open-lookup");
const panel = document.querySelector("#lookup-panel");
const form = document.querySelector("#guest-lookup");
const statusText = document.querySelector("#lookup-status");
const submitButton = form.querySelector('button[type="submit"]');
let lookupUrl = null;
let pending = false;
let configuring = true;

openButton.hidden = false;
openButton.addEventListener("click", () => {
  panel.hidden = !panel.hidden;
  openButton.setAttribute("aria-expanded", String(!panel.hidden));
  if (!panel.hidden) form.elements.first_name.focus();
});

async function configureLookup() {
  statusText.textContent = "Getting your invitation check ready…";
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
    statusText.textContent = "Invitation lookup is coming soon. Please check back a little later.";
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
      : "Invitation lookup is coming soon. Your name hasn't been checked or saved. Please check back a little later.";
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
      credentials: "omit",
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
    statusText.textContent = result.invited
      ? "You're on our invitation list! We can't wait to celebrate with you. Email sign-in will be available soon."
      : "We couldn't find that name on our invitation list. Check the spelling on your invitation, or reach out to Emily or James.";
  } catch {
    statusText.textContent = "We couldn't check your invitation right now. Please try again later.";
  } finally {
    pending = false;
    submitButton.disabled = false;
    form.removeAttribute("aria-busy");
  }
});

configureLookup();
