// Dice skins. Every player rolls with their own die on the board, so the skin is part
// of how you recognise them. Keep the ids in step with DICE_SKINS in routes_profile.py.

export interface DiceSkin {
  id: string;
  name: string;
  face: string; // die body
  pip: string; // pips
  edge: string; // outline
}

export const DICE_SKINS: Record<string, DiceSkin> = {
  classic: { id: "classic", name: "Classic", face: "#ffffff", pip: "#0e1320", edge: "#c8ccd4" },
  gold: { id: "gold", name: "Gold", face: "#f5b70a", pip: "#3a2a00", edge: "#c99400" },
  night: { id: "night", name: "Night", face: "#1b2233", pip: "#eef1f6", edge: "#3a4258" },
  mint: { id: "mint", name: "Mint", face: "#30a46c", pip: "#eafff5", edge: "#1e7a4e" },
  ruby: { id: "ruby", name: "Ruby", face: "#e5484d", pip: "#fff0f0", edge: "#b8383c" },
  ocean: { id: "ocean", name: "Ocean", face: "#3e63dd", pip: "#eaf0ff", edge: "#2c48a8" },
};

export const skinOf = (id: string | undefined): DiceSkin =>
  DICE_SKINS[id ?? "classic"] ?? DICE_SKINS.classic;

// pip layout on a 3×3 grid, per face value
export const PIPS: Record<number, [number, number][]> = {
  1: [[1, 1]],
  2: [[0, 0], [2, 2]],
  3: [[0, 0], [1, 1], [2, 2]],
  4: [[0, 0], [2, 0], [0, 2], [2, 2]],
  5: [[0, 0], [2, 0], [1, 1], [0, 2], [2, 2]],
  6: [[0, 0], [2, 0], [0, 1], [2, 1], [0, 2], [2, 2]],
};
