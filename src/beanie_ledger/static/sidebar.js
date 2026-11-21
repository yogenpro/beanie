(() => {
  const toggle = document.querySelector(".sidebar-toggle");
  const root = document.documentElement;
  const preference = "beanie.sidebar.folded";
  let folded = false;

  function update() {
    if (folded) {
      root.dataset.bsSidebar = "folded";
    } else {
      delete root.dataset.bsSidebar;
    }
    const label = folded ? "Expand sidebar" : "Fold sidebar";
    toggle.setAttribute("aria-expanded", String(!folded));
    toggle.setAttribute("aria-label", label);
    toggle.title = label;
  }

  try {
    folded = localStorage.getItem(preference) === "true";
  } catch {
    // The toggle also works when browser storage is unavailable.
  }
  update();
  toggle.hidden = false;

  toggle.addEventListener("click", () => {
    folded = !folded;
    update();
    try {
      localStorage.setItem(preference, String(folded));
    } catch {
      // Keep the current page's state.
    }
  });
})();
