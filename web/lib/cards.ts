// Fantasy-card types. The catalog itself lives in the DB (seeded by migration) and is
// fetched via GET /api/cards — it is deliberately NOT hard-coded here.

export type CardRarity = "common" | "uncommon" | "rare" | "epic";

export interface Card {
  id: string;
  name: string;
  icon: string;
  rarity: string;
  effect: string;
  status: "live" | "soon" | string;
  description: string;
}

// per-rarity accent colour for the card front
export const RARITY_COLOR: Record<string, string> = {
  common: "#7c8698",
  uncommon: "#3fa66a",
  rare: "#4a90d9",
  epic: "#b06bd6",
};

export function rarityColor(r: string): string {
  return RARITY_COLOR[r] ?? "#7c8698";
}
