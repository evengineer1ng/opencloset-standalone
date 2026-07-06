import { useEffect, useId, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import "./CustomSelect.css";

export interface CustomSelectOption {
  value: string;
  label: string;
  disabled?: boolean;
}

interface CustomSelectProps {
  options: CustomSelectOption[];
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  placeholder?: string;
  triggerClassName?: string;
  menuClassName?: string;
  optionClassName?: string;
  ariaLabel?: string;
}

type MenuPosition = {
  top: number;
  left: number;
  width: number;
  maxHeight: number;
  placement: "top" | "bottom";
};

const MENU_MARGIN = 10;
const MENU_MAX_HEIGHT = 280;

function joinClassNames(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}

function firstEnabledIndex(options: CustomSelectOption[]): number {
  return options.findIndex((option) => !option.disabled);
}

export function CustomSelect({
  options,
  value,
  onChange,
  disabled = false,
  placeholder = "Select",
  triggerClassName,
  menuClassName,
  optionClassName,
  ariaLabel,
}: CustomSelectProps) {
  const buttonRef = useRef<HTMLButtonElement | null>(null);
  const menuRef = useRef<HTMLDivElement | null>(null);
  const optionRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const listboxId = useId();
  const [open, setOpen] = useState(false);
  const [highlightedIndex, setHighlightedIndex] = useState(-1);
  const [menuPosition, setMenuPosition] = useState<MenuPosition | null>(null);

  const selectedOption = useMemo(
    () => options.find((option) => option.value === value) ?? null,
    [options, value],
  );

  useEffect(() => {
    if (!open) {
      return;
    }

    function updateMenuPosition() {
      const button = buttonRef.current;
      if (!button) {
        return;
      }

      const rect = button.getBoundingClientRect();
      const spaceBelow = window.innerHeight - rect.bottom - MENU_MARGIN;
      const spaceAbove = rect.top - MENU_MARGIN;
      const placement = spaceBelow >= 180 || spaceBelow >= spaceAbove ? "bottom" : "top";
      const maxHeight = Math.max(140, Math.min(MENU_MAX_HEIGHT, placement === "bottom" ? spaceBelow : spaceAbove));
      const top = placement === "bottom"
        ? Math.min(window.innerHeight - MENU_MARGIN - maxHeight, rect.bottom + 8)
        : Math.max(MENU_MARGIN, rect.top - maxHeight - 8);

      setMenuPosition({
        top,
        left: Math.min(rect.left, window.innerWidth - rect.width - MENU_MARGIN),
        width: rect.width,
        maxHeight,
        placement,
      });
    }

    function handlePointerDown(event: MouseEvent) {
      const target = event.target as Node;
      if (buttonRef.current?.contains(target) || menuRef.current?.contains(target)) {
        return;
      }
      setOpen(false);
    }

    function handleFocusIn(event: FocusEvent) {
      const target = event.target as Node;
      if (buttonRef.current?.contains(target) || menuRef.current?.contains(target)) {
        return;
      }
      setOpen(false);
    }

    function handleEscape(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setOpen(false);
        buttonRef.current?.focus();
      }
    }

    updateMenuPosition();
    window.addEventListener("resize", updateMenuPosition);
    window.addEventListener("scroll", updateMenuPosition, true);
    document.addEventListener("mousedown", handlePointerDown);
    document.addEventListener("focusin", handleFocusIn);
    document.addEventListener("keydown", handleEscape);

    return () => {
      window.removeEventListener("resize", updateMenuPosition);
      window.removeEventListener("scroll", updateMenuPosition, true);
      document.removeEventListener("mousedown", handlePointerDown);
      document.removeEventListener("focusin", handleFocusIn);
      document.removeEventListener("keydown", handleEscape);
    };
  }, [open]);

  useEffect(() => {
    if (!open) {
      return;
    }

    const selectedIndex = options.findIndex((option) => option.value === value && !option.disabled);
    const nextIndex = selectedIndex >= 0 ? selectedIndex : firstEnabledIndex(options);
    setHighlightedIndex(nextIndex);
  }, [open, options, value]);

  useEffect(() => {
    if (!open || highlightedIndex < 0) {
      return;
    }

    optionRefs.current[highlightedIndex]?.scrollIntoView({ block: "nearest" });
  }, [highlightedIndex, open]);

  function commitSelection(option: CustomSelectOption) {
    if (option.disabled) {
      return;
    }
    onChange(option.value);
    setOpen(false);
    buttonRef.current?.focus();
  }

  function moveHighlight(step: 1 | -1) {
    if (!options.length) {
      return;
    }

    let nextIndex = highlightedIndex;
    for (let attempts = 0; attempts < options.length; attempts += 1) {
      nextIndex = (nextIndex + step + options.length) % options.length;
      if (!options[nextIndex]?.disabled) {
        setHighlightedIndex(nextIndex);
        return;
      }
    }
  }

  function openMenuAndPrime(direction?: 1 | -1) {
    if (disabled || !options.length) {
      return;
    }

    const selectedIndex = options.findIndex((option) => option.value === value && !option.disabled);
    const baseIndex = selectedIndex >= 0 ? selectedIndex : firstEnabledIndex(options);
    setHighlightedIndex(baseIndex);
    setOpen(true);

    if (direction && baseIndex >= 0) {
      setTimeout(() => moveHighlight(direction), 0);
    }
  }

  function handleTriggerKeyDown(event: React.KeyboardEvent<HTMLButtonElement>) {
    if (disabled) {
      return;
    }

    if (event.key === "ArrowDown") {
      event.preventDefault();
      if (open) {
        moveHighlight(1);
      } else {
        openMenuAndPrime(1);
      }
      return;
    }

    if (event.key === "ArrowUp") {
      event.preventDefault();
      if (open) {
        moveHighlight(-1);
      } else {
        openMenuAndPrime(-1);
      }
      return;
    }

    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      setOpen((current) => !current);
    }
  }

  function handleMenuKeyDown(event: React.KeyboardEvent<HTMLDivElement>) {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      moveHighlight(1);
      return;
    }

    if (event.key === "ArrowUp") {
      event.preventDefault();
      moveHighlight(-1);
      return;
    }

    if (event.key === "Home") {
      event.preventDefault();
      setHighlightedIndex(firstEnabledIndex(options));
      return;
    }

    if (event.key === "End") {
      event.preventDefault();
      const lastEnabled = [...options].reverse().findIndex((option) => !option.disabled);
      if (lastEnabled >= 0) {
        setHighlightedIndex(options.length - 1 - lastEnabled);
      }
      return;
    }

    if ((event.key === "Enter" || event.key === " ") && highlightedIndex >= 0) {
      event.preventDefault();
      const option = options[highlightedIndex];
      if (option) {
        commitSelection(option);
      }
    }
  }

  const menu = open && menuPosition
    ? createPortal(
        <div
          ref={menuRef}
          className={joinClassNames("custom-select__menu", menuClassName, menuPosition.placement === "top" && "is-top")}
          style={{
            position: "fixed",
            top: menuPosition.top,
            left: menuPosition.left,
            width: menuPosition.width,
            maxHeight: menuPosition.maxHeight,
          }}
          role="listbox"
          id={listboxId}
          tabIndex={-1}
          aria-activedescendant={highlightedIndex >= 0 ? `${listboxId}-${highlightedIndex}` : undefined}
          onKeyDown={handleMenuKeyDown}
        >
          {options.map((option, index) => {
            const selected = option.value === value;
            const highlighted = index === highlightedIndex;
            return (
              <button
                key={`${option.value}-${index}`}
                ref={(node) => {
                  optionRefs.current[index] = node;
                }}
                id={`${listboxId}-${index}`}
                type="button"
                role="option"
                aria-selected={selected}
                className={joinClassNames(
                  "custom-select__option",
                  optionClassName,
                  selected && "is-selected",
                  highlighted && "is-highlighted",
                  option.disabled && "is-disabled",
                )}
                onMouseEnter={() => !option.disabled && setHighlightedIndex(index)}
                onClick={() => commitSelection(option)}
                disabled={option.disabled}
              >
                <span className="custom-select__option-label">{option.label}</span>
                {selected && <span className="custom-select__option-check">✓</span>}
              </button>
            );
          })}
        </div>,
        document.body,
      )
    : null;

  return (
    <div className={joinClassNames("custom-select", disabled && "is-disabled")}>
      <button
        ref={buttonRef}
        type="button"
        className={joinClassNames("custom-select__trigger", triggerClassName, open && "is-open")}
        onClick={() => setOpen((current) => !current)}
        onKeyDown={handleTriggerKeyDown}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listboxId : undefined}
        aria-label={ariaLabel}
        disabled={disabled}
      >
        <span className="custom-select__trigger-value">{selectedOption?.label ?? placeholder}</span>
        <span className="custom-select__trigger-icon" aria-hidden="true">▾</span>
      </button>
      {menu}
    </div>
  );
}

export default CustomSelect;