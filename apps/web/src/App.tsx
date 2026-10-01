import { useEffect, useState } from 'react';
import { useBoard, useBoards, useTags, errorText } from './api/hooks';
import type { Card } from './api/types';
import { BoardView } from './components/BoardView';
import { CardDrawer } from './components/CardDrawer';
import { CreateCardModal } from './components/CreateCardModal';
import { Header } from './components/Header';
import { TagManager } from './components/TagManager';
import { Toolbar } from './components/Toolbar';
import { NO_FILTER, type CardFilter } from './lib/filter';
import { MetricsPage } from './components/MetricsPage';
import { navigate, useNumberParam, useStringParam } from './state/urlState';

export default function App() {
  const boards = useBoards();
  const [boardParam] = useNumberParam('board');
  const [cardId, setCardId] = useNumberParam('card');
  const boardId = boardParam ?? boards.data?.[0]?.id ?? null;
  const board = useBoard(boardId);
  const tags = useTags(boardId);

  const [view, setView] = useStringParam('view');
  const [filter, setFilter] = useState<CardFilter>(NO_FILTER);
  const [creating, setCreating] = useState(false);
  const [managingTags, setManagingTags] = useState(false);

  // A filter from one board means nothing on another.
  useEffect(() => setFilter(NO_FILTER), [boardId]);

  const selectBoard = (id: number) => navigate({ board: id, card: null });
  const openCard = (card: Card) => setCardId(card.id);

  return (
    <div className="flex h-screen flex-col">
      <Header
        boards={boards.data ?? []}
        boardId={boardId}
        onSelectBoard={selectBoard}
        view={view === 'metrics' ? 'metrics' : 'board'}
        onSelectView={(next) => setView(next === 'metrics' ? 'metrics' : null)}
      />
      <main className="flex min-h-0 flex-1 flex-col">
        {view === 'metrics' && <MetricsPage />}
        {view !== 'metrics' && boards.isError && (
          <p role="alert" className="m-4 rounded-md bg-red-50 p-3 text-sm text-red-700">
            Could not load boards: {errorText(boards.error)}
          </p>
        )}
        {view !== 'metrics' && boards.data?.length === 0 && <p className="m-4 text-sm text-slate-500">There are no boards yet.</p>}
        {view !== 'metrics' && board.isError && (
          <p role="alert" className="m-4 rounded-md bg-red-50 p-3 text-sm text-red-700">
            Could not load this board: {errorText(board.error)}
          </p>
        )}
        {view !== 'metrics' && board.isLoading && <p className="m-4 text-sm text-slate-500">Loading board…</p>}
        {view !== 'metrics' && board.data && (
          <>
            <Toolbar
              tags={tags.data ?? []}
              reviewCount={board.data.columns.reduce((n, c) => n + c.cards.filter((card) => card.needs_review).length, 0)}
              filter={filter}
              onFilterChange={setFilter}
              onNewCard={() => setCreating(true)}
              onManageTags={() => setManagingTags(true)}
            />
            <div className="min-h-0 flex-1">
              <BoardView board={board.data} filter={filter} onOpenCard={openCard} />
            </div>
            {creating && (
              <CreateCardModal
                board={board.data}
                tags={tags.data ?? []}
                onClose={() => setCreating(false)}
                onCreated={(id) => {
                  setCreating(false);
                  setCardId(id);
                }}
              />
            )}
            {managingTags && boardId !== null && (
              <TagManager boardId={boardId} tags={tags.data ?? []} onClose={() => setManagingTags(false)} />
            )}
            {cardId !== null && (
              <CardDrawer
                cardId={cardId}
                board={board.data}
                tags={tags.data ?? []}
                onClose={() => setCardId(null)}
                onOpenCard={setCardId}
              />
            )}
          </>
        )}
      </main>
    </div>
  );
}
