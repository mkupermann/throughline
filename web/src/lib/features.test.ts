import { afterEach, describe, expect, it } from "vitest";

import { clearFeatures, features, setFeatures } from "./features";
import { NAV, NAV_GROUPS, visibleNav, visibleNavGroups } from "./nav";

afterEach(() => {
  clearFeatures();
});

describe("features", () => {
  it("defaults to PM on when the server did not inject meta (dev and tests)", () => {
    clearFeatures();
    expect(features().pm).toBe(true);
  });

  it("honours injected meta over the default", () => {
    setFeatures({ pm: false });
    expect(features().pm).toBe(false);
    setFeatures({ pm: true });
    expect(features().pm).toBe(true);
  });
});

describe("visibleNav", () => {
  it("shows every surface, including AI team operations, when PM is on", () => {
    setFeatures({ pm: true });
    expect(visibleNav().map((item) => item.to)).toEqual(NAV.map((item) => item.to));
    expect(visibleNav().some((item) => item.to.startsWith("/pm"))).toBe(true);
  });

  it("hides every /pm surface when PM is fenced off", () => {
    setFeatures({ pm: false });
    const items = visibleNav();
    expect(items.some((item) => item.to.startsWith("/pm"))).toBe(false);
    // Everything that is not PM stays reachable.
    expect(items.map((item) => item.to)).toEqual(
      NAV.filter((item) => !item.to.startsWith("/pm")).map((item) => item.to),
    );
  });

  it("keeps all groups when PM is off, because PM shares the System group", () => {
    setFeatures({ pm: false });
    // AI team operations sits in System alongside surfaces that are always
    // present, so fencing it off empties no group heading.
    for (const group of NAV_GROUPS) {
      expect(visibleNavGroups()).toContain(group);
    }
    expect(visibleNavGroups().length).toBe(NAV_GROUPS.length);
  });
});
