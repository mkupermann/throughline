import { getLang } from "./language";
import de from "./ui.de.json";

/** English source copy is the dictionary key. Preserve surrounding JSX spaces
 * so translated labels do not run into adjacent counts or inline links.
 * Translate only application copy; never pass imported content here.
 */
function translate(text: string): string {
  if (getLang() === "en") return text;
  const key = text.trim();
  const translated = (de as Record<string, string>)[key];
  if (translated === undefined) return text;
  const leading = text.length - text.trimStart().length;
  return text.slice(0, leading) + translated + text.slice(leading + key.length);
}

/** Interpolate values after translation so source names and content stay intact. */
export function t(text: string, values: Record<string, string | number> = {}): string {
  return translate(text).replace(/\{(\w+)\}/g, (placeholder, key: string) =>
    Object.prototype.hasOwnProperty.call(values, key) ? String(values[key]) : placeholder,
  );
}
