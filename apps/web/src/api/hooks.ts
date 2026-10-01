import { useMutation, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query';
import { applyMove, type Move } from '../board/dnd';
import { useToast } from '../state/toast';
import { api, ApiError } from './client';
import type { BoardDetail, Card, CardCreateInput, CardUpdateInput, Run, TriageCardInput } from './types';

export const keys = {
  users: ['users'] as const,
  boards: ['boards'] as const,
  board: (id: number) => ['board', id] as const,
  card: (id: number) => ['card', id] as const,
  tags: (boardId: number) => ['tags', boardId] as const,
  votes: (cardId: number, userId: number | null) => ['votes', cardId, userId] as const,
  suggestions: (cardId: number) => ['suggestions', cardId] as const,
  audit: (cardId: number) => ['audit', cardId] as const,
  run: (runId: number) => ['run', runId] as const,
  cardRuns: (cardId: number) => ['card-runs', cardId] as const,
  metrics: ['metrics'] as const,
};

export const errorText = (error: unknown): string =>
  error instanceof ApiError ? error.message : error instanceof Error ? error.message : 'Something went wrong';

export const useBoards = () => useQuery({ queryKey: keys.boards, queryFn: api.boards.list });
export const useBoard = (id: number | null) =>
  useQuery({ queryKey: keys.board(id ?? 0), queryFn: () => api.boards.get(id as number), enabled: id !== null });
export const useCard = (id: number | null) =>
  useQuery({ queryKey: keys.card(id ?? 0), queryFn: () => api.cards.get(id as number), enabled: id !== null });
export const useTags = (boardId: number | null) =>
  useQuery({ queryKey: keys.tags(boardId ?? 0), queryFn: () => api.tags.list(boardId as number), enabled: boardId !== null });
export const useVotes = (cardId: number, userId: number | null) =>
  useQuery({ queryKey: keys.votes(cardId, userId), queryFn: () => api.votes.get(cardId), enabled: userId !== null });

const refreshCard = (qc: QueryClient, card: Pick<Card, 'id' | 'board_id'>) =>
  Promise.all([
    qc.invalidateQueries({ queryKey: keys.board(card.board_id) }),
    qc.invalidateQueries({ queryKey: keys.card(card.id) }),
  ]);

export const MOVE_MUTATION_KEY = ['move-card'] as const;

/**
 * Drag and drop: the board cache is updated immediately, the server call runs in the
 * background, and the previous state is restored (with a message) if it fails.
 */
export function useMoveCard(boardId: number) {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationKey: MOVE_MUTATION_KEY,
    mutationFn: (move: Move & { version: number }) =>
      api.cards.move(move.cardId, { column_id: move.columnId, position: move.position, version: move.version }),
    onMutate: async (move) => {
      await qc.cancelQueries({ queryKey: keys.board(boardId) });
      const previous = qc.getQueryData<BoardDetail>(keys.board(boardId));
      if (previous) qc.setQueryData(keys.board(boardId), applyMove(previous, move));
      return { previous };
    },
    onError: (error, _move, context) => {
      if (context?.previous) qc.setQueryData(keys.board(boardId), context.previous);
      toast.error(
        error instanceof ApiError && error.isConflict
          ? 'That card was changed by someone else. The board has been refreshed.'
          : `Could not move the card: ${errorText(error)}`,
      );
    },
    onSettled: (_data, _error, move) => {
      // With several moves in flight, only the last one to settle refetches.
      if (qc.isMutating({ mutationKey: MOVE_MUTATION_KEY }) === 1) {
        void qc.invalidateQueries({ queryKey: keys.board(boardId) });
        void qc.invalidateQueries({ queryKey: keys.card(move.cardId) });
      }
    },
  });
}

export function useCreateCard() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: CardCreateInput) => api.cards.create(input),
    onSuccess: (card) => refreshCard(qc, card),
  });
}

/** Saves drawer edits: field changes first, then a column change (a move to the end). */
export function useSaveCard() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (args: { card: Card; version: number; changes: CardUpdateInput; columnId?: number }) => {
      let current: Card = args.card;
      let version = args.version;
      if (Object.keys(args.changes).length > 0) {
        current = await api.cards.update(args.card.id, { ...args.changes, version });
        version = current.version;
      }
      if (args.columnId !== undefined && args.columnId !== args.card.column_id) {
        current = await api.cards.move(args.card.id, { column_id: args.columnId, position: 10_000, version });
      }
      return current;
    },
    onSettled: (_data, _error, args) => refreshCard(qc, args.card),
  });
}

export function useAddComment(card: Pick<Card, 'id' | 'board_id'>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: string) => api.comments.add(card.id, body),
    onSuccess: () => refreshCard(qc, card),
  });
}

export function useAddFeedback(card: Pick<Card, 'id' | 'board_id'>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: { source: string; quote: string; customer_ref?: string | null }) =>
      api.feedback.add(card.id, input),
    onSuccess: () => refreshCard(qc, card),
  });
}

export function useRemoveFeedback(card: Pick<Card, 'id' | 'board_id'>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api.feedback.remove(id),
    onSuccess: () => refreshCard(qc, card),
  });
}

export function useToggleVote(card: Pick<Card, 'id' | 'board_id'>, userId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (voted: boolean) => (voted ? api.votes.remove(card.id) : api.votes.add(card.id)),
    onSuccess: (summary) => {
      qc.setQueryData(keys.votes(card.id, userId), summary);
      return refreshCard(qc, card);
    },
  });
}

export function useTagMutations(boardId: number) {
  const qc = useQueryClient();
  const refresh = () =>
    Promise.all([
      qc.invalidateQueries({ queryKey: keys.tags(boardId) }),
      qc.invalidateQueries({ queryKey: keys.board(boardId) }),
      qc.invalidateQueries({ queryKey: ['card'] }),
    ]);
  return {
    create: useMutation({
      mutationFn: (input: { name: string; color: string }) => api.tags.create({ board_id: boardId, ...input }),
      onSuccess: refresh,
    }),
    update: useMutation({
      mutationFn: (args: { id: number; name?: string; color?: string }) =>
        api.tags.update(args.id, { name: args.name, color: args.color }),
      onSuccess: refresh,
    }),
    remove: useMutation({ mutationFn: (id: number) => api.tags.remove(id), onSuccess: refresh }),
  };
}

// ---- AI triage ----------------------------------------------------------------------------------
export const useSuggestions = (cardId: number) =>
  useQuery({ queryKey: keys.suggestions(cardId), queryFn: () => api.suggestions.list(cardId) });
export const useAudit = (cardId: number, enabled: boolean) =>
  useQuery({ queryKey: keys.audit(cardId), queryFn: () => api.suggestions.audit(cardId), enabled });
export const useMetrics = () => useQuery({ queryKey: keys.metrics, queryFn: api.metrics.get });

/** The card's most recent run, so progress is shown again after a refresh or when reopening the drawer. */
export const useLatestRun = (cardId: number) =>
  useQuery({
    queryKey: keys.cardRuns(cardId),
    queryFn: async () => (await api.workflows.runsForCard(cardId, 1))[0] ?? null,
  });

/** A run, polled every second until it finishes or waits for a person. */
export const useRun = (runId: number | null, forcePoll = false) =>
  useQuery({
    queryKey: keys.run(runId ?? 0),
    queryFn: () => api.workflows.run(runId as number),
    enabled: runId !== null,
    refetchInterval: (query) =>
      forcePoll || query.state.data === undefined || query.state.data.status === 'running' ? 1000 : false,
  });

const invalidateCard = (qc: QueryClient, card: { id: number; board_id: number }) =>
  Promise.all([
    qc.invalidateQueries({ queryKey: keys.board(card.board_id) }),
    qc.invalidateQueries({ queryKey: keys.card(card.id) }),
    qc.invalidateQueries({ queryKey: keys.suggestions(card.id) }),
    qc.invalidateQueries({ queryKey: keys.audit(card.id) }),
    qc.invalidateQueries({ queryKey: keys.metrics }),
  ]);

/** Start triage (all steps, or a list) on a card. */
export function useStartTriage(card: Pick<Card, 'id' | 'board_id'>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (steps?: string[]) =>
      steps?.length === 1 ? api.workflows.step(steps[0] as string, card.id) : api.workflows.triage({ card_id: card.id }, steps),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.cardRuns(card.id) }),
  });
}

/** Create a card from a payload and triage it in one call. */
export function useCreateAndTriage() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (card: TriageCardInput) => api.workflows.triage({ card }),
    onSuccess: (accepted) =>
      Promise.all([
        qc.invalidateQueries({ queryKey: ['board'] }),
        qc.invalidateQueries({ queryKey: keys.cardRuns(accepted.card_id) }),
      ]),
  });
}

export function useResumeRun(card: Pick<Card, 'id' | 'board_id'>, runId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (decision: Record<string, unknown>) => api.workflows.resume(runId, decision),
    onSuccess: () =>
      Promise.all([qc.invalidateQueries({ queryKey: keys.run(runId) }), qc.invalidateQueries({ queryKey: keys.cardRuns(card.id) })]),
  });
}

/** Call when a run changes state, so the board, the card and the suggestion list catch up. */
export function useRefreshAfterRun(card: Pick<Card, 'id' | 'board_id'>) {
  const qc = useQueryClient();
  return () => invalidateCard(qc, card);
}

export function useAcceptSuggestion(card: Pick<Card, 'id' | 'board_id'>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: { sid: string; edits?: Record<string, unknown>; override?: { ratings: Record<string, number>; reason: string } }) =>
      api.suggestions.accept(card.id, args.sid, { edits: args.edits, override: args.override }),
    // Accepting can change other cards too (votes move to the primary card), so refetch every board.
    onSettled: () => Promise.all([invalidateCard(qc, card), qc.invalidateQueries({ queryKey: ['board'] }), qc.invalidateQueries({ queryKey: ['card'] })]),
  });
}

export function useRejectSuggestion(card: Pick<Card, 'id' | 'board_id'>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: { sid: string; reason?: string }) => api.suggestions.reject(card.id, args.sid, args.reason),
    onSettled: () => invalidateCard(qc, card),
  });
}

export type { Run };
