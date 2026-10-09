import { createServer } from "node:http";
import type { AddressInfo } from "node:net";

export const syntheticArchive = Buffer.from("UEsDBBQAAAAAAAAAIVx1ZgTZEgAAABIAAAAMAAAAYXJ0aWZhY3QudHh0c3ludGhldGljLWV4cG9ydC1BUEsBAhQDFAAAAAAAAAAhXHVmBNkSAAAAEgAAAAwAAAAAAAAAAAAAAIABAAAAAGFydGlmYWN0LnR4dFBLBQYAAAAAAQABADoAAAA8AAAAAAA=", "base64");

/** Chromium native downloads bypass Playwright route interception. Serve the
 * synthetic artifact at a real, test-owned HTTP boundary and record requests. */
export async function createDownloadBoundary() {
  const requests: string[] = [];
  const server = createServer((request, response) => {
    const url = new URL(request.url!, "http://127.0.0.1");
    requests.push(url.pathname + url.search);
    const filename = url.pathname.split("/").pop() || "dataset.zip";
    response.writeHead(200, { "Content-Type": "application/zip", "Content-Disposition": `attachment; filename="${filename}"` });
    response.end(syntheticArchive);
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const origin = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  return {
    origin, requests,
    close: async () => {
      server.closeAllConnections();
      await new Promise<void>((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
    },
  };
}
export type DownloadBoundary = Awaited<ReturnType<typeof createDownloadBoundary>>;
