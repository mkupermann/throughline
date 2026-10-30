// Runs test/integration/extension.test.ts inside a real VS Code against a
// running Throughline test instance. Local only, never in CI.
//   THROUGHLINE_TEST_URL=http://127.0.0.1:8792 THROUGHLINE_TEST_KEY=... npm run test:integration
import { mkdtemp, mkdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { build } from "esbuild";
import { runTests } from "@vscode/test-electron";

async function main(): Promise<void> {
  const url = process.env.THROUGHLINE_TEST_URL?.trim();
  if (!url) {
    console.log("test:integration skipped: THROUGHLINE_TEST_URL is not set.");
    return;
  }
  if (!process.env.THROUGHLINE_TEST_KEY?.trim()) {
    throw new Error("THROUGHLINE_TEST_KEY is not set (the agent key of the test instance).");
  }
  const root = path.resolve(__dirname, "..");
  const testsPath = path.join(root, "dist", "test", "integration.js");
  // The test must live inside the extension folder so it shares the extension's vscode API object.
  await build({
    entryPoints: [path.join(root, "test", "integration", "extension.test.ts")],
    bundle: true,
    platform: "node",
    format: "cjs",
    target: "node20",
    outfile: testsPath,
    external: ["vscode"],
    logLevel: "warning",
  });

  // A throwaway profile: the settings below and the in-memory secret store never touch the user's VS Code.
  const profile = await mkdtemp(path.join(tmpdir(), "tl-vsx-"));
  await mkdir(path.join(profile, "User"), { recursive: true });
  await writeFile(
    path.join(profile, "User", "settings.json"),
    JSON.stringify({ "throughline.url": url, "throughline.project": "Atlas-demo", "telemetry.telemetryLevel": "off" }),
  );
  try {
    await runTests({
      extensionDevelopmentPath: root,
      extensionTestsPath: testsPath,
      launchArgs: [
        "--disable-extensions",
        `--user-data-dir=${profile}`,
        `--extensions-dir=${path.join(profile, "extensions")}`,
        "--use-inmemory-secretstorage",
      ],
    });
  } finally {
    await rm(profile, { recursive: true, force: true });
  }
}

main().catch((e) => {
  console.error(e instanceof Error ? e.message : e);
  process.exit(1);
});
