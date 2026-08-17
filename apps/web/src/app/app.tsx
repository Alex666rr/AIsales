import { useEffect, useState } from "react";

import { getCurrentSession, SessionContext } from "../shared/api/client";
import { LoginForm } from "../features/auth/login-form";
import { StaffInvitationForm } from "../features/staff/staff-invitation-form";
import { SetupWizard } from "../features/auth/setup-wizard";

type AuthenticationState =
  | { kind: "loading" }
  | { kind: "anonymous" }
  | { kind: "authenticated"; session: SessionContext }
  | { kind: "unavailable" };

export function App() {
  const setupToken = new URLSearchParams(window.location.search).get("token");
  if (window.location.pathname === "/setup") return <SetupWizard setupToken={setupToken ?? ""} />;
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
    <main className="application-shell" data-testid="application-shell" data-theme="control-room">
      <aside>
        <p className="brand-mark">AIsales</p>
        <nav aria-label="Основная навигация">
          <a aria-current="page" href="#overview">Обзор</a>
          <a href="#accounts">Аккаунты</a>
          <a href="#team">Команда</a>
        </nav>
      </aside>
      <section className="workspace" id="overview">
        <h1>Администрирование</h1>
        <p className="muted">Управляйте аккаунтами и доступом команды через защищённую серверную сессию.</p>
        <div className="workspace-grid">
          <section className="context-card" id="accounts" aria-labelledby="accounts-heading">
            <div className="section-heading">
              <div>
                <h2 id="accounts-heading">Telegram-аккаунты</h2>
                <p className="muted">Подключённые рабочие аккаунты и их состояние будут отображаться здесь.</p>
              </div>
              <span className="status-chip">Нет подключений</span>
            </div>
            <p className="empty-state">Подключение аккаунтов станет доступно в следующем функциональном блоке. Сейчас платформа не создаёт и не имитирует подключения.</p>
          </section>
          <section className="context-card" id="team" aria-labelledby="team-heading">
            <div className="section-heading">
              <div>
                <h2 id="team-heading">Доступ команды</h2>
                <p className="muted">Текущая роль и приглашения сотрудников организации.</p>
              </div>
            </div>
            <dl>
              <dt>Роль</dt><dd>{state.session.roles.join(", ")}</dd>
              <dt>Организация</dt><dd>{state.session.organization_id}</dd>
            </dl>
            {state.session.roles.includes("company_owner") && <StaffInvitationForm />}
          </section>
        </div>
      </section>
    </main>
  );
}
