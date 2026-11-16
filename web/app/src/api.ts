// Every request the app makes to Hub. Same origin only; Hub refuses a change from anywhere else.
import type { Activity, ArchiveFound, ArchiveLanding, Changes, Down, Engine, House, MailItem, MailSettings, Me, Opened, Photos, Pick, Releases,
  Said } from "./types";

export async function get<T>(path: string): Promise<T> {
  const answer = await fetch("/app/api/" + path, { headers: { accept: "application/json" } });
  if (!answer.ok) throw new Error(String(answer.status));
  return (await answer.json()) as T;
}

export async function post(path: string, body: Record<string, unknown> = {}): Promise<Said> {
  try {
    const answer = await fetch("/app/api/" + path, {
      method: "POST",
      headers: { "content-type": "application/json", accept: "application/json" },
      body: JSON.stringify(body),
    });
    return (await answer.json()) as Said;
  } catch {
    return { error: "Can’t reach Hub. Nothing was changed." };
  }
}

export const home = () => get<(House | Down) & Me>("home");
export const picks = () => get<{ picks: Pick[] }>("picks");
export const versions = () => get<Releases>("versions");
export const engine = () => get<Engine>("engine");
export const changes = () => get<Changes>("changes");
export const activity = (kinds: string[], sources: string[], before?: number) =>
  get<Activity>(`activity?kinds=${kinds.join(",")}&sources=${sources.join(",")}${before ? "&before=" + before : ""}`);
export const mailSettings = () => get<MailSettings>("mail-settings");
export const photos = (month = "") => get<Photos>("photos" + (month ? "?month=" + encodeURIComponent(month) : ""));
export const archive = () => get<ArchiveLanding>("archive");
/** A read sent as POST, so what is searched for stays out of Hub's request log. */
async function asked<T>(path: string, body: Record<string, unknown>): Promise<T> {
  const answer = await fetch("/app/api/" + path, {
    method: "POST", headers: { "content-type": "application/json", accept: "application/json" }, body: JSON.stringify(body),
  });
  if (!answer.ok) throw new Error(String(answer.status));
  return (await answer.json()) as T;
}
export const archiveSearch = (q: string, source = "", offset = 0) => asked<ArchiveFound>("archive/search", { q, source, offset });
export const archiveMail = (q: string) => asked<{ mail: MailItem[] }>("archive/mail", { q });
export const archiveItem = (id: string) => get<Opened>(`archive/item/${encodeURIComponent(id)}`);
export const archiveAttachment = (id: string, part: string) =>
  get<{ name: string; text: string; cut: boolean }>(`archive/attachment/${encodeURIComponent(id)}/${encodeURIComponent(part)}`);
