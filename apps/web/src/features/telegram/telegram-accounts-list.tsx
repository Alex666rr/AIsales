import { useEffect, useState } from "react";

import {
  listTelegramAccounts,
  TelegramAccountStatus,
  transitionTelegramAccount,
} from "../../shared/api/client";

type DirectoryState =
  | { kind: "loading" }
  | { kind: "ready"; accounts: TelegramAccountStatus[] }
  | { kind: "unavailable" };

const stateLabels: Record<string, string> = {
  active: "Активен",
  quarantine: "Карантин",
  paused: "Приостановлен",
  archived: "Архив",
  reauth_required: "Нужна авторизация",
  limited: "Ограничен",
  blocked: "Заблокирован",
};

function accountLabel(accountId: string): string {
  return `Аккаунт #${accountId.slice(0, 8)}`;
}

export function TelegramAccountsList({ canManage = false }: { canManage?: boolean }) {
  const [state, setState] = useState<DirectoryState>({ kind: "loading" });
  const [pendingAccountId, setPendingAccountId] = useState<string | null>(null);
  const [controlError, setControlError] = useState<string | null>(null);
  const [archiveCandidate, setArchiveCandidate] = useState<TelegramAccountStatus | null>(null);

  useEffect(() => {
    let current = true;
    void listTelegramAccounts()
      .then((accounts) => {
        if (current) setState({ kind: "ready", accounts });
      })
      .catch(() => {
        if (current) setState({ kind: "unavailable" });
      });
    return () => { current = false; };
  }, []);

  if (state.kind === "loading") return <p className="muted">Загружаем подключённые аккаунты…</p>;
  if (state.kind === "unavailable") return <p className="empty-state">Не удалось загрузить аккаунты. Обновите страницу.</p>;
  if (state.accounts.length === 0) return <p className="empty-state">Подключённых Telegram-аккаунтов пока нет.</p>;

  async function transition(accountId: string, action: "pause" | "resume" | "archive") {
    setPendingAccountId(accountId);
    setControlError(null);
    try {
      const updated = await transitionTelegramAccount(accountId, action);
      setState((current) => current.kind === "ready"
        ? { kind: "ready", accounts: current.accounts.map((account) => account.account_id === accountId ? updated : account) }
        : current);
    } catch {
      setControlError("Не удалось изменить состояние аккаунта. Попробуйте ещё раз.");
    } finally {
      setPendingAccountId(null);
    }
  }

  return (
    <section className="telegram-account-list" aria-label="Подключённые Telegram-аккаунты">
      {state.accounts.map((account) => (
        <article className="telegram-account-row" key={account.account_id}>
          <div>
            <h2>{accountLabel(account.account_id)}</h2>
            <p className="muted">Состояние подключения отображается без номера телефона, сессии и других секретов.</p>
          </div>
          <span className={`connection-state connection-state-${account.state}`}>
            {stateLabels[account.state] ?? "Неизвестно"}
          </span>
          {canManage && account.state !== "paused" && account.state !== "archived" && (
            <button
              className="secondary"
              disabled={pendingAccountId === account.account_id}
              onClick={() => void transition(account.account_id, "pause")}
              type="button"
            >
              Приостановить
            </button>
          )}
          {canManage && account.state === "paused" && (
            <button
              className="secondary"
              disabled={pendingAccountId === account.account_id}
              onClick={() => void transition(account.account_id, "resume")}
              type="button"
            >
              Возобновить
            </button>
          )}
          {canManage && account.state !== "archived" && (
            <button
              className="secondary account-archive-action"
              disabled={pendingAccountId === account.account_id}
              onClick={() => setArchiveCandidate(account)}
              type="button"
            >
              Архивировать
            </button>
          )}
        </article>
      ))}
      {controlError && <p className="form-error" role="alert">{controlError}</p>}
      {archiveCandidate && (
        <section aria-modal="true" className="account-archive-confirmation" role="alertdialog">
          <h2>Архивировать {accountLabel(archiveCandidate.account_id)}?</h2>
          <p>Аккаунт не будет запускаться автоматически. Вернуть его в работу из этого раздела нельзя.</p>
          <div className="connection-actions">
            <button className="secondary" onClick={() => setArchiveCandidate(null)} type="button">Отмена</button>
            <button
              className="account-archive-action"
              disabled={pendingAccountId === archiveCandidate.account_id}
              onClick={() => {
                const accountId = archiveCandidate.account_id;
                setArchiveCandidate(null);
                void transition(accountId, "archive");
              }}
              type="button"
            >
              Подтвердить архивирование
            </button>
          </div>
        </section>
      )}
    </section>
  );
}
