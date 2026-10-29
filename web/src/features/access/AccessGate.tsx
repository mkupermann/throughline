import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
  type FormEvent,
} from "react";
import { useQueryClient } from "@tanstack/react-query";
import { request } from "@/lib/api";
import { setSessionToken } from "@/lib/session";
import { t } from "@/lib/ui";
import { useLanguage } from "@/lib/language";
import { Logo } from "@/components/Logo";
import "./access.css";

export type WorkspaceUser = {
  id: number;
  username: string;
  display_name: string;
  role: "viewer" | "editor" | "admin";
};
type Session = {
  mode: "local" | "team";
  user: WorkspaceUser | null;
  csrf_token: string | null;
};
const Context = createContext<{
  session: Session;
  reload: () => Promise<void>;
  signOut: () => Promise<void>;
}>({
  session: { mode: "local", user: null, csrf_token: null },
  reload: async () => {},
  signOut: async () => {},
});
export const useAccess = () => {
  const value = useContext(Context);
  return {
    ...value,
    admin:
      value.session.mode === "local" || value.session.user?.role === "admin",
    canEdit:
      value.session.mode === "local" || value.session.user?.role !== "viewer",
  };
};
export const adminPage = (path: string) =>
  /^\/(settings\/(ai|providers)|operate|console|pm)(\/|$)/.test(path);

export function AccessGate({ children }: { children: ReactNode }) {
  const { lang, setLang } = useLanguage();
  const queryClient = useQueryClient();
  const [session, setSession] = useState<Session | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const apply = (value: Session) => {
    setSessionToken(value.csrf_token);
    setSession(value);
  };
  const reload = async () => {
    try {
      setError("");
      apply(await request<Session>("/auth/session"));
    } catch (e) {
      setError((e as Error).message);
    }
  };
  useEffect(() => {
    void reload();
    const expired = () => {
      setSessionToken(null);
      queryClient.clear();
      setSession({ mode: "team", user: null, csrf_token: null });
    };
    window.addEventListener("throughline-session-expired", expired);
    return () =>
      window.removeEventListener("throughline-session-expired", expired);
  }, []);
  const login = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setBusy(true);
    setError("");
    try {
      const result = await request<Session>("/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(Object.fromEntries(data)),
      });
      queryClient.clear();
      apply(result);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  if (!session || (session.mode === "team" && !session.user))
    return (
      <main className="access-gate">
        <div className="access-card">
          <Logo size={40} />
          <h1>Throughline</h1>
          <div className="language-toggle">
            <button onClick={() => setLang("en")} aria-pressed={lang === "en"}>
              EN
            </button>
            <button onClick={() => setLang("de")} aria-pressed={lang === "de"}>
              DE
            </button>
          </div>
          <h2>{t("Sign in to your workspace")}</h2>
          {error && <p role="alert">{error}</p>}
          {!session ? (
            <>
              <p>{t("Connecting to workspace…")}</p>
              <button onClick={() => void reload()}>{t("Retry")}</button>
            </>
          ) : (
            <form onSubmit={login}>
              <label>
                {t("Username")}
                <input
                  name="username"
                  autoComplete="username"
                  required
                  maxLength={120}
                  autoFocus
                />
              </label>
              <label>
                {t("Password")}
                <input
                  name="password"
                  type="password"
                  autoComplete="current-password"
                  required
                  maxLength={128}
                />
              </label>
              <button type="submit" disabled={busy}>
                {busy ? t("Signing in…") : t("Sign in")}
              </button>
              <p>
                {t(
                  "Ask your workspace administrator for an account. Sessions expire after 30 minutes of inactivity.",
                )}
              </p>
            </form>
          )}
        </div>
      </main>
    );
  const signOut = async () => {
    await request("/auth/logout", { method: "POST" });
    queryClient.clear();
    await reload();
  };
  return (
    <Context.Provider value={{ session, reload, signOut }}>
      {children}
    </Context.Provider>
  );
}
