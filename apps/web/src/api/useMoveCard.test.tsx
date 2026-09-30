import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { makeBoard } from '../test/fixtures';
import { ToastProvider } from '../state/toast';
import { keys, useMoveCard } from './hooks';
import type { BoardDetail } from './types';

function setup() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  client.setQueryData(keys.board(1), makeBoard());
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>
      <ToastProvider>{children}</ToastProvider>
    </QueryClientProvider>
  );
  return { client, ...renderHook(() => useMoveCard(1), { wrapper }) };
}

const inboxTitles = (client: QueryClient) =>
  client.getQueryData<BoardDetail>(keys.board(1))?.columns[0]?.cards.map((c) => c.title);

afterEach(() => vi.unstubAllGlobals());

describe('useMoveCard', () => {
  it('updates the board immediately, before the server answers', async () => {
    let respond: (r: Response) => void = () => {};
    const fetchMock = vi.fn(() => new Promise<Response>((resolve) => (respond = resolve)));
    vi.stubGlobal('fetch', fetchMock);
    const { client, result } = setup();

    act(() => result.current.mutate({ cardId: 1, columnId: 10, position: 2, version: 1 }));

    await waitFor(() => expect(inboxTitles(client)).toEqual(['B', 'C', 'A']));
    expect(result.current.isPending).toBe(true);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('/api/cards/1/move');
    expect(JSON.parse(init.body as string)).toEqual({ column_id: 10, position: 2, version: 1 });

    respond(new Response(JSON.stringify({ id: 1 }), { status: 200 }));
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
  });

  it('rolls the board back and shows a message when the server rejects the move', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify({ error: { code: 'version_conflict', message: 'stale', details: [] } }), { status: 409 })),
    );
    const { client, result } = setup();

    act(() => result.current.mutate({ cardId: 1, columnId: 10, position: 2, version: 1 }));

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(inboxTitles(client)).toEqual(['A', 'B', 'C']);
    expect(await screen.findByRole('alert')).toHaveTextContent(/changed by someone else/i);
  });

  it('rolls back on a network failure too', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => Promise.reject(new TypeError('offline'))));
    const { client, result } = setup();

    act(() => result.current.mutate({ cardId: 2, columnId: 20, position: 0, version: 1 }));

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(inboxTitles(client)).toEqual(['A', 'B', 'C']);
    expect(await screen.findByRole('alert')).toHaveTextContent(/cannot reach the server/i);
  });
});
