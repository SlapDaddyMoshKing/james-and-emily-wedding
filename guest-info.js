"use strict";
const form = document.querySelector("#guest-info");
const lookupForm = document.querySelector("#contact-lookup");
const button = form.querySelector('[type="submit"]');
const lookupButton = lookupForm.querySelector('[type="submit"]');
const statusText = document.querySelector("#form-status");
const lookupStatus = document.querySelector("#lookup-status");
const confirmation = document.querySelector("#confirmation");
const plusOneSection = document.querySelector("#plus-one-section");
const plusOneFields = document.querySelector("#plus-one-fields");
const unknownCheckbox = form.elements.guest_name_unknown;
let configPromise, identity, guest, submissionId;
let lastAttempt = null;
let pending = false;

function status(target, message, error = false) {
  target.textContent = message;
  target.classList.toggle("error", error);
}

async function configure() {
  const response = await fetch(new URL("site-config.json", document.baseURI), { cache: "no-store", signal: AbortSignal.timeout(10000) });
  if (!response.ok) throw new Error("The form is temporarily unavailable. Please try again shortly.");
  const config = await response.json();
  for (const key of ["guestInfoUrl", "contactPartyUrl"]) {
    if (typeof config[key] !== "string" || !config[key].trim()) throw new Error("Please refresh the page to load the latest form.");
    const url = new URL(config[key], document.baseURI);
    if (url.protocol !== "https:" && !(url.protocol === "http:" && ["localhost", "127.0.0.1"].includes(url.hostname))) throw new Error("The form is temporarily unavailable.");
    config[key] = url.href;
  }
  return config;
}

async function post(key, data) {
  if (!configPromise) configPromise = configure().catch(error => { configPromise = null; throw error; });
  const config = await configPromise;
  const response = await fetch(config[key], {
    method: "POST", headers: { "Content-Type": "application/json" }, credentials: "omit", cache: "no-store",
    body: JSON.stringify(data), signal: AbortSignal.timeout(25000),
  });
  if (response.status === 429) throw new Error("Please wait ten minutes before trying again.");
  const result = await response.json();
  if (!response.ok) {
    const error = new Error(result.error || "We couldn't complete that request. Please try again.");
    error.field = result.field;
    error.code = response.status;
    throw error;
  }
  return result;
}

function errorMessage(error) {
  return ["TimeoutError", "TypeError", "SyntaxError"].includes(error.name)
    ? "We couldn't confirm the request completed. Check your connection and try again; your entries are still here."
    : error.message;
}

function updatePlusOneFields() {
  const unknown = plusOneSection.hidden || unknownCheckbox.checked;
  plusOneFields.hidden = unknown;
  for (const name of ["plus_one_first_name", "plus_one_last_name"]) {
    const field = form.elements[name];
    field.required = !unknown;
    if (unknown) field.value = "";
  }
}
unknownCheckbox.addEventListener("change", updatePlusOneFields);

function setFieldsDisabled(disabled) {
  for (const field of form.elements) field.disabled = disabled;
}

lookupForm.addEventListener("submit", async event => {
  event.preventDefault();
  if (pending) return;
  const first = lookupForm.elements.first_initial;
  first.value = first.value.trim().normalize("NFKC").replace(/\.$/, "");
  first.setCustomValidity(/^\p{L}$/u.test(first.value) ? "" : "Please enter just your first initial, such as J.");
  lookupForm.elements.last_name.value = lookupForm.elements.last_name.value.trim();
  if (!lookupForm.reportValidity()) return;
  const candidate = Object.fromEntries(new FormData(lookupForm));
  pending = true;
  lookupButton.disabled = true;
  lookupForm.setAttribute("aria-busy", "true");
  status(lookupStatus, "Finding your invitation…");
  try {
    const result = await post("contactPartyUrl", candidate);
    if (typeof result.first_name !== "string" || typeof result.last_name !== "string" || typeof result.plus_one_allowed !== "boolean") throw new Error("We couldn't confirm your invitation. Please try again.");
    identity = candidate;
    guest = result;
    lastAttempt = null;
    form.reset();
    for (const field of form.querySelectorAll("input, select")) {
      field.setCustomValidity("");
      field.removeAttribute("aria-invalid");
    }
    form.elements.first_name.value = guest.first_name;
    form.elements.last_name.value = guest.last_name;
    plusOneSection.hidden = !guest.plus_one_allowed;
    updatePlusOneFields();
    document.querySelector("#party-summary").textContent = guest.plus_one_allowed
      ? "We found your invitation. It includes a plus-one."
      : "We found your invitation. It's for you only.";
    document.querySelector("#lookup-content").hidden = true;
    document.querySelector("#form-content").hidden = false;
    document.querySelector("#form-title").focus();
    button.disabled = false;
    status(lookupStatus, "");
  } catch (error) {
    status(lookupStatus, errorMessage(error), true);
  } finally {
    pending = false;
    lookupButton.disabled = false;
    lookupForm.removeAttribute("aria-busy");
  }
});
lookupForm.elements.first_initial.addEventListener("input", event => event.target.setCustomValidity(""));

form.addEventListener("input", event => {
  event.target.setCustomValidity?.("");
  event.target.removeAttribute("aria-invalid");
});

form.addEventListener("submit", async event => {
  event.preventDefault();
  if (pending || !identity || !guest) return;
  for (const field of form.querySelectorAll("input")) {
    field.value = field.value.trim();
    field.setCustomValidity("");
    field.removeAttribute("aria-invalid");
  }
  if (!form.reportValidity()) {
    for (const field of form.querySelectorAll(":invalid")) field.setAttribute("aria-invalid", "true");
    status(statusText, "Please check the highlighted fields.", true);
    return;
  }
  const data = { ...Object.fromEntries(new FormData(form)), guest_name_unknown: unknownCheckbox.checked, lookup: identity };
  const body = JSON.stringify(data);
  submissionId = lastAttempt?.body === body ? lastAttempt.id : crypto.randomUUID();
  lastAttempt = { body, id: submissionId };
  pending = true;
  button.disabled = true;
  form.setAttribute("aria-busy", "true");
  setFieldsDisabled(true);
  for (const control of document.querySelectorAll(".change-invitation")) control.disabled = true;
  status(statusText, "Sending your details…");
  try {
    const result = await post("guestInfoUrl", { ...data, submission_id: submissionId });
    if (result.saved !== true || result.submission_id !== submissionId) throw new Error("We couldn't confirm your details were saved. Please try again.");
    document.querySelector("#form-content").hidden = true;
    confirmation.hidden = false;
    document.querySelector("#receipt").textContent = `Your reference: ${submissionId}`;
    confirmation.focus();
    status(statusText, "");
    form.reset();
    identity = null;
    guest = null;
    lastAttempt = null;
  } catch (error) {
    if (error.code === 409) lastAttempt = null;
    if (error.field && form.elements.namedItem(error.field)) {
      const field = form.elements.namedItem(error.field);
      field.disabled = false;
      field.setCustomValidity(error.message);
      field.setAttribute("aria-invalid", "true");
      field.reportValidity();
    }
    status(statusText, errorMessage(error), true);
  } finally {
    pending = false;
    button.disabled = false;
    form.removeAttribute("aria-busy");
    setFieldsDisabled(false);
    for (const control of document.querySelectorAll(".change-invitation")) control.disabled = false;
  }
});

for (const control of document.querySelectorAll(".change-invitation")) control.addEventListener("click", () => {
  if (pending) return;
  identity = null;
  guest = null;
  lastAttempt = null;
  form.reset();
  confirmation.hidden = true;
  document.querySelector("#form-content").hidden = true;
  document.querySelector("#lookup-content").hidden = false;
  lookupForm.elements.first_initial.focus();
});
