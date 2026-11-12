"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

import { type Capabilities, type Plant, type User, endpoints, getToken, setToken } from "./api";

interface AppContextValue {
  ready: boolean;
  user: User | null;
  plants: Plant[];
  plant: Plant | null;
  capabilities: Capabilities | null;
  selectPlant: (id: number) => void;
  refreshPlants: () => Promise<void>;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
}

const AppContext = createContext<AppContextValue | null>(null);
const PLANT_KEY = "solar.plant";

export function AppProvider({ children }: { children: React.ReactNode }) {
  const [ready, setReady] = useState(false);
  const [user, setUser] = useState<User | null>(null);
  const [plants, setPlants] = useState<Plant[]>([]);
  const [plantId, setPlantId] = useState<number | null>(null);
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);

  const loadSession = useCallback(async () => {
    if (!getToken()) {
      setUser(null);
      setPlants([]);
      setReady(true);
      return;
    }
    try {
      const [me, ps, caps] = await Promise.all([endpoints.me(), endpoints.plants(), endpoints.capabilities()]);
      setUser(me);
      setPlants(ps);
      setCapabilities(caps);
      let stored: number | null = null;
      try {
        stored = Number(window.localStorage.getItem(PLANT_KEY)) || null;
      } catch {
        /* ignore */
      }
      const chosen = ps.find((p) => p.id === stored)?.id ?? ps[0]?.id ?? null;
      setPlantId(chosen);
    } catch {
      setUser(null);
    } finally {
      setReady(true);
    }
  }, []);

  useEffect(() => {
    // Session bootstrap is an external-system subscription (localStorage + API); the
    // synchronous "no token" branch sets ready=true immediately by design.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void loadSession();
  }, [loadSession]);

  const selectPlant = useCallback((id: number) => {
    setPlantId(id);
    try {
      window.localStorage.setItem(PLANT_KEY, String(id));
    } catch {
      /* ignore */
    }
  }, []);

  const refreshPlants = useCallback(async () => {
    const ps = await endpoints.plants();
    setPlants(ps);
    if (!ps.find((p) => p.id === plantId)) setPlantId(ps[0]?.id ?? null);
  }, [plantId]);

  const login = useCallback(
    async (email: string, password: string) => {
      const res = await endpoints.login(email, password);
      setToken(res.access_token);
      setReady(false);
      await loadSession();
    },
    [loadSession],
  );

  const logout = useCallback(() => {
    setToken(null);
    setUser(null);
    setPlants([]);
    setPlantId(null);
  }, []);

  const value = useMemo<AppContextValue>(
    () => ({
      ready,
      user,
      plants,
      plant: plants.find((p) => p.id === plantId) ?? null,
      capabilities,
      selectPlant,
      refreshPlants,
      login,
      logout,
    }),
    [ready, user, plants, plantId, capabilities, selectPlant, refreshPlants, login, logout],
  );

  return <AppContext.Provider value={value}>{children}</AppContext.Provider>;
}

export function useApp(): AppContextValue {
  const ctx = useContext(AppContext);
  if (!ctx) throw new Error("useApp must be used inside AppProvider");
  return ctx;
}

/** Convenience: the selected plant id or null while loading. */
export function usePlantId(): number | null {
  return useApp().plant?.id ?? null;
}
