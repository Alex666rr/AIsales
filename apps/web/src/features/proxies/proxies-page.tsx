import { type FormEvent, useEffect, useState } from "react";

import { createTelegramProxy, listTelegramProxies, TelegramProxyStatus } from "../../shared/api/client";

type PageState =
  | { kind: "loading" }
  | { kind: "ready"; proxies: TelegramProxyStatus[] }
  | { kind: "unavailable" };

const healthLabel: Record<TelegramProxyStatus["health"], string> = {
  awaiting_check: "Ожидает проверки",
  healthy: "Доступен",
  degraded: "Требует внимания",
};

export function ProxiesPage() {
  const [state, setState] = useState<PageState>({ kind: "loading" });
  const [url, setUrl] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let current = true;
    void listTelegramProxies()
      .then((proxies) => {
        if (!Array.isArray(proxies)) throw new Error("proxy directory was malformed");
        if (current) setState({ kind: "ready", proxies });
      })
      .catch(() => { if (current) setState({ kind: "unavailable" }); });
    return () => { current = false; };
  }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true); setError(null);
    try {
      const created = await createTelegramProxy({ url, capacity: 1, default: false });
      setState((current) => current.kind === "ready" ? { kind: "ready", proxies: [...current.proxies, created] } : { kind: "ready", proxies: [created] });
      setUrl("");
    } catch { setError("Не удалось добавить прокси. Проверьте адрес и попробуйте снова."); }
    finally { setSubmitting(false); }
  }

  const content = state.kind === "loading" ? <p className="muted">Загружаем прокси…</p>
    : state.kind === "unavailable" ? <p className="empty-state">Не удалось загрузить прокси. Обновите страницу.</p>
    : state.proxies.length === 0 ? <p className="empty-state">Прокси пока не добавлены. Подключения продолжат работать без них.</p>
    : <section className="proxy-list" aria-label="Прокси Telegram">
        {state.proxies.map((proxy) => (
          <article className="proxy-row" key={proxy.proxy_id}>
            <div><h2>{proxy.endpoint}</h2><p className="muted">{proxy.protocol.toUpperCase()} · назначено аккаунтов: {proxy.assignment_count} из {proxy.capacity}</p></div>
            <span className={`connection-state proxy-health-${proxy.health}`}>{healthLabel[proxy.health]}</span>
            {proxy.is_default && <span className="status-chip">По умолчанию</span>}
          </article>
        ))}
      </section>;

  return (
    <>
      {content}
      <form className="proxy-form" onSubmit={(event) => void submit(event)}>
        <label>Адрес прокси<input autoComplete="off" onChange={(event) => setUrl(event.target.value)} placeholder="socks5://login:password@host:port" required type="text" value={url} /></label>
        <p className="muted">Логин и пароль, если они есть в адресе, шифруются и не отображаются в интерфейсе.</p>
        {error && <p className="form-error" role="alert">{error}</p>}
        <button disabled={submitting} type="submit">Добавить прокси</button>
      </form>
    </>
  );
}
