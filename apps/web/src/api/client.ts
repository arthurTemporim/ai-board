import type {
  AuditEntry,
  Board,
  BoardDetail,
  Card,
  CardCreateInput,
  CardDetail,
  CardUpdateInput,
  Comment,
  Decision,
  ErrorDetail,
  Feedback,
  Metrics,
  Run,
  RunAccepted,
  RunSummary,
  Suggestion,
  Tag,
  TriageCardInput,
  User,
  VoteSummary,
} from './types';

const BASE = import.meta.env.VITE_API_BASE ?? '/api';

/** Every non-2xx response, plus network failures (status 0). */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly details: ErrorDetail[] = [],
  ) {
    super(message);
    this.name = 'ApiError';
  }

  get isConflict(): boolean {
    return this.status === 409;
  }
}

let actorId: number | null = null;
/** The acting user, sent as X-User-Id on every request. */
export function setActorId(id: number | null): void {
  actorId = id;
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = {};
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (actorId !== null) headers['X-User-Id'] = String(actorId);

  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, 'network_error', 'Cannot reach the server. Check your connection.');
  }

  if (response.status === 204) return undefined as T;
  const data: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const error = (data as { error?: { code?: string; message?: string; details?: ErrorDetail[] } } | null)?.error;
    throw new ApiError(
      response.status,
      error?.code ?? 'http_error',
      error?.message ?? (response.statusText || 'Request failed'),
      error?.details ?? [],
    );
  }
  return data as T;
}

const query = (params: Record<string, string | number | undefined>): string => {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined) search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : '';
};

export const api = {
  workflows: {
    triage: (target: { card_id: number } | { card: TriageCardInput }, steps?: string[]) =>
      request<RunAccepted>('POST', '/workflows/triage', { ...target, ...(steps ? { steps } : {}) }),
    step: (step: string, cardId: number) => request<RunAccepted>('POST', `/workflows/steps/${step}`, { card_id: cardId }),
    run: (runId: number) => request<Run>('GET', `/workflows/runs/${runId}`),
    runsForCard: (cardId: number, limit = 5) => request<RunSummary[]>('GET', `/workflows/runs${query({ card_id: cardId, limit })}`),
    resume: (runId: number, decision: Record<string, unknown>) =>
      request<RunSummary>('POST', `/workflows/runs/${runId}/resume`, { decision }),
  },
  suggestions: {
    list: (cardId: number) => request<Suggestion[]>('GET', `/cards/${cardId}/suggestions`),
    accept: (cardId: number, sid: string, body: { edits?: Record<string, unknown>; override?: { ratings: Record<string, number>; reason: string } } = {}) =>
      request<Decision>('POST', `/cards/${cardId}/suggestions/${sid}/accept`, body),
    reject: (cardId: number, sid: string, reason?: string) =>
      request<Decision>('POST', `/cards/${cardId}/suggestions/${sid}/reject`, reason ? { reason } : {}),
    audit: (cardId: number) => request<AuditEntry[]>('GET', `/cards/${cardId}/audit`),
  },
  metrics: { get: () => request<Metrics>('GET', '/metrics') },
  users: { list: () => request<User[]>('GET', '/users') },
  boards: {
    list: () => request<Board[]>('GET', '/boards'),
    get: (id: number) => request<BoardDetail>('GET', `/boards/${id}`),
  },
  cards: {
    get: (id: number) => request<CardDetail>('GET', `/cards/${id}`),
    create: (input: CardCreateInput) => request<Card>('POST', '/cards', input),
    update: (id: number, input: CardUpdateInput) => request<Card>('PATCH', `/cards/${id}`, input),
    move: (id: number, move: { column_id: number; position: number; version?: number }) =>
      request<Card>('POST', `/cards/${id}/move`, move),
  },
  comments: {
    add: (cardId: number, body: string) => request<Comment>('POST', `/cards/${cardId}/comments`, { body }),
  },
  feedback: {
    add: (cardId: number, input: { source: string; quote: string; customer_ref?: string | null }) =>
      request<Feedback>('POST', `/cards/${cardId}/feedback`, input),
    remove: (id: number) => request<void>('DELETE', `/feedback/${id}`),
  },
  votes: {
    get: (cardId: number) => request<VoteSummary>('GET', `/cards/${cardId}/votes`),
    add: (cardId: number) => request<VoteSummary>('POST', `/cards/${cardId}/votes`),
    remove: (cardId: number) => request<VoteSummary>('DELETE', `/cards/${cardId}/votes`),
  },
  tags: {
    list: (boardId: number) => request<Tag[]>('GET', `/tags${query({ board_id: boardId })}`),
    create: (input: { board_id: number; name: string; color: string }) => request<Tag>('POST', '/tags', input),
    update: (id: number, input: { name?: string; color?: string }) => request<Tag>('PATCH', `/tags/${id}`, input),
    remove: (id: number) => request<void>('DELETE', `/tags/${id}`),
  },
};
