import {useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode} from 'react';
import {createPortal} from 'react-dom';

interface DropdownProps {
  /** Content rendered inside the trigger button (e.g. an icon). Must not itself be a <button>. */
  trigger: ReactNode;
  /** Menu content. Receives a `close` callback to dismiss the menu after an action. */
  children: (close: () => void) => ReactNode;
  /** Menu width in px (defaults to 192 = Tailwind w-48). */
  width?: number;
  /** Classes applied to the trigger <button> itself. */
  triggerClassName?: string;
  /** Extra classes for the floating menu. */
  menuClassName?: string;
  /** Accessible label for the trigger. */
  ariaLabel?: string;
}

/**
 * A dropdown menu that renders its panel in a portal on document.body with
 * `position: fixed`. This lets the menu escape ancestor clipping contexts
 * (overflow-hidden cards, overflow-x-auto scroll containers) that would
 * otherwise cut off its content. Position is computed from the trigger's
 * bounding rect and clamped to the viewport, flipping upward when there is
 * not enough room below.
 */
export function Dropdown({
  trigger,
  children,
  width = 192,
  triggerClassName,
  menuClassName,
  ariaLabel,
}: DropdownProps) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{top: number; left: number}>({top: 0, left: 0});
  const triggerRef = useRef<HTMLDivElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  const close = useCallback(() => setOpen(false), []);

  const computePosition = useCallback(() => {
    const el = triggerRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const gap = 8;
    // RTL-friendly: align the menu's right edge to the trigger's right edge,
    // then clamp inside the viewport so it never spills off-screen.
    let left = rect.right - width;
    left = Math.max(gap, Math.min(left, window.innerWidth - gap - width));
    // Prefer opening downward; flip above the trigger if it would overflow.
    const menuHeight = menuRef.current?.offsetHeight ?? 0;
    let top = rect.bottom + gap;
    if (menuHeight && top + menuHeight > window.innerHeight - gap) {
      const above = rect.top - gap - menuHeight;
      top = above > gap ? above : Math.max(gap, window.innerHeight - gap - menuHeight);
    }
    setPos({top, left});
  }, [width]);

  // Measure & position once the menu DOM exists (same commit as open=true).
  useLayoutEffect(() => {
    if (open) computePosition();
  }, [open, computePosition]);

  // Close on scroll (menu is fixed and would detach from the trigger),
  // reposition on resize, close on Escape.
  useEffect(() => {
    if (!open) return;
    const onScroll = () => close();
    const onResize = () => computePosition();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') close();
    };
    window.addEventListener('scroll', onScroll, true);
    window.addEventListener('resize', onResize);
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('scroll', onScroll, true);
      window.removeEventListener('resize', onResize);
      window.removeEventListener('keydown', onKey);
    };
  }, [open, close, computePosition]);

  // Close when clicking outside both the trigger and the menu.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      const target = e.target as Node;
      if (triggerRef.current?.contains(target) || menuRef.current?.contains(target)) return;
      close();
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [open, close]);

  return (
    <div ref={triggerRef} className="relative inline-block">
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={ariaLabel}
        onClick={() => setOpen((o) => !o)}
        className={triggerClassName}
      >
        {trigger}
      </button>
      {open &&
        createPortal(
          <div
            ref={menuRef}
            dir="rtl"
            role="menu"
            style={{position: 'fixed', top: pos.top, left: pos.left, width}}
            className={`rounded-xl glass shadow-2xl overflow-hidden z-[100] view-in ${menuClassName ?? ''}`}
          >
            {children(close)}
          </div>,
          document.body,
        )}
    </div>
  );
}
