"use strict";

const nameForm = document.querySelector("#rsvp-name-form");
const statusText = document.querySelector("#rsvp-status");
const rsvpForm = document.querySelector("#rsvp-form");
const membersList = document.querySelector("#rsvp-members");
let partyUrl = null;
let rsvpUrl = null;
let members = [];
let pending = false;

async function loadConfig() {
  try {
    const response = await fetch(new URL("site-config.json", document.baseURI), {
      cache: "no-store",
      signal: AbortSignal.timeout(10000),
    });
    if (!response.ok) throw new Error("Configuration unavailable");
    const config = await response.json();
    const urls = {};
    for (const key of ["partyUrl", "rsvpUrl"]) {
      if (typeof config[key] !== "string" || !config[key].trim()) throw new Error("RSVP not connected");
      const endpoint = new URL(config[key], document.baseURI);
      const local = ["localhost", "127.0.0.1"].includes(endpoint.hostname);
      if (endpoint.protocol !== "https:" && !(local && endpoint.protocol === "http:")) throw new Error("Unsupported address");
      urls[key] = endpoint.href;
    }
    partyUrl = urls.partyUrl;
    rsvpUrl = urls.rsvpUrl;
    return true;
  } catch {
    return false;
  }
}

function renderMembers() {
  membersList.innerHTML = "";
  for (const member of members) {
    const item = document.createElement("li");
    item.className = "rsvp-member";

    const name = document.createElement("span");
    name.className = "rsvp-member-name";
    name.textContent = `${member.first_name} ${member.last_name}`;

    const choice = document.createElement("span");
    choice.className = "rsvp-choice";
    const yes = document.createElement("button");
    yes.type = "button";
    yes.textContent = "Joyfully attending";
    yes.setAttribute("aria-pressed", String(member.attending === true));
    yes.addEventListener("click", () => { member.attending = true; renderMembers(); });
    const no = document.createElement("button");
    no.type = "button";
    no.textContent = "Can't make it";
    no.setAttribute("aria-pressed", String(member.attending === false));
    no.addEventListener("click", () => { member.attending = false; renderMembers(); });
    choice.append(yes, no);

    item.append(name, choice);
    membersList.append(item);
  }
}

async function loadParty(firstName, lastName) {
  statusText.textContent = "Loading your invitation…";
  rsvpForm.hidden = true;
  try {
    const response = await fetch(partyUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      cache: "no-store",
      body: JSON.stringify({ first_name: firstName, last_name: lastName }),
      signal: AbortSignal.timeout(10000),
    });
    if (response.status === 403) {
      statusText.textContent = "We couldn't confirm that invitation. Please check the spelling and try again.";
      nameForm.hidden = false;
      return;
    }
    if (response.status === 429) {
      statusText.textContent = "You've tried a few times. Please wait a bit before trying again.";
      return;
    }
    if (!response.ok) throw new Error("Party lookup failed");
    const result = await response.json();
    if (!Array.isArray(result.members) || result.members.length === 0) throw new Error("Invalid response");
    members = result.members;
    // Remember this name so a page refresh doesn't lose your place.
    sessionStorage.setItem("guestFirstName", firstName);
    sessionStorage.setItem("guestLastName", lastName);
    statusText.textContent = "";
    nameForm.hidden = true;
    rsvpForm.hidden = false;
    renderMembers();
  } catch {
    statusText.textContent = "We couldn't load your invitation right now. Please try again shortly.";
  }
}

nameForm?.addEventListener("submit", (event) => {
  event.preventDefault();
  if (!nameForm.reportValidity()) return;
  const firstName = nameForm.elements.first_name.value.trim();
  const lastName = nameForm.elements.last_name.value.trim();
  if (!firstName || !lastName) return;
  loadParty(firstName, lastName);
});

rsvpForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (pending) return;
  const responses = members
    .filter((member) => typeof member.attending === "boolean")
    .map((member) => ({ guest_id: member.guest_id, attending: member.attending }));
  if (responses.length === 0) {
    statusText.textContent = "Choose “Joyfully attending” or “Can't make it” for at least one guest.";
    return;
  }
  pending = true;
  const submitButton = rsvpForm.querySelector('button[type="submit"]');
  submitButton.disabled = true;
  statusText.textContent = "Saving your RSVP…";
  try {
    const firstName = sessionStorage.getItem("guestFirstName");
    const lastName = sessionStorage.getItem("guestLastName");
    const response = await fetch(rsvpUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      cache: "no-store",
      body: JSON.stringify({ first_name: firstName, last_name: lastName, responses }),
      signal: AbortSignal.timeout(10000),
    });
    if (response.status === 429) {
      statusText.textContent = "You've tried a few times. Please wait a bit before trying again.";
      return;
    }
    if (!response.ok) throw new Error("RSVP failed");
    const result = await response.json();
    members = result.members;
    renderMembers();
    statusText.textContent = "Thank you! Your RSVP has been saved.";
  } catch {
    statusText.textContent = "We couldn't save your RSVP right now. Please try again shortly.";
  } finally {
    pending = false;
    submitButton.disabled = false;
  }
});

async function init() {
  if (!(await loadConfig())) {
    statusText.textContent = "RSVP isn't connected yet. Please check back soon.";
    return;
  }
  const firstName = sessionStorage.getItem("guestFirstName");
  const lastName = sessionStorage.getItem("guestLastName");
  if (firstName && lastName) {
    loadParty(firstName, lastName);
  } else {
    // Arrived directly (bookmark, shared link, or a fresh tab) without going
    // through the RSVP check first -- ask again instead of a dead end.
    statusText.textContent = "";
    nameForm.hidden = false;
  }
}

init();
