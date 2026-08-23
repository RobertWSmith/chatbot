export function initializeToolFilter(root) {
  if (!root) return null;

  const trigger = root.querySelector("#tool-filter-trigger");
  const popover = root.querySelector("#tool-filter-popover");
  const closeButton = root.querySelector(".tool-filter-close");
  const search = root.querySelector("[data-tool-search]");
  const selectAll = root.querySelector("[data-tool-select-all]");
  const clear = root.querySelector("[data-tool-clear]");
  const summary = root.querySelector("[data-tool-summary]");
  const count = root.querySelector("[data-tool-count]");
  const empty = root.querySelector("[data-tool-empty]");
  const options = Array.from(root.querySelectorAll("[data-tool-option]"));
  const checkboxes = options.map((option) => option.querySelector("input[type='checkbox']"));

  function selectedNamespaces() {
    return checkboxes.filter((checkbox) => checkbox.checked).map((checkbox) => checkbox.value);
  }

  function updateSummary() {
    const selectedCount = selectedNamespaces().length;
    if (!checkboxes.length) {
      summary.textContent = "None";
    } else if (selectedCount === checkboxes.length) {
      summary.textContent = "All";
    } else if (!selectedCount) {
      summary.textContent = "Off";
    } else {
      summary.textContent = `${selectedCount} selected`;
    }
    if (count) count.textContent = `${selectedCount} of ${checkboxes.length} selected`;
  }

  function closePopover({ restoreFocus = false } = {}) {
    popover.hidden = true;
    trigger.setAttribute("aria-expanded", "false");
    if (restoreFocus) trigger.focus();
  }

  function openPopover() {
    if (trigger.disabled) return;
    popover.hidden = false;
    trigger.setAttribute("aria-expanded", "true");
    if (search) search.focus();
  }

  trigger.addEventListener("click", () => {
    if (popover.hidden) openPopover();
    else closePopover();
  });
  closeButton.addEventListener("click", () => closePopover({ restoreFocus: true }));

  if (search) {
    search.addEventListener("input", () => {
      const query = search.value.trim().toLowerCase();
      let visibleCount = 0;
      for (const option of options) {
        const matches = !query || option.dataset.searchValue.includes(query);
        option.hidden = !matches;
        if (matches) visibleCount += 1;
      }
      empty.hidden = visibleCount !== 0;
    });
  }

  if (selectAll) {
    selectAll.addEventListener("click", () => {
      for (const checkbox of checkboxes) checkbox.checked = true;
      updateSummary();
    });
  }
  if (clear) {
    clear.addEventListener("click", () => {
      for (const checkbox of checkboxes) checkbox.checked = false;
      updateSummary();
    });
  }
  for (const checkbox of checkboxes) checkbox.addEventListener("change", updateSummary);

  document.addEventListener("click", (event) => {
    if (!popover.hidden && !root.contains(event.target)) closePopover();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !popover.hidden) {
      event.preventDefault();
      closePopover({ restoreFocus: true });
    }
  });

  updateSummary();
  return {
    value() {
      const selected = selectedNamespaces();
      return checkboxes.length > 0 && selected.length === checkboxes.length ? null : selected;
    },
    setDisabled(disabled) {
      if (disabled) closePopover();
      trigger.disabled = disabled || checkboxes.length === 0;
    },
  };
}
