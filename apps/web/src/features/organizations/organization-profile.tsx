import { FormEvent, useEffect, useState } from "react";
import { getOrganizationProfile, OrganizationProfile, renameOrganization } from "../../shared/api/client";

export function OrganizationProfileCard({ canManage }: { canManage: boolean }) {
  const [profile, setProfile] = useState<OrganizationProfile | null>(null);
  const [name, setName] = useState("");
  const [failed, setFailed] = useState(false);
  const [saveFailed, setSaveFailed] = useState(false);
  useEffect(() => {
    void getOrganizationProfile()
      .then((item) => {
        if (typeof item?.name !== "string" || !item.name.trim()) throw new Error("organization profile was malformed");
        setProfile(item);
        setName(item.name);
      })
      .catch(() => setFailed(true));
  }, []);
  if (failed) return <p className="empty-state">Не удалось загрузить данные организации. Обновите страницу.</p>;
  if (!profile) return <p className="muted">Загружаем организацию…</p>;
  async function submit(event: FormEvent) {
    event.preventDefault();
    setSaveFailed(false);
    try {
      const saved = await renameOrganization(name.trim());
      setProfile(saved);
      setName(saved.name);
    } catch {
      setSaveFailed(true);
    }
  }
  return <section className="organization-profile" aria-label="Профиль организации"><h2>{profile.name}</h2><p className="muted">Рабочее пространство продаж.</p>{canManage && <form onSubmit={(event) => void submit(event)}><label>Название организации<input aria-label="Название организации" value={name} onChange={(event) => setName(event.target.value)} required maxLength={256} /></label><button type="submit">Сохранить название</button>{saveFailed && <p className="form-error" role="alert">Не удалось сохранить название. Попробуйте ещё раз.</p>}</form>}</section>;
}
