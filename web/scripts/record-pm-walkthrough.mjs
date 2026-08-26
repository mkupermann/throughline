#!/usr/bin/env node
/**
 * Record the Project Management walkthrough video.
 *
 * Drives the PM surface step by step against a server running on the DEMO
 * database (scripts/seed_demo_data.py) and records one continuous take with
 * a caption bar narrating each step. It must never point at the live
 * server: everything that ends up on screen has to come from the fictional
 * demo fixture, which is why the default URL is the demo port 8791 and
 * why the script refuses to run until the page titled with the demo
 * project names actually renders.
 *
 *   node scripts/record-pm-walkthrough.mjs [--url http://127.0.0.1:8791]
 *
 * Outputs (repo-relative):
 *   docs/assets/pm-walkthrough.webm   raw Playwright recording
 *   docs/assets/pm-walkthrough.mp4    libx264 / yuv420p, crf 23
 *   docs/assets/pm-walkthrough.gif    fps=10 scale=960 (fps=8/720 if >10MB)
 */
import { chromium } from "playwright";
import { spawnSync } from "node:child_process";
import { copyFile, mkdir, readdir, stat } from "node:fs/promises";
import { existsSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const args = process.argv.slice(2);
const argOf = (flag, fallback) => {
  const i = args.indexOf(flag);
  return i === -1 ? fallback : args[i + 1];
};

const BASE = argOf("--url", "http://127.0.0.1:8791").replace(/\/$/, "");
const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const OUT_DIR = path.join(REPO_ROOT, "docs", "assets");
const VIEWPORT = { width: 1280, height: 800 };

// The bundled Chromium may not be installed on this machine; Edge is a
// Chromium too and ships with Windows.
const launchOptions = existsSync(chromium.executablePath())
  ? {}
  : { channel: "msedge" };

/** Fixed bottom caption bar, injected into the live page. Re-applied after
 *  every full navigation (the SPA keeps it across client-side route
 *  changes, page.goto does not). */
const caption = async (page, text) => {
  await page.evaluate((t) => {
    let el = document.getElementById("pm-demo-caption");
    if (!el) {
      el = document.createElement("div");
      el.id = "pm-demo-caption";
      Object.assign(el.style, {
        position: "fixed",
        left: "0",
        right: "0",
        bottom: "0",
        zIndex: "99999",
        padding: "14px 28px",
        background: "rgba(9, 11, 16, 0.84)",
        backdropFilter: "blur(6px)",
        color: "#ffffff",
        font: "500 18px/1.35 system-ui, 'Segoe UI', sans-serif",
        textAlign: "center",
        letterSpacing: "0.01em",
        pointerEvents: "none",
      });
      document.body.appendChild(el);
    }
    el.textContent = t;
  }, text);
};

const hold = (page, ms) => page.waitForTimeout(ms);

const smoothScroll = async (page, y) => {
  await page.evaluate((top) => window.scrollTo({ top, behavior: "smooth" }), y);
  await hold(page, 900);
};

const scrollToText = async (page, text) => {
  await page
    .getByText(text, { exact: false })
    .first()
    .scrollIntoViewIfNeeded()
    .catch(() => {});
  await hold(page, 700);
};

// ── ffmpeg ──────────────────────────────────────────────────────────────────

const findFfmpeg = async () => {
  if (spawnSync("ffmpeg", ["-version"]).status === 0) return "ffmpeg";
  const pkgRoot = path.join(
    process.env.LOCALAPPDATA ?? path.join(os.homedir(), "AppData", "Local"),
    "Microsoft",
    "WinGet",
    "Packages",
  );
  try {
    for (const entry of await readdir(pkgRoot)) {
      if (!entry.startsWith("Gyan.FFmpeg")) continue;
      const pkg = path.join(pkgRoot, entry);
      for (const sub of await readdir(pkg)) {
        const candidate = path.join(pkg, sub, "bin", "ffmpeg.exe");
        if (existsSync(candidate)) return candidate;
      }
    }
  } catch {
    /* fall through */
  }
  throw new Error("ffmpeg not found on PATH or under the WinGet packages dir");
};

const run = (bin, argv) => {
  const res = spawnSync(bin, argv, { stdio: ["ignore", "inherit", "inherit"] });
  if (res.status !== 0) throw new Error(`${bin} ${argv.join(" ")} exited ${res.status}`);
};

const makeGif = async (ffmpeg, webm, gifPath, fps, width) => {
  const palette = path.join(os.tmpdir(), "pm-walkthrough-palette.png");
  const filters = `fps=${fps},scale=${width}:-1:flags=lanczos`;
  run(ffmpeg, ["-y", "-i", webm, "-vf", `${filters},palettegen`, palette]);
  run(ffmpeg, [
    "-y", "-i", webm, "-i", palette,
    "-lavfi", `${filters}[x];[x][1:v]paletteuse`,
    gifPath,
  ]);
  return (await stat(gifPath)).size;
};

// ── The walkthrough ─────────────────────────────────────────────────────────

const record = async () => {
  await mkdir(OUT_DIR, { recursive: true });
  const videoDir = path.join(os.tmpdir(), "pm-walkthrough-video");
  await mkdir(videoDir, { recursive: true });

  const browser = await chromium.launch(launchOptions);
  const context = await browser.newContext({
    viewport: VIEWPORT,
    colorScheme: "dark",
    recordVideo: { dir: videoDir, size: VIEWPORT },
  });
  // Force the app's own dark theme and the English PM strings before the
  // first render — colorScheme alone only covers the "system" case.
  await context.addInitScript(() => {
    try {
      localStorage.setItem("throughline-theme", "dark");
      localStorage.setItem("pm-lang", "en");
    } catch {}
  });

  const page = await context.newPage();
  const goto = async (route, waitForText) => {
    await page.goto(`${BASE}${route}`, { waitUntil: "networkidle", timeout: 30_000 });
    await page.getByText(waitForText, { exact: false }).first().waitFor({ timeout: 15_000 });
  };

  // 1/8 — dashboard.
  await goto("/pm", "Acme Storefront Relaunch");
  await caption(page, "1/8  Project Management — virtual AI teams, live");
  await hold(page, 5000);

  // 2/8 — the project cards.
  await caption(page, "2/8  Every project: teams, tasks, budget at a glance");
  await page.locator(".pm-card").first().hover().catch(() => {});
  await hold(page, 5000);

  // 3/8 — repository projects (adopt flow).
  await caption(page, "3/8  Adopt any repository project you already work in");
  await scrollToText(page, "Repository projects");
  await hold(page, 4500);

  // 4/8 — the cockpit's team pipeline.
  await goto("/pm/projects/1", "Delivery Squad");
  await caption(page, "4/8  The team pipeline: Analyst → Executor → Tester");
  await scrollToText(page, "Delivery Squad");
  await hold(page, 5500);

  // 5/8 — roles, opening the Executor editor.
  await goto("/pm/roles", "Executor");
  await caption(page, "5/8  Every role: model, skills, prompt, budget");
  const executorRow = page.locator(".pm-cat-row", { hasText: "Executor" }).first();
  await executorRow.getByRole("button", { name: "Edit" }).click();
  await hold(page, 1200);
  await executorRow.scrollIntoViewIfNeeded().catch(() => {});
  await hold(page, 4800);
  await executorRow.getByRole("button", { name: "Close" }).click();
  await hold(page, 800);

  // 6/8 — AI providers.
  await goto("/pm/models", "AI models");
  await caption(page, "6/8  Bring your own AI providers");
  await hold(page, 5000);

  // 7/8 — task drill-down of the passed task, one log expanded.
  await goto("/pm/tasks/1", "Iterations");
  await caption(page, "7/8  Watch a run: iterations, verdicts, logs");
  await hold(page, 2500);
  const firstToggle = page.locator(".pm-iter-logtoggle").first();
  await firstToggle.scrollIntoViewIfNeeded().catch(() => {});
  await firstToggle.click();
  await hold(page, 1500);
  await page.locator(".pm-log-pre").first().scrollIntoViewIfNeeded().catch(() => {});
  await hold(page, 4500);

  // 8/8 — back to the cockpit. Hover and scroll only; never stop/delete/launch.
  await goto("/pm/projects/1", "Delivery Squad");
  await caption(page, "8/8  Stop, archive, or start the next task");
  await scrollToText(page, "Tasks");
  await page.locator(".pm-task-row").first().hover().catch(() => {});
  await hold(page, 3000);
  await page.locator(".pm-task-row").nth(1).hover().catch(() => {});
  await hold(page, 3000);

  // Closing the context flushes the .webm to disk.
  const video = page.video();
  await context.close();
  await browser.close();
  return video.path();
};

const main = async () => {
  const rawPath = await record();
  const webm = path.join(OUT_DIR, "pm-walkthrough.webm");
  await copyFile(rawPath, webm);
  console.log(`webm: ${webm}`);

  const ffmpeg = await findFfmpeg();

  const mp4 = path.join(OUT_DIR, "pm-walkthrough.mp4");
  run(ffmpeg, [
    "-y", "-i", webm,
    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "23",
    "-movflags", "+faststart",
    mp4,
  ]);
  console.log(`mp4:  ${mp4} (${((await stat(mp4)).size / 1e6).toFixed(1)} MB)`);

  const gif = path.join(OUT_DIR, "pm-walkthrough.gif");
  let size = await makeGif(ffmpeg, webm, gif, 10, 960);
  if (size > 10 * 1024 * 1024) {
    console.log(`gif is ${(size / 1e6).toFixed(1)} MB — retrying at fps=8, 720px`);
    size = await makeGif(ffmpeg, webm, gif, 8, 720);
  }
  console.log(`gif:  ${gif} (${(size / 1e6).toFixed(1)} MB)`);
};

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
