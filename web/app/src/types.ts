// The house as Hub sends it (hub/app/model.py). Names are data to show, never instructions.

export interface Light { id: string; name: string; on: boolean; level: number; dims: boolean; offline: boolean }
export interface Other { id: string; name: string; kind: string; on: boolean; offline: boolean }
export interface Room {
  id: string; name: string; on: boolean; lit: number; level: number; dims: boolean;
  lights: Light[]; others: Other[]; speaker: string | null; sounding: boolean;
}
export interface Speaker { id: string; name: string; in: boolean; leads: boolean; volume: number | null; offline: boolean }
export interface Music {
  speaker: string; playing: boolean; state: string; title: string; artist: string; album: string;
  volume: number | null; speakers: Speaker[];
}
export interface Fact { label: string; value: string; tone: string }
export interface Floor { name: string; rooms: string[] }
export interface Scene { id: string; name: string; on: boolean; mine: boolean }
export interface House {
  ok: true; at: string; floors: Floor[]; rooms: Record<string, Room>; scenes: Scene[];
  music: Music | null; facts: Fact[]; lights_on: number;
}
export interface Down { ok: false; why: string }
export interface Me {
  signed_in: boolean; initial: string; versions: { js: string; css: string };
  links: { ha: string | null; music: string | null };
}
export interface Pick { name: string; kind: string; uri: string }
export interface Said { ok?: boolean; said?: string; batch?: string | null; error?: string }
