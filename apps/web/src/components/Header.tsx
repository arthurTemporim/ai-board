import type { Board } from '../api/types';
import { useCurrentUser } from '../state/user';

interface Props {
  boards: Board[];
  boardId: number | null;
  onSelectBoard: (id: number) => void;
}

export function Header({ boards, boardId, onSelectBoard }: Props) {
  const { users, current, select } = useCurrentUser();
  return (
    <header className="flex flex-wrap items-center gap-3 border-b border-slate-200 bg-white px-4 py-2.5">
      <h1 className="text-base font-semibold tracking-tight">Feature Requests</h1>
      <div className="flex items-center gap-2">
        <label htmlFor="board-switcher" className="text-sm text-slate-500">
          Board
        </label>
        <select
          id="board-switcher"
          className="input !w-auto min-w-48 font-medium"
          value={boardId ?? ''}
          onChange={(e) => onSelectBoard(Number(e.target.value))}
        >
          {boards.map((board) => (
            <option key={board.id} value={board.id}>
              {board.name}
            </option>
          ))}
        </select>
      </div>
      <div className="ml-auto flex items-center gap-2">
        <label htmlFor="acting-user" className="text-sm text-slate-500">
          Acting as
        </label>
        <select
          id="acting-user"
          className="input !w-auto"
          value={current?.id ?? ''}
          onChange={(e) => select(Number(e.target.value))}
          disabled={users.length === 0}
        >
          {users.map((user) => (
            <option key={user.id} value={user.id}>
              {user.name}
            </option>
          ))}
        </select>
      </div>
    </header>
  );
}
