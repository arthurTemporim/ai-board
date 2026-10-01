import { useCallback, useSyncExternalStore } from 'react';

const EVENT = 'fr:urlchange';

function subscribe(callback: () => void): () => void {
  window.addEventListener('popstate', callback);
  window.addEventListener(EVENT, callback);
  return () => {
    window.removeEventListener('popstate', callback);
    window.removeEventListener(EVENT, callback);
  };
}

/** Builds a link to a board (and optionally a card) using the query string as the router. */
export function hrefFor(boardId: number | null, cardId: number | null = null): string {
  const params = new URLSearchParams();
  if (boardId !== null) params.set('board', String(boardId));
  if (cardId !== null) params.set('card', String(cardId));
  return `?${params.toString()}`;
}

export function navigate(params: Record<string, number | null>): void {
  const url = new URL(window.location.href);
  for (const [key, value] of Object.entries(params)) {
    if (value === null) url.searchParams.delete(key);
    else url.searchParams.set(key, String(value));
  }
  window.history.pushState(null, '', url);
  window.dispatchEvent(new Event(EVENT));
}

/** A numeric query-string param, kept in sync with the address bar and back/forward. */
export function useNumberParam(key: string): [number | null, (value: number | null) => void] {
  const raw = useSyncExternalStore(
    subscribe,
    () => new URLSearchParams(window.location.search).get(key),
    () => null,
  );
  const value = raw !== null && /^\d+$/.test(raw) ? Number(raw) : null;
  const set = useCallback((next: number | null) => navigate({ [key]: next }), [key]);
  return [value, set];
}

/** A string query-string param (for example `?view=metrics`). */
export function useStringParam(key: string): [string | null, (value: string | null) => void] {
  const value = useSyncExternalStore(
    subscribe,
    () => new URLSearchParams(window.location.search).get(key),
    () => null,
  );
  const set = useCallback(
    (next: string | null) => {
      const url = new URL(window.location.href);
      if (next === null) url.searchParams.delete(key);
      else url.searchParams.set(key, next);
      window.history.pushState(null, '', url);
      window.dispatchEvent(new Event(EVENT));
    },
    [key],
  );
  return [value, set];
}
