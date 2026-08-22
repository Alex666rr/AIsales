import { useEffect, useState } from "react";

import { getCurrentSession, SessionContext } from "../shared/api/client";
import { LoginForm } from "../features/auth/login-form";
import { StaffInvitationForm } from "../features/staff/staff-invitation-form";
import { SetupWizard } from "../features/auth/setup-wizard";
import { TelegramConnectionPanel } from "../features/telegram/telegram-connection-panel";
import { TelegramAccountsList } from "../features/telegram/telegram-accounts-list";

type AuthenticationState =
  | { kind: "loading" }
  | { kind: "anonymous" }
  | { kind: "authenticated"; session: SessionContext }
  | { kind: "unavailable" };

type WorkspaceView = "overview" | "accounts" | "team";

export function App() {
  const setupToken = new URLSearchParams(window.location.search).get("token");
  if (window.location.pathname === "/setup") return <SetupWizard setupToken={setupToken ?? ""} />;
  const [state, setState] = useState<AuthenticationState>({ kind: "loading" });
  const [view, setView] = useState<WorkspaceView>("overview");

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
          <button aria-current={view === "overview" ? "page" : undefined} onClick={() => setView("overview")} type="button">Обзор</button>
          <button aria-current={view === "accounts" ? "page" : undefined} onClick={() => setView("accounts")} type="button">Аккаунты</button>
          <button aria-current={view === "team" ? "page" : undefined} onClick={() => setView("team")} type="button">Команда</button>
        </nav>
      </aside>
      <section className="workspace">
        {view === "overview" && <>
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
        </>}
        {view === "accounts" && <section className="context-card workspace-page" aria-labelledby="accounts-page-heading">
          <div className="section-heading">
            <div>
              <h1 id="accounts-page-heading">Telegram-аккаунты</h1>
              <p className="muted">Подключённые рабочие аккаунты и их состояние.</p>
            </div>
            <span className="status-chip">Статусы аккаунтов</span>
          </div>
          <TelegramAccountsList />
          {state.session.roles.includes("company_owner")
            ? <TelegramConnectionPanel />
            : <p className="empty-state">Подключения Telegram доступны владельцу организации.</p>}
        </section>}
        {view === "team" && <section className="context-card workspace-page" aria-labelledby="team-page-heading">
          <div className="section-heading">
            <div>
              <h1 id="team-page-heading">Команда</h1>
              <p className="muted">Роль текущего пользователя и управление приглашениями сотрудников.</p>
            </div>
          </div>
          <dl>
            <dt>Роль</dt><dd>{state.session.roles.join(", ")}</dd>
            <dt>Организация</dt><dd>{state.session.organization_id}</dd>
          </dl>
          {state.session.roles.includes("company_owner") && <StaffInvitationForm />}
        </section>}
      </section>
    </main>
  );
}
