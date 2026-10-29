import { readFileSync } from "node:fs";
import path from "node:path";

import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";

const stylesRoot = path.resolve(process.cwd(), "src", "styles");
const detailCss = readFileSync(path.join(stylesRoot, "detail.css"), "utf8");
const findCss = readFileSync(path.join(stylesRoot, "find.css"), "utf8");
const overviewCss = readFileSync(path.join(stylesRoot, "overview.css"), "utf8");

let stylesheet;

beforeAll(() => {
  stylesheet = document.createElement("style");
  stylesheet.textContent = [detailCss, findCss, overviewCss].join("\n");
  document.head.append(stylesheet);
});

afterAll(() => {
  stylesheet.remove();
});

afterEach(() => {
  document.body.replaceChildren();
});

function elementFrom(markup, selector) {
  document.body.innerHTML = markup;
  const element = document.querySelector(selector);
  if (!element) throw new Error(`Fixture did not contain ${selector}`);
  return element;
}

function matchingDeclarations(element) {
  const declarations = [];
  for (const sheet of Array.from(document.styleSheets)) {
    for (const rule of Array.from(sheet.cssRules)) {
      if (rule.type !== 1) continue;
      if (element.matches(rule.selectorText)) declarations.push(rule.style);
    }
  }
  return declarations;
}

function sideRailDeclarations(element) {
  const railProperties = [
    "border-left",
    "border-left-width",
    "border-left-color",
    "border-inline-start",
    "border-inline-start-width",
    "border-inline-start-color",
  ];
  return matchingDeclarations(element).flatMap((style) =>
    railProperties.filter((property) => style.getPropertyValue(property) !== ""),
  );
}

describe("card and tile edges", () => {
  it.each([
    ["attention item", '<li class="attn attn-critical"></li>', ".attn"],
    ["disclosure", '<div class="disclosure"></div>', ".disclosure"],
    ["record content", '<article class="detail-content is-memory"></article>', ".detail-content"],
    ["prompt body", '<div class="detail-promptbody"><pre></pre></div>', ".detail-promptbody pre"],
    ["transcript message", '<li class="tx-msg tx-user"></li>', ".tx-msg"],
    ["target message", '<li class="tx-msg tx-user is-target"></li>', ".tx-msg"],
  ])("does not add a left rail to %s", (_label, markup, selector) => {
    const element = elementFrom(markup, selector);

    expect(sideRailDeclarations(element)).toEqual([]);
  });

  it("selects a result without an inset edge rail", () => {
    const element = elementFrom('<li class="result is-selected"></li>', ".result");
    const shadows = matchingDeclarations(element).map((style) => style.getPropertyValue("box-shadow"));

    expect(shadows.join(" ")).not.toMatch(/inset/i);
  });
});
