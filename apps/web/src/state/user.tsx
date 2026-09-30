import { useQuery } from '@tanstack/react-query';
import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { api, setActorId } from '../api/client';
import type { User } from '../api/types';

interface UserState {
  users: User[];
  current: User | null;
  select: (id: number) => void;
}

const UserContext = createContext<UserState | null>(null);
const STORAGE_KEY = 'fr.userId';

/** "Who am I" picker state; there is no login, the id is sent as X-User-Id. */
export function UserProvider({ children }: { children: ReactNode }) {
  const { data: users = [] } = useQuery({ queryKey: ['users'], queryFn: api.users.list, staleTime: Infinity });
  const [selectedId, setSelectedId] = useState<number | null>(() => {
    const saved = window.localStorage.getItem(STORAGE_KEY);
    return saved ? Number(saved) : null;
  });

  const current = users.find((u) => u.id === selectedId) ?? users[0] ?? null;
  // Set synchronously so child effects and queries already carry the header.
  setActorId(current?.id ?? null);
  useEffect(() => () => setActorId(null), []);

  const value = useMemo<UserState>(
    () => ({
      users,
      current,
      select: (id) => {
        window.localStorage.setItem(STORAGE_KEY, String(id));
        setSelectedId(id);
      },
    }),
    [users, current],
  );
  return <UserContext.Provider value={value}>{children}</UserContext.Provider>;
}

export function useCurrentUser(): UserState {
  const value = useContext(UserContext);
  if (!value) throw new Error('useCurrentUser must be used inside <UserProvider>');
  return value;
}
