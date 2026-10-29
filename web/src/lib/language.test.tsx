import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { useLanguage } from "./language";
import { t } from "./ui";
import { useLang } from "@/features/pm/i18n";
import { formatCount } from "./format";

function LanguageProbe() {
  const { lang, setLang } = useLanguage();
  return <><button onClick={() => setLang("de")}>DE</button><button onClick={() => setLang("en")}>EN</button><span data-testid="language">{lang}</span><h1>{t("Projects")}</h1><p>{t("First prompt · excerpt")}</p><output>{formatCount(1234)}</output><input aria-label="Draft" defaultValue="My unfinished note" /><pre>Answer: Original conversation content</pre></>;
}
function TeamProbe() {
  const { t: copy } = useLang();
  return <h2>{copy.common.projectManagement}</h2>;
}
afterEach(() => { fireEvent.click(screen.getByText("EN")); });
it("switches project and team copy together, keeps drafts and source content, and persists the choice", () => {
  render(<><LanguageProbe /><TeamProbe /></>);
  expect(screen.getByRole("heading", { name: "Projects" })).toBeTruthy();
  fireEvent.change(screen.getByLabelText("Draft"), { target: { value: "Keep this work" } });
  fireEvent.click(screen.getByText("DE"));
  expect(screen.getByRole("heading", { name: "Projekte" })).toBeTruthy();
  expect(screen.getByRole("heading", { name: "KI-Teamsteuerung" })).toBeTruthy();
  expect(screen.getByText("Erster Prompt · Auszug")).toBeTruthy();
  expect(screen.getByText("1.234")).toBeTruthy();
  expect(localStorage.getItem("pm-lang")).toBe("de");
  expect(t("Scope: {selected} selected sessions of {total} in the current folder scope. Folder: {folder}.", { selected: 1, total: 2, folder: "Original {source}" })).toContain("Ordner: Original {source}.");
  expect((screen.getByLabelText("Draft") as HTMLInputElement).value).toBe("Keep this work");
  expect(screen.getByText("Answer: Original conversation content")).toBeTruthy();
  fireEvent.click(screen.getByText("EN"));
  expect(screen.getByRole("heading", { name: "AI team operations" })).toBeTruthy();
  expect(screen.getByText("1,234")).toBeTruthy();
  expect(t(" Unknown technical term ")).toBe(" Unknown technical term ");
});
