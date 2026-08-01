import { createReadStream, statSync } from "node:fs";
import { createServer } from "node:http";
import { extname, join, normalize, resolve } from "node:path";

const root = resolve(import.meta.dirname, "../../..");
const types = {
  ".css": "text/css; charset=utf-8",
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".png": "image/png",
};

createServer((request, response) => {
  const pathname = decodeURIComponent(new URL(request.url, "http://localhost").pathname);
  const requested = normalize(join(root, pathname));
  if (!requested.startsWith(root)) {
    response.writeHead(403).end("Forbidden");
    return;
  }
  let file = requested;
  try {
    if (statSync(file).isDirectory()) file = join(file, "index.html");
    response.writeHead(200, {
      "Cache-Control": "no-store",
      "Content-Type": types[extname(file)] || "application/octet-stream",
    });
    createReadStream(file).pipe(response);
  } catch {
    response.writeHead(404).end("Not found");
  }
}).listen(4173, "127.0.0.1", () => {
  console.log("Household Tasks UI harness listening on http://127.0.0.1:4173");
});
