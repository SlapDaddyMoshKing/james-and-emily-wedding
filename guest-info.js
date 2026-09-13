"use strict";
const form = document.querySelector("#guest-info");
const button = form.querySelector('[type="submit"]');
const statusText = document.querySelector("#form-status");
const confirmation = document.querySelector("#confirmation");
let endpoint, lastBody, submissionId;
let pending = false;

function status(message, error = false) {
  statusText.textContent = message;
  statusText.classList.toggle("error", error);
}

async function configure() {
  const response = await fetch(new URL("site-config.json", document.baseURI), {
    cache: "no-store", signal: AbortSignal.timeout(10000),
  });
  if (!response.ok) throw new Error("Configuration unavailable");
  const config = await response.json();
  if (typeof config.guestInfoUrl !== "string" || !config.guestInfoUrl.trim()) throw new Error("Configuration unavailable");
  const url = new URL(config.guestInfoUrl, document.baseURI);
  if (url.protocol !== "https:" && !(url.protocol === "http:" && ["localhost", "127.0.0.1"].includes(url.hostname))) throw new Error("Configuration unavailable");
  endpoint = url.href;
}

form.addEventListener("input", (event) => {
  event.target.setCustomValidity?.("");
  event.target.removeAttribute("aria-invalid");
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (pending) return;
  for (const field of form.querySelectorAll("input, textarea")) {
    field.value = field.value.trim();
    field.setCustomValidity("");
    field.removeAttribute("aria-invalid");
  }
  const initial = form.elements.first_initial;
  initial.value = initial.value.normalize("NFC").replace(/\.$/, "");
  if (!/^\p{L}\p{M}*$/u.test(initial.value)) initial.setCustomValidity("Please enter just your first initial, such as E.");
  const us = ["united states", "united states of america", "us", "usa", "u.s.", "u.s.a."].includes(form.elements.country.value.toLowerCase());
  if (us && !form.elements.region.value) form.elements.region.setCustomValidity("Please enter your state.");
  if (us && !/^\d{5}(-\d{4})?$/.test(form.elements.postal_code.value)) form.elements.postal_code.setCustomValidity("Please enter a five-digit ZIP code or ZIP+4.");
  if (!form.reportValidity()) {
    for (const field of form.querySelectorAll(":invalid")) field.setAttribute("aria-invalid", "true");
    status("Please check the highlighted fields.", true);
    return;
  }
  const data = Object.fromEntries(new FormData(form));
  // A retry uses the same random reference; names and email never become keys.
  const body = JSON.stringify(data);
  if (body !== lastBody) { submissionId = crypto.randomUUID(); lastBody = body; }
  pending = true;
  button.disabled = true;
  form.setAttribute("aria-busy", "true");
  for (const field of form.querySelectorAll("input, textarea")) field.readOnly = true;
  status("Sending your details…");
  try {
    if (!endpoint) await configure();
    const response = await fetch(endpoint, {
      method: "POST", headers: { "Content-Type": "application/json" },
      credentials: "omit", cache: "no-store",
      body: JSON.stringify({ ...data, submission_id: submissionId }),
      signal: AbortSignal.timeout(15000),
    });
    if (response.status === 429) throw new Error("Please wait ten minutes before trying again. Your entries are still here.");
    if (response.status === 409) {
      lastBody = null;
      throw new Error("Please send your details again to create a new submission.");
    }
    const result = await response.json();
    if (response.status === 400 && result.field && form.elements.namedItem(result.field)) {
      const field = form.elements.namedItem(result.field);
      field.setCustomValidity(result.error);
      field.setAttribute("aria-invalid", "true");
      field.readOnly = false;
      field.reportValidity();
      throw new Error(result.error);
    }
    if (!response.ok || result.saved !== true || result.submission_id !== submissionId) throw new Error("We couldn't confirm your details were saved. Please try again; your entries are still here.");
    document.querySelector("#form-content").hidden = true;
    confirmation.hidden = false;
    document.querySelector("#receipt").textContent = `Your reference: ${submissionId}`;
    confirmation.focus();
    status("");
    form.reset();
  } catch (error) {
    status(error.name === "TimeoutError" || error.name === "TypeError" || error.name === "SyntaxError"
      ? "We couldn't confirm your details were saved. Check your connection and try again; your entries are still here."
      : error.message === "Configuration unavailable"
        ? "The form is temporarily unavailable. Please try again shortly, or contact Emily or James."
        : error.message, true);
  } finally {
    pending = false;
    button.disabled = false;
    form.removeAttribute("aria-busy");
    for (const field of form.querySelectorAll("input, textarea")) field.readOnly = false;
  }
});

document.querySelector("#another-household").addEventListener("click", () => {
  lastBody = null;
  confirmation.hidden = true;
  document.querySelector("#form-content").hidden = false;
  form.elements.first_initial.focus();
});
configure().catch(() => {}).finally(() => { button.disabled = false; });
