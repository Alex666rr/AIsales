import { useEffect, useState } from "react";

import { getCurrentSession, SessionContext } from "../shared/api/client";
import { LoginForm } from "../features/auth/login-form";

type AuthenticationState =
  | { kind: "loading" }
  | { kind: "anonymous" }
  | { kind: "authenticated"; session: SessionContext }
  | { kind: "unavailable" };

export function App() {
  const [state, setState] = useState<AuthenticationState>({ kind: "loading" });

  async function refreshSession() {
    try {
      const session = await getCurrentSession();
      setState(session ? { kind: "authenticated", session } : { kind: "anonymous" });
    } catch {
      setState({ kind: "unavailable" });
    }
  }

  useEffect(() => {
    void refreshSession();
  }, []);

  if (state.kind === "loading") return <main className="centered">Проверяем доступ…</main>;
  if (state.kind === "unavailable") return <main className="centered">Сервис временно недоступен. Попробуйте обновить страницу.</main>;
  if (state.kind === "anonymous") return <LoginForm onAuthenticated={refreshSession} />;

  return (
    <main className="application-shell">
      <aside>
        <p className="eyebrow">AIsales</p>
        <nav aria-label="Основная навигация">
          <a aria-current="page" href="#overview">Обзор</a>
          <a href="#organization">Организация</a>
          <a href="#users">Пользователи</a>
        </nav>
      </aside>
      <section className="workspace" id="overview">
        <p className="eyebrow">Stage 1 · S1.03</p>
        <h1>Администрирование</h1>
        <p className="muted">Защищённая оболочка готова. Следующим шагом в этом интерфейсе будет управление приглашениями staff.</p>
        <article className="context-card">
          <h2>Текущий доступ</h2>
          <dl>
            <dt>Роль</dt><dd>{state.session.roles.join(", ")}</dd>
            <dt>Организация</dt><dd>{state.session.organization_id}</dd>
          </dl>
        </article>
      </section>
    </main>
  );
}
