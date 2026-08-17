import { FormEvent, useState } from "react";

import { ApiError, createStaffInvitation, StaffInvitation } from "../../shared/api/client";

export function StaffInvitationForm() {
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<"administrator" | "manager">("manager");
  const [invitation, setInvitation] = useState<StaffInvitation | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setInvitation(null);
    setSubmitting(true);
    try {
      setInvitation(await createStaffInvitation({ email, role }));
    } catch (caught) {
      setError(caught instanceof ApiError && caught.status === 400
        ? "Этот email уже используется или данные приглашения неверны."
        : "Не удалось создать приглашение. Повторите попытку позже.");
    } finally {
      setSubmitting(false);
    }
  }

  const setupLink = invitation
    ? `${window.location.origin}/setup?token=${encodeURIComponent(invitation.setup_token)}`
    : null;

  return (
    <article className="context-card" id="users">
      <h2>Пригласить сотрудника</h2>
      <p className="muted">Создайте одноразовую ссылку и передайте её сотруднику через защищённый канал.</p>
      <form onSubmit={submit}>
        <label>
          Email сотрудника
          <input autoComplete="email" type="email" value={email} onChange={(event) => setEmail(event.target.value)} required />
        </label>
        <label>
          Роль
          <select value={role} onChange={(event) => setRole(event.target.value as "administrator" | "manager")}> 
            <option value="manager">Менеджер</option>
            <option value="administrator">Администратор</option>
          </select>
        </label>
        {error && <p role="alert" className="error">{error}</p>}
        <button type="submit" disabled={submitting}>{submitting ? "Создаём…" : "Создать приглашение"}</button>
      </form>
      {setupLink && (
        <section className="setup-link" aria-live="polite">
          <h3>Одноразовая ссылка для сотрудника</h3>
          <p className="muted">Она действует 48 часов и отображается только сейчас.</p>
          <input aria-label="Одноразовая ссылка для сотрудника" readOnly value={setupLink} />
        </section>
      )}
    </article>
  );
}
