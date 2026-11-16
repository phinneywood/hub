// Every request the app makes to Hub. Same origin only; Hub refuses a change from anywhere else.
import type { Activity, Care, Changes, Down, Engine, Health, House, MailSettings, Me, Pick, Releases, Said } from "./types";

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
export const health = () => get<Health>("health");
export const careTop = () => get<Care>("health/top");
export const healthAsk = (what: string, target = "") => post("health/" + what, { target });
