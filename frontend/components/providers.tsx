"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  QueryClient,
  QueryClientProvider,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { MotionConfig, useReducedMotion } from "motion/react";
import { ApiError, errorFromResponse, messageFor } from "@/lib/api";
import type { Info, Status } from "@/lib/types";

const KEY_STORE = "ollama-provider-admin-key";
type Options = { method?: string; body?: unknown; signal?: AbortSignal };
type Requester = <T>(path: string, options?: Options) => Promise<T>;
const AuthContext = createContext<{
  authenticated: boolean;
  ready: boolean;
  notice: string;
  login: (key: string) => Promise<void>;
  logout: (notice?: string) => void;
  request: Requester;
} | null>(null);
const PreferencesContext = createContext<{
  theme: string;
  setTheme: (theme: string) => void;
  animations: boolean;
  setAnimations: (value: boolean) => void;
  motionEnabled: boolean;
} | null>(null);

export function Providers({ children }: { children: ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            retry: false,
            refetchOnWindowFocus: false,
            staleTime: 5000,
          },
          mutations: { retry: false },
        },
      }),
  );
  return (
    <QueryClientProvider client={client}>
      <Preferences>
        <Auth>{children}</Auth>
      </Preferences>
    </QueryClientProvider>
  );
}

function Preferences({ children }: { children: ReactNode }) {
  const [theme, updateTheme] = useState("system");
  const [animations, updateAnimations] = useState(true);
  const reduced = useReducedMotion();
  const setTheme = useCallback((value: string) => {
    updateTheme(value);
    if (value === "system") delete document.documentElement.dataset.theme;
    else document.documentElement.dataset.theme = value;
    try {
      localStorage.setItem("ollama-ui-theme-v1", value);
    } catch {
      /* Preferences remain in memory. */
    }
  }, []);
  const setAnimations = useCallback((value: boolean) => {
    updateAnimations(value);
    document.documentElement.dataset.motion = value ? "on" : "off";
    try {
      localStorage.setItem("ollama-ui-motion-v1", String(value));
    } catch {
      /* Preferences remain in memory. */
    }
  }, []);
  useEffect(() => {
    try {
      const stored = localStorage.getItem("ollama-ui-theme-v1");
      if (stored && ["light", "dark", "system"].includes(stored))
        setTheme(stored);
      setAnimations(localStorage.getItem("ollama-ui-motion-v1") !== "false");
    } catch {
      /* Storage can be disabled. */
    }
  }, [setTheme, setAnimations]);
  return (
    <PreferencesContext.Provider
      value={{
        theme,
        setTheme,
        animations,
        setAnimations,
        motionEnabled: animations && !reduced,
      }}
    >
      <MotionConfig reducedMotion={animations ? "user" : "always"}>
        {children}
      </MotionConfig>
    </PreferencesContext.Provider>
  );
}

function Auth({ children }: { children: ReactNode }) {
  const client = useQueryClient();
  const key = useRef<string | null>(null);
  const epoch = useRef(0);
  const controllers = useRef(new Set<AbortController>());
  const [authenticated, setAuthenticated] = useState(false);
  const [ready, setReady] = useState(false);
  const [notice, setNotice] = useState("");
  const logout = useCallback(
    (reason = "") => {
      epoch.current++;
      key.current = null;
      for (const controller of controllers.current) controller.abort();
      controllers.current.clear();
      void client.cancelQueries();
      client.clear();
      try {
        sessionStorage.removeItem(KEY_STORE);
      } catch {
        /* Ignore disabled storage. */
      }
      setAuthenticated(false);
      setNotice(reason);
      setReady(true);
    },
    [client],
  );
  const request: Requester = useCallback(
    async <T,>(path: string, options: Options = {}) => {
      const started = epoch.current;
      const controller = new AbortController();
      const cancel = () => controller.abort();
      if (options.signal?.aborted) controller.abort();
      else options.signal?.addEventListener("abort", cancel, { once: true });
      controllers.current.add(controller);
      try {
        const response = await fetch(`/admin${path}`, {
          method: options.method || "GET",
          cache: "no-store",
          signal: controller.signal,
          headers: {
            Authorization: `Bearer ${key.current || ""}`,
            ...(options.body !== undefined
              ? { "Content-Type": "application/json" }
              : {}),
          },
          body:
            options.body !== undefined
              ? JSON.stringify(options.body)
              : undefined,
        });
        if (started !== epoch.current)
          throw new DOMException("Session ended", "AbortError");
        if (response.status === 401) {
          logout(
            "Sesi berakhir atau admin key sudah diganti. Silakan masuk kembali.",
          );
          throw new ApiError(
            "Admin key tidak sesuai. Periksa kembali key Anda.",
            401,
          );
        }
        if (!response.ok) throw await errorFromResponse(response);
        return (await response.json()) as T;
      } finally {
        controllers.current.delete(controller);
        options.signal?.removeEventListener("abort", cancel);
      }
    },
    [logout],
  );
  const login = useCallback(
    async (value: string) => {
      const started = ++epoch.current;
      key.current = value;
      try {
        const info = await request<Info>("/info");
        if (started !== epoch.current) return;
        client.setQueryData(["info"], info);
        try {
          sessionStorage.setItem(KEY_STORE, value);
        } catch {
          /* Stay in memory. */
        }
        setNotice("");
        setAuthenticated(true);
        setReady(true);
      } catch (error) {
        if (started === epoch.current) key.current = null;
        throw error;
      }
    },
    [client, request],
  );
  useEffect(() => {
    let saved: string | null = null;
    try {
      saved = sessionStorage.getItem(KEY_STORE);
    } catch {
      /* Ignore disabled storage. */
    }
    if (saved) void login(saved).catch(() => setReady(true));
    else setReady(true);
  }, [login]);
  return (
    <AuthContext.Provider
      value={{ authenticated, ready, notice, login, logout, request }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const value = useContext(AuthContext);
  if (!value) throw new Error("Missing auth provider");
  return value;
}
export function usePreferences() {
  const value = useContext(PreferencesContext);
  if (!value) throw new Error("Missing preferences");
  return value;
}
export function useInfo() {
  const { request, authenticated } = useAuth();
  return useQuery({
    queryKey: ["info"],
    queryFn: ({ signal }) => request<Info>("/info", { signal }),
    enabled: authenticated,
  });
}
export function useStatus() {
  const { request, authenticated } = useAuth();
  return useQuery({
    queryKey: ["status"],
    queryFn: ({ signal }) => request<Status>("/status", { signal }),
    enabled: authenticated,
    refetchInterval: 10000,
  });
}
export function useTask() {
  const locked = useRef(false);
  const mounted = useRef(true);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [fields, setFields] = useState<Record<string, string>>({});
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  useEffect(() => {
    if (Object.keys(fields).length)
      document
        .querySelector<HTMLInputElement>(
          'dialog[open] input[aria-invalid="true"]',
        )
        ?.focus();
  }, [fields]);
  async function run(work: () => Promise<void>) {
    if (locked.current) return false;
    locked.current = true;
    setPending(true);
    setError("");
    setFields({});
    try {
      await work();
      return true;
    } catch (err) {
      if (
        mounted.current &&
        !(err instanceof DOMException && err.name === "AbortError")
      ) {
        setError(messageFor(err));
        setFields(err instanceof ApiError ? err.fields : {});
      }
      return false;
    } finally {
      locked.current = false;
      if (mounted.current) setPending(false);
    }
  }
  return { pending, error, fields, run };
}
