import { useEffect, useState } from "react";
import { deactivateWorkspaceMember, listWorkspaceMembers, WorkspaceMember } from "../../shared/api/client";

export function StaffMembersList({ canManage }: { canManage: boolean }) {
  const [members, setMembers] = useState<WorkspaceMember[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [actionFailed, setActionFailed] = useState(false);
  useEffect(() => {
    if (!canManage) return;
    void listWorkspaceMembers()
      .then((items) => {
        if (!Array.isArray(items)) throw new Error("workspace member directory was malformed");
        setMembers(items);
      })
      .catch(() => setFailed(true));
  }, [canManage]);
  if (!canManage) return <p className="empty-state">У вас нет прав на просмотр состава команды.</p>;
  if (failed) return <p className="empty-state">Не удалось загрузить команду. Обновите страницу.</p>;
  if (!members) return <p className="muted">Загружаем команду…</p>;
  if (!members.length) return <p className="empty-state">Сотрудников пока нет.</p>;
  async function deactivate(member: WorkspaceMember) {
    if (!window.confirm(`Отключить доступ для ${member.email}?`)) return;
    setActionFailed(false);
    try {
      const saved = await deactivateWorkspaceMember(member.user_id);
      setMembers((current) => current?.map((item) => item.user_id === saved.user_id ? saved : item) ?? []);
    } catch {
      setActionFailed(true);
    }
  }
  return <section className="staff-members" aria-label="Сотрудники">{members.map((member) => <article key={member.user_id}><div><strong>{member.email}</strong><p className="muted">{member.role === "administrator" ? "Администратор" : "Менеджер"} · {member.is_active ? "Активен" : "Отключён"}</p></div>{member.is_active && <button className="secondary" onClick={() => void deactivate(member)} type="button">Отключить доступ</button>}</article>)}{actionFailed && <p className="form-error" role="alert">Не удалось отключить доступ. Попробуйте ещё раз.</p>}</section>;
}
