#!/usr/bin/env node
/**
 * Record the Project Management walkthrough video.
 *
 * Drives the PM surface step by step against a server running on the DEMO
 * database (scripts/seed_demo_data.py) and records one continuous take with
 * a timed subtitle track narrating exactly what is happening on screen. It
 * must never point at the live server: everything that ends up on screen
 * has to come from the fictional demo fixture, which is why the default
 * URL is the demo port 8791 and why the script refuses to run until the
 * page titled with the demo project names actually renders.
 *
 *   node scripts/record-pm-walkthrough.mjs [--url http://127.0.0.1:8791]
 *
 * Outputs (repo-relative):
 *   docs/assets/pm-walkthrough.webm   raw Playwright recording
 *   docs/assets/pm-walkthrough.mp4    libx264 / yuv420p, crf 23
 *   docs/assets/pm-walkthrough.gif    fps=10 scale=960 (fps=8/720, then
 *                                     fps=8/640, if still >10MB)
 *   docs/assets/pm-walkthrough.srt    subtitle sidecar, timestamps scaled
 *                                     to the measured mp4 duration
 *
 * Subtitle lines are anchored to real seeded values (token spend, iteration
 * verdicts, model names, skill names) — see scripts/seed_demo_data.py and
 * .superpowers/sdd/ui-rebuild/demo-and-video.md for the fixture they read
 * from, so a change to the fixture should be reflected here too.
 */
import { chromium } from "playwright";
import { spawnSync } from "node:child_process";
import { copyFile, mkdir, readdir, stat, writeFile } from "node:fs/promises";
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

// ── Subtitle bar ─────────────────────────────────────────────────────────
//
// A fixed bottom band styled like a real subtitle track: dark translucent,
// full width, white ~19px text, up to two lines, small step indicator at
// the left edge, subtle fade-in on every text change. Re-created after
// every full navigation (the SPA keeps it across client-side route
// changes, page.goto does not).
const setSubtitle = async (page, stepLabel, text) => {
  await page.evaluate(
    ({ stepLabel, text }) => {
      let bar = document.getElementById("pm-demo-caption");
      if (!bar) {
        bar = document.createElement("div");
        bar.id = "pm-demo-caption";
        Object.assign(bar.style, {
          position: "fixed",
          left: "0",
          right: "0",
          bottom: "0",
          zIndex: "99999",
          display: "flex",
          alignItems: "flex-end",
          gap: "18px",
          padding: "12px 32px 16px",
          background: "rgba(8, 10, 15, 0.86)",
          backdropFilter: "blur(8px)",
          WebkitBackdropFilter: "blur(8px)",
          borderTop: "1px solid rgba(255, 255, 255, 0.08)",
          pointerEvents: "none",
        });

        const step = document.createElement("div");
        step.id = "pm-demo-caption-step";
        Object.assign(step.style, {
          flex: "0 0 auto",
          minWidth: "44px",
          paddingBottom: "3px",
          font: "600 13px/1 system-ui, 'Segoe UI', sans-serif",
          color: "rgba(255, 255, 255, 0.5)",
          letterSpacing: "0.03em",
        });
        bar.appendChild(step);

        const line = document.createElement("div");
        line.id = "pm-demo-caption-text";
        Object.assign(line.style, {
          flex: "1 1 auto",
          maxWidth: "920px",
          margin: "0 auto",
          color: "#ffffff",
          font: "500 19px/1.45 system-ui, 'Segoe UI', sans-serif",
          textAlign: "center",
          letterSpacing: "0.01em",
          display: "-webkit-box",
          WebkitLineClamp: "2",
          WebkitBoxOrient: "vertical",
          overflow: "hidden",
          opacity: "0",
          transition: "opacity 320ms ease",
        });
        bar.appendChild(line);

        document.body.appendChild(bar);
      }
      const stepEl = document.getElementById("pm-demo-caption-step");
      const textEl = document.getElementById("pm-demo-caption-text");
      stepEl.textContent = stepLabel;
      textEl.style.opacity = "0";
      textEl.textContent = text;
      void textEl.offsetWidth; // force reflow so the fade-in actually transitions
      requestAnimationFrame(() => {
        textEl.style.opacity = "1";
      });
    },
    { stepLabel, text },
  );
};

const hold = (page, ms) => page.waitForTimeout(ms);

const scrollToText = async (page, text) => {
  await page
    .getByText(text, { exact: false })
    .first()
    .scrollIntoViewIfNeeded()
    .catch(() => {});
  await hold(page, 500);
};

// ── Timed subtitle track ────────────────────────────────────────────────
//
// `say` sets the subtitle, optionally runs an action (a click/scroll that
// illustrates the line) while it's on screen, then waits out the rest of
// the requested duration. Every line's actual on/off timestamps (relative
// to `recordStart`, captured right after the recording context/page is
// created) are logged to `subtitleLog` so the .srt sidecar reflects what
// really happened, not the nominal schedule.
let recordStart = 0;
const subtitleLog = [];

const say = async (page, stepLabel, text, ms, action) => {
  const start = Date.now() - recordStart;
  await setSubtitle(page, stepLabel, text);
  if (action) await action();
  const spent = Date.now() - recordStart - start;
  const remaining = Math.max(300, ms - spent);
  await hold(page, remaining);
  const end = Date.now() - recordStart;
  subtitleLog.push({ text, start, end });
};

// ── ffmpeg / ffprobe ────────────────────────────────────────────────────

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

// ffprobe lives next to ffmpeg either way (PATH, or the same WinGet bin dir).
const findFfprobe = (ffmpegBin) => {
  if (ffmpegBin === "ffmpeg") return "ffprobe";
  return path.join(path.dirname(ffmpegBin), "ffprobe.exe");
};

const probeDurationSeconds = (ffprobeBin, file) => {
  const res = spawnSync(ffprobeBin, [
    "-v", "error",
    "-show_entries", "format=duration",
    "-of", "default=noprint_wrappers=1:nokey=1",
    file,
  ]);
  const out = (res.stdout ?? "").toString().trim();
  const n = Number.parseFloat(out);
  return Number.isFinite(n) ? n : null;
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

// ── SRT sidecar ─────────────────────────────────────────────────────────

const srtTimestamp = (ms) => {
  const clamped = Math.max(0, Math.round(ms));
  const h = Math.floor(clamped / 3_600_000);
  const m = Math.floor((clamped % 3_600_000) / 60_000);
  const s = Math.floor((clamped % 60_000) / 1000);
  const msRemainder = clamped % 1000;
  const pad = (n, len) => String(n).padStart(len, "0");
  return `${pad(h, 2)}:${pad(m, 2)}:${pad(s, 2)},${pad(msRemainder, 3)}`;
};

/** Build SRT text from the logged (text, start, end) entries, scaling every
 *  timestamp by `scale` so the sidecar matches the *measured* mp4 duration
 *  rather than the Node-side wall clock, which can drift slightly from the
 *  recorded video's own internal clock. */
const buildSrt = (entries, scale) =>
  entries
    .map((e, i) => {
      const startMs = e.start * scale;
      const endMs = e.end * scale;
      return `${i + 1}\n${srtTimestamp(startMs)} --> ${srtTimestamp(endMs)}\n${e.text}\n`;
    })
    .join("\n");

// ── The walkthrough ─────────────────────────────────────────────────────

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
  recordStart = Date.now();

  const goto = async (route, waitForText) => {
    await page.goto(`${BASE}${route}`, { waitUntil: "networkidle", timeout: 30_000 });
    await page.getByText(waitForText, { exact: false }).first().waitFor({ timeout: 15_000 });
  };

  // 1/8 — dashboard.
  await goto("/pm", "Acme Storefront Relaunch");
  await say(page, "1 / 8", "Project Management: virtual AI teams working real engineering tasks.", 4000);
  await say(page, "1 / 8", "Three projects total — two active, one archived.", 3800);

  // 2/8 — the project cards.
  await say(
    page,
    "2 / 8",
    "Each card shows the project's teams, task states and token spend.",
    4500,
    () => page.locator(".pm-card").first().hover().catch(() => {}),
  );
  await say(page, "2 / 8", "Acme Storefront Relaunch has burned 747K of its 5M token budget.", 4500);

  // 3/8 — repository projects (adopt flow).
  await say(
    page,
    "3 / 8",
    "Six repositories the CLI already tracks show up here too.",
    4000,
    () => scrollToText(page, "Repository projects"),
  );
  await say(page, "3 / 8", "Any of them can be adopted into a PM project in one click.", 4500);

  // 4/8 — the cockpit's team pipeline.
  await goto("/pm/projects/1", "Delivery Squad");
  await say(
    page,
    "4 / 8",
    "Acme's Delivery Squad: Analyst → Executor → Tester, plus human reviewer Sam Rivera.",
    5300,
    () => scrollToText(page, "Delivery Squad"),
  );
  await say(page, "4 / 8", "Three tasks so far: one passed, one running, one over budget.", 4300);

  // 5/8 — roles, opening the Executor editor.
  await goto("/pm/roles", "Executor");
  const executorRow = page.locator(".pm-cat-row", { hasText: "Executor" }).first();
  await say(
    page,
    "5 / 8",
    "Opening the Executor role — its AI tool, model and budget live here.",
    4800,
    async () => {
      await executorRow.getByRole("button", { name: "Edit" }).click();
      await executorRow.scrollIntoViewIfNeeded().catch(() => {});
    },
  );
  await say(page, "5 / 8", "It runs Aider against a local qwen3-coder:30b model, capped at 500K tokens.", 5000);
  await say(page, "5 / 8", "Skills are picked from the indexed catalog.", 3500);
  await say(
    page,
    "5 / 8",
    "Two are attached: db-migration-safety and pricing-money-math.",
    4200,
    async () => {
      await executorRow.getByRole("button", { name: "Close" }).click();
    },
  );

  // 6/8 — AI providers.
  await goto("/pm/models", "AI models");
  await say(page, "6 / 8", "AI providers are bring-your-own: a local Ollama and a team OpenAI key.", 4800);
  await say(page, "6 / 8", "The local Ollama endpoint serves qwen3-coder and devstral at no API cost.", 4800);

  // 7/8 — task drill-down: passed task, its iteration history and one log.
  await goto("/pm/tasks/1", "Iterations");
  await say(page, "7 / 8", "‘Add product search with typo tolerance’ passed after six iterations.", 4200);
  await say(
    page,
    "7 / 8",
    "Iteration 6 passes — all acceptance criteria verified, p95 down to 94 ms.",
    4800,
    async () => {
      const firstToggle = page.locator(".pm-iter-logtoggle").first();
      await firstToggle.scrollIntoViewIfNeeded().catch(() => {});
      await firstToggle.click();
      await hold(page, 400);
      await page.locator(".pm-log-pre").first().scrollIntoViewIfNeeded().catch(() => {});
    },
  );
  await say(
    page,
    "7 / 8",
    "Iteration 5 was rejected: 96 ms with the debounce, but the flag defaulted to on.",
    5300,
    () => scrollToText(page, "96 ms"),
  );
  await say(
    page,
    "7 / 8",
    "Iteration 4 failed before that: p95 latency hit 210 ms with no keystroke debounce.",
    4600,
    () => scrollToText(page, "210 ms"),
  );

  // 8/8 — back to the cockpit. Hover and scroll only; never stop/delete/launch.
  await goto("/pm/projects/1", "Delivery Squad");
  await scrollToText(page, "Tasks");
  await say(
    page,
    "8 / 8",
    "Back on the cockpit: ‘Fix cart total rounding’ is running right now.",
    4300,
    () => page.locator(".pm-task-row").first().hover().catch(() => {}),
  );
  await say(
    page,
    "8 / 8",
    "‘Migrate checkout to new API’ hit its token budget before finishing.",
    4300,
    () => page.locator(".pm-task-row").nth(1).hover().catch(() => {}),
  );
  await say(page, "8 / 8", "Stop, archive, or launch the next task straight from this list.", 4300);

  // Closing the context flushes the .webm to disk.
  const video = page.video();
  await context.close();
  await browser.close();
  return video.path();
};

async function main() {
  const rawPath = await record();
  const recordedWallMs = Date.now() - recordStart;

  const webm = path.join(OUT_DIR, "pm-walkthrough.webm");
  await copyFile(rawPath, webm);
  console.log(`webm: ${webm}`);

  const ffmpeg = await findFfmpeg();
  const ffprobe = findFfprobe(ffmpeg);

  const mp4 = path.join(OUT_DIR, "pm-walkthrough.mp4");
  run(ffmpeg, [
    "-y", "-i", webm,
    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "23",
    "-movflags", "+faststart",
    mp4,
  ]);
  const mp4Size = (await stat(mp4)).size;
  console.log(`mp4:  ${mp4} (${(mp4Size / 1e6).toFixed(1)} MB)`);

  const gif = path.join(OUT_DIR, "pm-walkthrough.gif");
  let size = await makeGif(ffmpeg, webm, gif, 10, 960);
  if (size > 10 * 1024 * 1024) {
    console.log(`gif is ${(size / 1e6).toFixed(1)} MB — retrying at fps=8, 720px`);
    size = await makeGif(ffmpeg, webm, gif, 8, 720);
  }
  if (size > 10 * 1024 * 1024) {
    console.log(`gif is still ${(size / 1e6).toFixed(1)} MB — retrying at fps=8, 640px`);
    size = await makeGif(ffmpeg, webm, gif, 8, 640);
  }
  console.log(`gif:  ${gif} (${(size / 1e6).toFixed(1)} MB)`);

  // Scale the logged (Node wall-clock) subtitle timestamps to the measured
  // mp4 duration: Playwright's video encoder and Node's Date.now() do not
  // share a clock, so a small drift accumulates over a ~100s take. ffprobe
  // gives ground truth; everything else is scaled proportionally to it.
  const measuredSeconds = probeDurationSeconds(ffprobe, mp4);
  const scale = measuredSeconds ? (measuredSeconds * 1000) / recordedWallMs : 1;
  console.log(
    `duration: wall=${(recordedWallMs / 1000).toFixed(2)}s measured(mp4)=${
      measuredSeconds?.toFixed(2) ?? "?"
    }s scale=${scale.toFixed(4)}`,
  );

  const srtPath = path.join(OUT_DIR, "pm-walkthrough.srt");
  await writeFile(srtPath, buildSrt(subtitleLog, scale), "utf8");
  console.log(`srt:  ${srtPath} (${subtitleLog.length} lines)`);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
