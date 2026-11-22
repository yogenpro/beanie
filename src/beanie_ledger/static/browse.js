for (const navigation of document.querySelectorAll("[data-navigation]")) {
  navigation.hidden = false;
  const select = navigation.querySelector("select");
  select.addEventListener("change", () => location.assign(select.value));
}

for (const input of document.querySelectorAll("[data-account-search]")) {
  const groups = document.querySelectorAll("[data-account-group]");
  input.closest("label").hidden = false;
  input.addEventListener("input", () => {
    const value = input.value.trim().toLocaleLowerCase();
    let count = 0;
    for (const group of groups) {
      let visible = 0;
      for (const row of group.querySelectorAll("[data-account]")) {
        row.hidden = !row.dataset.account.toLocaleLowerCase().includes(value);
        if (!row.hidden) visible++;
      }
      group.hidden = visible === 0;
      count += visible;
    }
    document.querySelector("[data-no-accounts]").hidden = count > 0;
  });
}

const journalPages = new Map();
document.addEventListener("toggle", async event => {
  const entry = event.target;
  if (!entry.open || !entry.dataset.journalEntry || entry.dataset.postingsLoaded) return;
  const url = new URL(entry.dataset.journalEntry, location.href);
  const id = url.hash.slice(1);
  url.hash = "";
  const target = entry.querySelector("[data-posting-detail]");
  if (target.hasAttribute("aria-busy")) return;
  target.setAttribute("aria-busy", "true");
  try {
    if (!journalPages.has(url.href)) {
      const request = fetch(url).then(response => {
        if (!response.ok) throw new Error("Journal page unavailable");
        return response.text();
      }).then(text => new DOMParser().parseFromString(text, "text/html"));
      journalPages.set(url.href, request);
    }
    const page = await journalPages.get(url.href);
    const table = page.getElementById(id)?.querySelector(".posting-table");
    if (!table) return;
    const wrapper = table.parentElement.cloneNode(true);
    for (const anchor of wrapper.querySelectorAll("a[href]")) anchor.href = new URL(anchor.getAttribute("href"), url);
    target.replaceWith(wrapper);
    entry.dataset.postingsLoaded = "true";
  } catch {
    journalPages.delete(url.href);
  } finally {
    target.removeAttribute("aria-busy");
  }
}, true);

for (const expand of document.querySelectorAll("[data-expand-postings]")) {
  expand.hidden = false;
  const checkbox = expand.querySelector("input");
  checkbox.addEventListener("change", () => {
    for (const entry of document.querySelectorAll(".journal-entry")) entry.open = checkbox.checked;
  });
}

function revealJournalEntry() {
  const entry = document.getElementById(location.hash.slice(1));
  if (entry?.classList.contains("journal-entry")) entry.open = true;
}
addEventListener("hashchange", revealJournalEntry);
revealJournalEntry();

for (const search of document.querySelectorAll("[data-journal-search]")) {
  const form = search.querySelector("form");
  const status = search.querySelector("[data-search-status]");
  const results = search.querySelector("[data-search-results]");
  const button = form.querySelector("button");
  const index = new Map();
  search.hidden = false;
  async function readYear(source) {
    if (!index.has(source)) {
      const response = await fetch(source);
      if (!response.ok) throw new Error("Journal index unavailable");
      const rows = await response.json();
      index.set(source, rows.map(([date, title, terms, path]) => ({
        date, title, terms, href: new URL(path, response.url),
      })));
    }
    return index.get(source);
  }
  form.addEventListener("submit", async event => {
    event.preventDefault();
    const fields = new FormData(form);
    const year = fields.get("year");
    const sources = year ? [year] : [...form.elements.year.options].map(option => option.value).filter(Boolean);
    const terms = fields.get("text").trim().toLowerCase().split(/\s+/).filter(Boolean);
    button.disabled = true;
    status.textContent = "Searching…";
    results.replaceChildren();
    try {
      const matches = (await Promise.all(sources.map(readYear))).flat()
        .filter(entry => terms.every(term => entry.terms.includes(term)));
      status.textContent = matches.length > 50
        ? `Showing the newest 50 of ${matches.length.toLocaleString()} matches.`
        : `${matches.length.toLocaleString()} matching transactions.`;
      for (const entry of matches.slice(0, 50)) {
        const row = document.createElement("li");
        row.className = "list-group-item px-0 d-flex gap-2";
        const time = document.createElement("time");
        time.className = "text-secondary text-nowrap";
        time.dateTime = time.textContent = entry.date;
        const link = document.createElement("a");
        link.href = entry.href;
        link.textContent = entry.title;
        row.append(time, link);
        results.append(row);
      }
    } catch {
      status.textContent = "Unable to search the journal.";
    } finally {
      button.disabled = false;
    }
  });
}
