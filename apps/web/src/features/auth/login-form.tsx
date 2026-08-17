import { FormEvent, useState } from "react";

import { ApiError, login } from "../../shared/api/client";

type LoginFormProps = {
  onAuthenticated: () => Promise<void>;
};

export function LoginForm({ onAuthenticated }: LoginFormProps) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [verification, setVerification] = useState("");
  const [isRecoveryCode, setIsRecoveryCode] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setIsSubmitting(true);
    try {
      await login({
        email,
        password,
        ...(verification && (isRecoveryCode ? { recovery_code: verification } : { totp_code: verification })),
      });
      await onAuthenticated();
    } catch (caught) {
      setError(caught instanceof ApiError && caught.status === 401 ? "Проверьте данные входа и второй фактор." : "Сервер временно недоступен. Попробуйте ещё раз.");
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <main className="login-layout">
      <section className="login-card" aria-labelledby="login-title">
        <p className="eyebrow">AIsales</p>
        <h1 id="login-title">Вход в AIsales</h1>
        <p className="muted">Рабочее пространство продаж. Доступ подтверждается серверной сессией.</p>
        <form onSubmit={submit}>
          <label>
            Рабочий email
            <input autoComplete="email" type="email" value={email} onChange={(event) => setEmail(event.target.value)} required />
          </label>
          <label>
            Пароль
            <input autoComplete="current-password" type="password" value={password} onChange={(event) => setPassword(event.target.value)} required />
          </label>
          <label>
            {isRecoveryCode ? "Recovery-код" : "Код приложения-аутентификатора"}
            <input autoComplete="one-time-code" inputMode="numeric" value={verification} onChange={(event) => setVerification(event.target.value)} />
          </label>
          <label className="inline-control">
            <input type="checkbox" checked={isRecoveryCode} onChange={(event) => setIsRecoveryCode(event.target.checked)} />
            Использовать recovery-код
          </label>
          {error && <p role="alert" className="error">{error}</p>}
          <button type="submit" disabled={isSubmitting}>{isSubmitting ? "Проверяем…" : "Войти"}</button>
        </form>
      </section>
    </main>
  );
}
