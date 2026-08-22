import { useEffect, useState } from "react";

import { listTelegramAccounts, TelegramAccountStatus } from "../../shared/api/client";

type DirectoryState =
  | { kind: "loading" }
  | { kind: "ready"; accounts: TelegramAccountStatus[] }
  | { kind: "unavailable" };

const stateLabels: Record<string, string> = {
  active: "Активен",
  quarantine: "Карантин",
  paused: "Приостановлен",
  archived: "Архив",
};

function accountLabel(accountId: string): string {
  return `Аккаунт #${accountId.slice(0, 8)}`;
}

export function TelegramAccountsList() {
  const [state, setState] = useState<DirectoryState>({ kind: "loading" });

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
        </article>
      ))}
    </section>
  );
}
