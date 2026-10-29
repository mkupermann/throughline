import { useCallback, useEffect, useState } from "react";
export type Lang = "de" | "en";
const STORAGE_KEY = "pm-lang"; // Retain existing preferences across the upgrade.
// ── Language state ──────────────────────────────────────────────────────
// Module-level, with a tiny pub/sub: every component using useLang() re-
// renders when the language changes anywhere, without a context provider.

function readStored(): Lang {
  try {
    const v = localStorage.getItem(STORAGE_KEY);
    if (v === "de" || v === "en") return v;
  } catch {
    // localStorage unavailable (private mode, disabled storage, …) — fall
    // back to the default silently.
  }
  return "en";
}

let currentLang: Lang | null = null;
const listeners = new Set<(l: Lang) => void>();

export function getLang(): Lang {
  if (currentLang === null) currentLang = readStored();
  return currentLang;
}

function setGlobalLang(l: Lang) {
  currentLang = l;
  document.documentElement.lang = l;
  try {
    localStorage.setItem(STORAGE_KEY, l);
  } catch {
    // Best effort — the in-memory state still switches for this session.
  }
  listeners.forEach((fn) => fn(l));
}



export function useLanguage() {
  const [lang, setLangState] = useState<Lang>(getLang);

  useEffect(() => {
    const listener = (l: Lang) => setLangState(l);
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  }, []);

  const setLang = useCallback((l: Lang) => setGlobalLang(l), []);
  const toggle = useCallback(() => setGlobalLang(lang === "de" ? "en" : "de"), [lang]);

  return { lang, setLang, toggle };
}
