const EXPECTED_TOTALS = {
  conversations: 10,
  messages: 52,
  chunks: 22,
  skills: 5,
  projects: 5,
};

const KNOWLEDGE_FINGERPRINT =
  "JWT auth strategy for acme-web: 15-minute access tokens + 7-day refresh tokens";

export function assertScreenshotFixture(overview, project) {
  const totalsMatch = Object.entries(EXPECTED_TOTALS).every(
    ([key, expected]) => overview?.totals?.[key] === expected,
  );
  const projectMatches =
    project?.project === "acme-web" &&
    project?.sessionCount === 5 &&
    project?.messageCount === 26 &&
    project?.summary === "5 sessions, 26 messages";
  const knowledgeMatches = project?.knowledge?.some((item) =>
    item?.content?.startsWith(KNOWLEDGE_FINGERPRINT),
  );

  if (!totalsMatch || !projectMatches || !knowledgeMatches) {
    throw new Error(
      "Refusing to capture screenshots: the server does not match the bundled synthetic fixture.",
    );
  }
}
