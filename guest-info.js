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
const smsConsentCheckbox = form.elements.sms_consent;
const choices = document.querySelector("#invitation-choices");
const choicesList = document.querySelector("#choices-list");
const choicesTitle = document.querySelector("#choices-title");
const choicesHelp = document.querySelector("#choices-help");
const lookupContent = document.querySelector("#lookup-content");
const formContent = document.querySelector("#form-content");
const formTitle = document.querySelector("#form-title");
const partySummary = document.querySelector("#party-summary");
const receipt = document.querySelector("#receipt");
const changeInvitationControls = document.querySelectorAll(".change-invitation");
const personalLinkHelp = "Please open the personal link in your text to share your details. If you need a new link, contact Emily or James.";
const incompleteLinkHelp = "This personal link is incomplete. Please reopen the original text or ask Emily or James for a new link.";
let configPromise, identity;
let lastAttempt = null;
let pending = false;
let linkToken = null;
let invalidLink = false;

// The secret stays in this page's memory. It is never saved in browser storage
// or left in the URL when a guest follows a link from their text.
const fragment = new URLSearchParams(window.location.hash.slice(1));
if (fragment.has("invite")) {
  const tokens = fragment.getAll("invite");
  invalidLink = tokens.length !== 1 || !/^[A-Za-z0-9_-]{43}$/.test(tokens[0]);
  if (!invalidLink) linkToken = tokens[0];
  history.replaceState(history.state, "", window.location.pathname + window.location.search);
}
// Reopening a text while this page is already open can be a fragment-only
// navigation. Start a fresh page so it cannot reuse a previous invitation.
window.addEventListener("hashchange", () => {
  if (new URLSearchParams(window.location.hash.slice(1)).has("invite")) window.location.reload();
});

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

function clearChoices() {
  choices.hidden = true;
  choicesList.replaceChildren();
}

function showChoices(result, candidate) {
  if (typeof result.requires_personal_link !== "boolean" || result.candidates.length === 0
      || result.candidates.some(item => typeof item.invitation_id !== "string" || !item.invitation_id
        || typeof item.first_name !== "string" || typeof item.last_name !== "string")) {
    throw new Error("We couldn't load your details. Please try again.");
  }
  clearChoices();
  choicesHelp.textContent = result.requires_personal_link
    ? "To share your details, open the link in your text."
    : "Select your name below to continue.";
  for (const match of result.candidates) {
    const item = document.createElement("li");
    const choiceButton = document.createElement("button");
    choiceButton.type = "button";
    choiceButton.className = "choice-button";
    choiceButton.textContent = `${match.first_name} ${match.last_name}`;
    choiceButton.addEventListener("click", () => {
      if (pending) return;
      if (result.requires_personal_link || !linkToken) {
        status(lookupStatus, personalLinkHelp, true);
        lookupStatus.focus();
        return;
      }
      findInvitation({ ...candidate, invitation_id: match.invitation_id });
    });
    item.append(choiceButton);
    choicesList.append(item);
  }
  choices.hidden = false;
  choicesTitle.focus();
}

function unlockInvitation(result, candidate) {
  if (!linkToken || typeof result.invitation_id !== "string" || !result.invitation_id
      || typeof result.first_name !== "string" || typeof result.last_name !== "string"
      || typeof result.plus_one_allowed !== "boolean" || typeof result.prefill !== "object" || result.prefill === null) {
    throw new Error("We couldn't load your details. Please try again.");
  }
  identity = { ...candidate, invitation_id: result.invitation_id, link_token: linkToken };
  const guest = result;
  lastAttempt = null;
  form.reset();
  for (const field of form.querySelectorAll("input, select")) {
    field.setCustomValidity("");
    field.removeAttribute("aria-invalid");
  }
  form.elements.first_name.value = guest.first_name;
  form.elements.last_name.value = guest.last_name;
  // Already-known details fill the form in but stay editable, so a guest
  // only has to confirm or correct them rather than retype everything.
  const prefill = guest.prefill;
  for (const field of ["address_line1", "address_line2", "city", "region", "postal_code", "phone"]) {
    if (typeof prefill[field] === "string" && prefill[field]) form.elements[field].value = prefill[field];
  }
  plusOneSection.hidden = !guest.plus_one_allowed;
  if (guest.plus_one_allowed) {
    unknownCheckbox.checked = prefill.guest_name_unknown === true;
    if (typeof prefill.plus_one_first_name === "string" && prefill.plus_one_first_name) form.elements.plus_one_first_name.value = prefill.plus_one_first_name;
    if (typeof prefill.plus_one_last_name === "string" && prefill.plus_one_last_name) form.elements.plus_one_last_name.value = prefill.plus_one_last_name;
  }
  updatePlusOneFields();
  partySummary.textContent = guest.plus_one_allowed
    ? "You can add your plus-one's name below."
    : "Please share your contact information below.";
  clearChoices();
  lookupContent.hidden = true;
  formContent.hidden = false;
  formTitle.focus();
  button.disabled = false;
}

async function findInvitation(candidate) {
  if (pending) return;
  if (invalidLink) {
    status(lookupStatus, incompleteLinkHelp, true);
    return;
  }
  pending = true;
  lookupButton.disabled = true;
  for (const control of choicesList.querySelectorAll("button")) control.disabled = true;
  lookupForm.setAttribute("aria-busy", "true");
  choices.setAttribute("aria-busy", "true");
  status(lookupStatus, "Finding your name…");
  try {
    const scopedCandidate = linkToken ? { ...candidate, link_token: linkToken } : candidate;
    const result = await post("contactPartyUrl", scopedCandidate);
    if (Array.isArray(result.candidates)) showChoices(result, scopedCandidate);
    else unlockInvitation(result, scopedCandidate);
    status(lookupStatus, "");
  } catch (error) {
    status(lookupStatus, errorMessage(error), true);
  } finally {
    pending = false;
    lookupButton.disabled = false;
    for (const control of choicesList.querySelectorAll("button")) control.disabled = false;
    lookupForm.removeAttribute("aria-busy");
    choices.removeAttribute("aria-busy");
  }
}

lookupForm.addEventListener("submit", event => {
  event.preventDefault();
  if (pending) return;
  const first = lookupForm.elements.first_initial;
  first.value = first.value.trim().normalize("NFKC").replace(/\.$/, "");
  first.setCustomValidity(/^\p{L}$/u.test(first.value) ? "" : "Please enter just your first initial, such as J.");
  lookupForm.elements.last_name.value = lookupForm.elements.last_name.value.trim();
  if (!lookupForm.reportValidity()) return;
  clearChoices();
  findInvitation(Object.fromEntries(new FormData(lookupForm)));
});
lookupForm.elements.first_initial.addEventListener("input", event => event.target.setCustomValidity(""));
lookupForm.addEventListener("input", () => {
  if (pending) return;
  clearChoices();
  status(lookupStatus, "");
});
if (invalidLink) status(lookupStatus, incompleteLinkHelp, true);

form.addEventListener("input", event => {
  event.target.setCustomValidity?.("");
  event.target.removeAttribute("aria-invalid");
});

form.addEventListener("submit", async event => {
  event.preventDefault();
  if (pending || !identity) return;
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
  const data = { ...Object.fromEntries(new FormData(form)), guest_name_unknown: unknownCheckbox.checked,
    sms_consent: smsConsentCheckbox.checked, lookup: identity };
  const body = JSON.stringify(data);
  const submissionId = lastAttempt?.body === body ? lastAttempt.id : crypto.randomUUID();
  lastAttempt = { body, id: submissionId };
  pending = true;
  button.disabled = true;
  form.setAttribute("aria-busy", "true");
  setFieldsDisabled(true);
  for (const control of changeInvitationControls) control.disabled = true;
  status(statusText, "Sending your details…");
  try {
    const result = await post("guestInfoUrl", { ...data, submission_id: submissionId });
    if (result.saved !== true || result.submission_id !== submissionId) throw new Error("We couldn't confirm your details were saved. Please try again.");
    formContent.hidden = true;
    confirmation.hidden = false;
    receipt.textContent = `Your reference: ${submissionId}`;
    confirmation.focus();
    status(statusText, "");
    form.reset();
    identity = null;
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
    for (const control of changeInvitationControls) control.disabled = false;
  }
});

for (const control of changeInvitationControls) control.addEventListener("click", () => {
  if (pending) return;
  identity = null;
  lastAttempt = null;
  clearChoices();
  status(lookupStatus, "");
  form.reset();
  confirmation.hidden = true;
  formContent.hidden = true;
  lookupContent.hidden = false;
  lookupForm.elements.first_initial.focus();
});
