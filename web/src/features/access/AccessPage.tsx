import { useState, type FormEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { request } from "@/lib/api";
import { t } from "@/lib/ui";
import { useLanguage } from "@/lib/language";
import { useAccess, type WorkspaceUser } from "./AccessGate";

type User = WorkspaceUser & { enabled: boolean };
type Event = {
  id: number;
  occurred_at: string;
  actor: string;
  actor_name?: string | null;
  action: string;
  entity_type: string;
  entity_id: string | null;
  changed_fields: string[];
};
export function AccessPage() {
  useLanguage();
  const { session, admin, reload } = useAccess();
  const cache = useQueryClient();
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [before, setBefore] = useState<number | null>(null);
  const enabled = session.mode === "team" && admin;
  const users = useQuery({
    queryKey: ["access-users"],
    queryFn: () => request<{ users: User[] }>("/access/users"),
    enabled,
  });
  const audit = useQuery({
    queryKey: ["access-audit", before],
    queryFn: () =>
      request<{ events: Event[] }>(
        `/access/audit${before ? `?before=${before}` : ""}`,
      ),
    enabled,
  });
  if (session.mode === "local")
    return (
      <section className="access-page">
        <h1>{t("Workspace access")}</h1>
        <p>
          {t(
            "This installation uses local mode. Team mode is enabled by the operator after creating the first administrator account.",
          )}
        </p>
      </section>
    );
  const add = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = event.currentTarget;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await request("/access/users", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(Object.fromEntries(new FormData(form))),
      });
      form.reset();
      await cache.invalidateQueries({ queryKey: ["access-users"] });
      await cache.invalidateQueries({ queryKey: ["access-audit"] });
      setNotice(t("Account created."));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const update = async (user: User, role: User["role"], active: boolean) => {
    setBusy(true);
    setError("");
    try {
      await request(`/access/users/${user.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ role, enabled: active }),
      });
      await cache.invalidateQueries({ queryKey: ["access-users"] });
      await cache.invalidateQueries({ queryKey: ["access-audit"] });
      if (user.id === session.user?.id) await reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const password = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await request("/auth/password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(
          Object.fromEntries(new FormData(event.currentTarget)),
        ),
      });
      cache.clear();
      await reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="access-page">
      <h1>{t("Workspace access")}</h1>
      <p>
        {t(
          "One shared workspace. Viewers read project history; editors also curate knowledge and record project state. Administrators manage accounts, providers, processing and exports.",
        )}
      </p>
      {error && <p role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
      <details className="access-card">
        <summary>{t("Change your password")}</summary>
        <form onSubmit={password}>
          <label>
            {t("Current password")}
            <input
              name="current_password"
              type="password"
              required
              autoComplete="current-password"
            />
          </label>
          <label>
            {t("New password")}
            <input
              name="new_password"
              type="password"
              minLength={15}
              maxLength={128}
              required
              autoComplete="new-password"
            />
          </label>
          <button disabled={busy}>
            {t("Change password and sign out everywhere")}
          </button>
        </form>
      </details>
      {admin && (
        <>
          <section className="access-card">
            <h2>{t("Accounts")}</h2>
            {users.error && <p role="alert">{users.error.message}</p>}
            <div className="access-table">
              <table>
                <thead>
                  <tr>
                    <th>{t("Username")}</th>
                    <th>{t("Name")}</th>
                    <th>{t("Role")}</th>
                    <th>{t("Access")}</th>
                  </tr>
                </thead>
                <tbody>
                  {users.data?.users.map((user) => (
                    <tr key={user.id}>
                      <td>{user.username}</td>
                      <td>{user.display_name}</td>
                      <td>
                        <select
                          aria-label={`${t("Role")}: ${user.username}`}
                          value={user.role}
                          disabled={busy}
                          onChange={(e) =>
                            void update(
                              user,
                              e.target.value as User["role"],
                              user.enabled,
                            )
                          }
                        >
                          {["viewer", "editor", "admin"].map((r) => (
                            <option key={r} value={r}>
                              {t(r)}
                            </option>
                          ))}
                        </select>
                      </td>
                      <td>
                        <button
                          disabled={busy}
                          onClick={() =>
                            void update(user, user.role, !user.enabled)
                          }
                        >
                          {user.enabled
                            ? t("Disable account")
                            : t("Enable account")}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p>
              {t(
                "Changing a role or disabling an account signs that person out. The last active administrator cannot be removed.",
              )}
            </p>
            <details>
              <summary>{t("Add account")}</summary>
              <form onSubmit={add}>
                <label>
                  {t("Username")}
                  <input
                    name="username"
                    required
                    maxLength={120}
                    pattern="[a-zA-Z0-9][a-zA-Z0-9._@-]*"
                    autoComplete="off"
                  />
                </label>
                <label>
                  {t("Name")}
                  <input name="display_name" required maxLength={120} />
                </label>
                <label>
                  {t("Password")}
                  <input
                    name="password"
                    type="password"
                    required
                    minLength={15}
                    maxLength={128}
                    autoComplete="new-password"
                  />
                </label>
                <label>
                  {t("Role")}
                  <select name="role">
                    {["viewer", "editor", "admin"].map((r) => (
                      <option key={r} value={r}>
                        {t(r)}
                      </option>
                    ))}
                  </select>
                </label>
                <button disabled={busy}>{t("Create account")}</button>
              </form>
            </details>
          </section>
          <section className="access-card">
            <h2>{t("Change history")}</h2>
            <p>
              {t(
                "Records show who changed which fields and when. Passwords, provider keys and conversation contents are excluded.",
              )}
            </p>
            {audit.error && <p role="alert">{audit.error.message}</p>}
            <div className="access-table">
              <table>
                <thead>
                  <tr>
                    <th>{t("Time")}</th>
                    <th>{t("Actor")}</th>
                    <th>{t("Action")}</th>
                    <th>{t("Record")}</th>
                    <th>{t("Changed fields")}</th>
                  </tr>
                </thead>
                <tbody>
                  {audit.data?.events.map((e) => (
                    <tr key={e.id}>
                      <td>
                        <time dateTime={e.occurred_at}>
                          {new Date(e.occurred_at).toLocaleString(undefined, {
                            timeZoneName: "short",
                          })}
                        </time>
                      </td>
                      <td title={e.actor}>{e.actor_name || e.actor}</td>
                      <td>{e.action}</td>
                      <td>
                        {e.entity_type} {e.entity_id}
                      </td>
                      <td>{e.changed_fields.join(", ")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <button onClick={() => setBefore(null)} disabled={!before}>
              {t("Latest")}
            </button>
            {(audit.data?.events.length ?? 0) === 50 && (
              <button onClick={() => setBefore(audit.data!.events.at(-1)!.id)}>
                {t("Older")}
              </button>
            )}
          </section>
        </>
      )}
    </section>
  );
}
