export type Density = "comfortable" | "compact";

const STORAGE_KEY = "throughline-density";

export function readDensity(): Density {
  try {
    return localStorage.getItem(STORAGE_KEY) === "compact" ? "compact" : "comfortable";
  } catch {
    return "comfortable";
  }
}

export function saveDensity(density: Density) {
  try {
    localStorage.setItem(STORAGE_KEY, density);
  } catch {
    // Private browsing can deny storage. The current-session preference still applies.
  }
}
