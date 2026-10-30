import http from "node:http";
import type { AddressInfo } from "node:net";

export interface Recorded {
  method: string;
  path: string;
  headers: http.IncomingHttpHeaders;
  body: string;
}

export interface Canned {
  status: number;
  body: unknown;
}

/** A server on 127.0.0.1 only, on a free port. It records every request. */
export class FakeServer {
  readonly requests: Recorded[] = [];
  private canned: Canned = { status: 200, body: {} };
  private queued: Canned[] = [];
  private readonly server: http.Server;
  baseUrl = "";

  constructor() {
    this.server = http.createServer((req, res) => {
      const chunks: Buffer[] = [];
      req.on("data", (c: Buffer) => chunks.push(c));
      req.on("end", () => {
        this.requests.push({
          method: req.method ?? "",
          path: req.url ?? "",
          headers: req.headers,
          body: Buffer.concat(chunks).toString("utf8"),
        });
        const c = this.queued.shift() ?? this.canned;
        res.writeHead(c.status, { "Content-Type": "application/json" });
        res.end(typeof c.body === "string" ? c.body : JSON.stringify(c.body));
      });
    });
  }

  /** Answers for the next requests, in order; after that the canned answer applies. */
  queue(...answers: Canned[]): void {
    this.queued = [...answers];
  }

  respond(status: number, body: unknown): void {
    this.canned = { status, body };
  }

  async start(): Promise<void> {
    await new Promise<void>((ok) => this.server.listen(0, "127.0.0.1", ok));
    this.baseUrl = `http://127.0.0.1:${(this.server.address() as AddressInfo).port}`;
  }

  async stop(): Promise<void> {
    this.server.closeAllConnections();
    await new Promise<void>((ok) => this.server.close(() => ok()));
  }
}

/** A loopback port that nothing listens on. */
export async function closedPort(): Promise<number> {
  const s = new FakeServer();
  await s.start();
  const port = Number(new URL(s.baseUrl).port);
  await s.stop();
  return port;
}
