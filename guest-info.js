"use strict";
const form = document.querySelector("#guest-info");
const lookupForm = document.querySelector("#contact-lookup");
const button = form.querySelector('[type="submit"]');
const lookupButton = lookupForm.querySelector('[type="submit"]');
const statusText = document.querySelector("#form-status");
const lookupStatus = document.querySelector("#lookup-status");
const confirmation = document.querySelector("#confirmation");
let configPromise, identity, members = [], selectedId, submissionId;
const attempts = new Map();
let pending = false;
const saved = new Set();

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

function renderMembers() {
  const list = document.querySelector("#party-members");
  list.replaceChildren();
  for (const member of members) {
    const label = document.createElement("label");
    label.className = "guest-option";
    const radio = document.createElement("input");
    radio.type = "radio";
    radio.name = "party-member";
    radio.value = member.guest_id;
    radio.checked = member.guest_id === selectedId;
    radio.disabled = saved.has(member.guest_id) || pending;
    radio.addEventListener("change", () => selectGuest(member.guest_id));
    const text = document.createElement("span");
    text.textContent = member.name + (saved.has(member.guest_id) ? " — details saved" : "");
    label.append(radio, text);
    list.append(label);
  }
}

// Keep each approved guest's in-progress details separate when switching guests.
const drafts = new Map();
function selectGuest(identifier) {
  if (pending) return;
  if (selectedId) drafts.set(selectedId, Object.fromEntries(new FormData(form)));
  selectedId = identifier;
  form.reset();
  for (const field of form.querySelectorAll("input")) {
    field.setCustomValidity("");
    field.removeAttribute("aria-invalid");
    if (drafts.has(identifier) && field.name in drafts.get(identifier)) field.value = drafts.get(identifier)[field.name];
  }
  form.elements.name_line_one.value = members.find(member => member.guest_id === identifier).name;
  status(statusText, "");
  renderMembers();
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
    if (!Array.isArray(result.members) || !result.members.length || !result.members.every(member => typeof member.guest_id === "string" && typeof member.name === "string") || !result.members.some(member => member.guest_id === result.matched_guest_id)) throw new Error("We couldn't confirm your invitation. Please try again.");
    identity = candidate;
    members = result.members;
    saved.clear();
    drafts.clear();
    attempts.clear();
    selectedId = null;
    pending = false;
    selectGuest(result.matched_guest_id);
    document.querySelector("#party-summary").textContent = members.length === 1
      ? "We found your invitation. It's for you only."
      : "We found your invitation. The guests listed below are included. Please share details for each person.";
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
  if (pending || !identity || !selectedId) return;
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
  const data = { ...Object.fromEntries(new FormData(form)), lookup: identity, guest_id: selectedId };
  const body = JSON.stringify(data);
  const previous = attempts.get(selectedId);
  submissionId = previous?.body === body ? previous.id : crypto.randomUUID();
  attempts.set(selectedId, { body, id: submissionId });
  pending = true;
  button.disabled = true;
  form.setAttribute("aria-busy", "true");
  for (const field of form.querySelectorAll("input")) field.readOnly = true;
  for (const control of document.querySelectorAll(".change-invitation, #party-members input")) control.disabled = true;
  status(statusText, "Sending your details…");
  try {
    const result = await post("guestInfoUrl", { ...data, submission_id: submissionId });
    if (result.saved !== true || result.submission_id !== submissionId) throw new Error("We couldn't confirm your details were saved. Please try again.");
    saved.add(selectedId);
    drafts.delete(selectedId);
    document.querySelector("#form-content").hidden = true;
    confirmation.hidden = false;
    document.querySelector("#receipt").textContent = `Your reference: ${submissionId}`;
    const remaining = members.filter(member => !saved.has(member.guest_id));
    document.querySelector("#remaining-guests").textContent = remaining.length ? "You can now share details for the other guest on your invitation." : "Everyone on this invitation is all set.";
    document.querySelector("#another-guest").hidden = !remaining.length;
    confirmation.focus();
    status(statusText, "");
    form.reset();
    selectedId = null;
  } catch (error) {
    if (error.code === 409) attempts.delete(selectedId);
    if (error.field && form.elements.namedItem(error.field)) {
      const field = form.elements.namedItem(error.field);
      field.setCustomValidity(error.message);
      field.setAttribute("aria-invalid", "true");
      if (field.name !== "name_line_one") field.readOnly = false;
      field.reportValidity();
    }
    status(statusText, errorMessage(error), true);
  } finally {
    pending = false;
    button.disabled = false;
    form.removeAttribute("aria-busy");
    for (const field of form.querySelectorAll("input")) field.readOnly = field.name === "name_line_one";
    for (const control of document.querySelectorAll(".change-invitation")) control.disabled = false;
    renderMembers();
  }
});

document.querySelector("#another-guest").addEventListener("click", () => {
  const next = members.find(member => !saved.has(member.guest_id));
  if (!next || pending) return;
  selectGuest(next.guest_id);
  confirmation.hidden = true;
  document.querySelector("#form-content").hidden = false;
  document.querySelector("#form-title").focus();
});

for (const control of document.querySelectorAll(".change-invitation")) control.addEventListener("click", () => {
  if (pending) return;
  identity = null;
  selectedId = null;
  members = [];
  saved.clear();
  drafts.clear();
  attempts.clear();
  form.reset();
  confirmation.hidden = true;
  document.querySelector("#form-content").hidden = true;
  document.querySelector("#lookup-content").hidden = false;
  lookupForm.elements.first_initial.focus();
});
