/**
 * A dropdown that belongs to this design system.
 *
 * A native `<select>` draws itself with the operating system's widget. On
 * Windows that means a white popup in the system font with its own scrollbar,
 * which on a dark instrument panel looks like a hole punched through the page,
 * and no amount of CSS on the `<select>` element changes the list it opens.
 *
 * So this is a button plus a listbox. What it keeps from the native element is
 * the part worth keeping: full keyboard control and correct ARIA roles, so it is
 * still operable without a mouse and still announces itself properly to a screen
 * reader.
 *
 *   Enter / Space / Down   open
 *   Up / Down              move the highlight
 *   Home / End             jump to first or last
 *   Enter                  choose the highlighted option
 *   Escape                 close and return focus to the button
 *   Tab or outside click   close
 */

import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";

export type SelectOption = { value: string; label: string };

export default function Select({
  value,
  options,
  onChange,
  label,
  minWidth,
}: {
  value: string;
  options: SelectOption[];
  onChange: (value: string) => void;
  /** Accessible name. There is no visible <label>, so this is not optional. */
  label: string;
  minWidth?: number;
}) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const rootRef = useRef<HTMLDivElement>(null);
  const btnRef = useRef<HTMLButtonElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  const listId = useId();

  const selectedIndex = Math.max(0, options.findIndex((o) => o.value === value));
  const selected = options[selectedIndex];

  // Opening should highlight whatever is currently selected, not the top of the
  // list, so arrowing from an open menu starts where the user already is.
  useEffect(() => {
    if (open) setActive(selectedIndex);
  }, [open, selectedIndex]);

  // Close on any click that isn't inside this component.
  useEffect(() => {
    if (!open) return;
    function onDocPointerDown(e: PointerEvent) {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("pointerdown", onDocPointerDown);
    return () => document.removeEventListener("pointerdown", onDocPointerDown);
  }, [open]);

  // Keep the highlighted option in view when arrowing past the visible window.
  // Layout effect so it happens before paint and the list never visibly jumps.
  useLayoutEffect(() => {
    if (!open) return;
    const el = listRef.current?.children[active] as HTMLElement | undefined;
    el?.scrollIntoView({ block: "nearest" });
  }, [open, active]);

  function commit(index: number) {
    const opt = options[index];
    if (opt) onChange(opt.value);
    setOpen(false);
    btnRef.current?.focus();
  }

  function onKeyDown(e: React.KeyboardEvent) {
    if (!open) {
      if (e.key === "Enter" || e.key === " " || e.key === "ArrowDown") {
        e.preventDefault();
        setOpen(true);
      }
      return;
    }
    switch (e.key) {
      case "Escape":
        e.preventDefault();
        setOpen(false);
        btnRef.current?.focus();
        break;
      case "ArrowDown":
        e.preventDefault();
        setActive((i) => Math.min(options.length - 1, i + 1));
        break;
      case "ArrowUp":
        e.preventDefault();
        setActive((i) => Math.max(0, i - 1));
        break;
      case "Home":
        e.preventDefault();
        setActive(0);
        break;
      case "End":
        e.preventDefault();
        setActive(options.length - 1);
        break;
      case "Enter":
      case " ":
        e.preventDefault();
        commit(active);
        break;
      case "Tab":
        setOpen(false);
        break;
    }
  }

  return (
    <div
      className="ps-select"
      data-open={open}
      ref={rootRef}
      style={minWidth ? { minWidth } : undefined}
      onKeyDown={onKeyDown}
    >
      <button
        type="button"
        ref={btnRef}
        className="ps-select-btn"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        aria-label={label}
        onClick={() => setOpen((v) => !v)}
      >
        <span>{selected?.label ?? ""}</span>
        <span className="ps-select-caret" aria-hidden="true" />
      </button>

      {open && (
        <ul className="ps-select-menu" role="listbox" id={listId} ref={listRef}
            aria-label={label}>
          {options.map((o, i) => (
            <li
              key={o.value}
              role="option"
              aria-selected={o.value === value}
              data-active={i === active}
              className="ps-select-opt"
              onMouseEnter={() => setActive(i)}
              // pointerdown rather than click: the document listener above closes
              // the menu on pointerdown, which would otherwise unmount this before
              // the click ever lands.
              onPointerDown={(e) => {
                e.preventDefault();
                commit(i);
              }}
            >
              {o.label}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
