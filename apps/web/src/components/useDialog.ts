import { useEffect, useRef, type RefObject } from 'react';

const FOCUSABLE =
  'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

/** Open dialogs, innermost last; only the top one reacts to keys. */
const stack: HTMLElement[] = [];

const focusableIn = (node: HTMLElement) =>
  Array.from(node.querySelectorAll<HTMLElement>(FOCUSABLE)).filter((el) => el.offsetParent !== null || el === document.activeElement);

/**
 * Focus management for dialogs: moves focus in on open, keeps Tab inside, closes on Escape,
 * and gives focus back to whatever opened it.
 *
 * Keys are handled on the document, not the dialog node: when the focused element is removed
 * (for example a link that swaps the drawer's content) focus falls back to <body>, and Escape
 * must still work.
 */
export function useDialog(
  ref: RefObject<HTMLElement>,
  onClose: () => void,
  initialFocus?: RefObject<HTMLElement>,
  /** CSS selector for where focus should go on close if the opener no longer exists (e.g. a card that moved column). */
  restoreSelector?: () => string | null,
) {
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  const restoreRef = useRef(restoreSelector);
  restoreRef.current = restoreSelector;

  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    const opener = document.activeElement as HTMLElement | null;
    stack.push(node);
    (initialFocus?.current ?? focusableIn(node)[0] ?? node).focus();

    const onKeyDown = (event: KeyboardEvent) => {
      if (stack[stack.length - 1] !== node) return;
      if (event.key === 'Escape') {
        event.preventDefault();
        onCloseRef.current();
        return;
      }
      if (event.key !== 'Tab') return;
      const items = focusableIn(node);
      const first = items[0];
      const last = items[items.length - 1];
      if (!first || !last) {
        event.preventDefault();
        return;
      }
      const active = document.activeElement;
      if (!node.contains(active)) {
        event.preventDefault();
        (event.shiftKey ? last : first).focus();
      } else if (event.shiftKey && active === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('keydown', onKeyDown);
      stack.splice(stack.indexOf(node), 1);
      const selector = restoreRef.current?.();
      const target = selector ? document.querySelector<HTMLElement>(selector) : null;
      if (target) target.focus();
      else if (opener && document.contains(opener)) opener.focus();
    };
    // Only on mount/unmount: re-running would steal focus while typing.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
}
