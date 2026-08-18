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

  function toggleVerificationMethod() {
    setIsRecoveryCode((current) => !current);
    setVerification("");
  }

  return (
    <main className="login-layout">
      <section className="login-card" aria-labelledby="login-title">
        <header className="login-brand">
          <span className="login-monogram" data-testid="login-monogram" aria-hidden="true">
            <svg viewBox="0 0 40 40" focusable="false">
              <path d="M20 6 33 32h-6.2l-2.5-5.2H15.7L13.2 32H7L20 6Zm0 12.5-2.1 4.3h4.2L20 18.5Z" />
            </svg>
          </span>
          <h1 id="login-title">AIsales</h1>
          <p className="login-subtitle">Рабочее пространство продаж</p>
        </header>
        <form onSubmit={submit}>
          <label>
            Электронная почта
            <input autoComplete="email" type="email" value={email} onChange={(event) => setEmail(event.target.value)} required />
          </label>
          <label>
            Пароль
            <input autoComplete="current-password" type="password" value={password} onChange={(event) => setPassword(event.target.value)} required />
          </label>
          <label>
            {isRecoveryCode ? "Код восстановления" : "Google Authenticator"}
            <input
              autoComplete={isRecoveryCode ? "off" : "one-time-code"}
              inputMode={isRecoveryCode ? "text" : "numeric"}
              value={verification}
              onChange={(event) => setVerification(event.target.value)}
            />
          </label>
          <button className="verification-toggle" type="button" onClick={toggleVerificationMethod}>
            {isRecoveryCode ? "Вернуться к Google Authenticator" : "Использовать код восстановления"}
          </button>
          {error && <p role="alert" className="error">{error}</p>}
          <button type="submit" disabled={isSubmitting}>{isSubmitting ? "Проверяем…" : "Войти"}</button>
        </form>
        <p className="login-security-note">Защищённый вход</p>
      </section>
    </main>
  );
}
