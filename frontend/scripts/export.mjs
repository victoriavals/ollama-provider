import {
  cp,
  mkdir,
  readFile,
  readdir,
  rename,
  writeFile,
} from "node:fs/promises";
import { createHash, randomUUID } from "node:crypto";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { parse } from "parse5";

const frontend = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
);
const source = path.join(frontend, "out");
const root = path.resolve(frontend, "../gateway/static/dashboard/build");
const release = `release-${Date.now()}-${randomUUID().slice(0, 8)}`;
const destination = path.join(root, release);
await mkdir(root, { recursive: true });
await cp(source, destination, { recursive: true, errorOnExist: true });
const pages = {};
const assets = {};
const types = {
  ".js": "text/javascript",
  ".css": "text/css",
  ".woff2": "font/woff2",
  ".woff": "font/woff",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".ico": "image/x-icon",
  ".txt": "text/plain",
  ".json": "application/json",
  ".lottie": "application/zip",
  ".riv": "application/octet-stream",
  ".wasm": "application/wasm",
};
async function walk(directory) {
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const full = path.join(directory, entry.name);
    if (entry.isDirectory()) {
      await walk(full);
      continue;
    }
    const relative = path.relative(destination, full).split(path.sep).join("/");
    if (entry.name.startsWith(".") || relative.endsWith(".map")) continue;
    if (relative.endsWith(".html")) {
      const route =
        relative === "index.html"
          ? ""
          : relative.endsWith("/index.html")
            ? relative.slice(0, -11)
            : null;
      if (
        route === null ||
        !["", "keys", "usage", "requests", "prompts", "models"].includes(route)
      )
        continue;
      const html = await readFile(full, "utf8");
      const hashes = new Set();
      function inspect(node) {
        if (
          node.tagName === "script" &&
          !node.attrs?.some((attr) => attr.name === "src")
        ) {
          const code = (node.childNodes || [])
            .filter((child) => child.nodeName === "#text")
            .map((child) => child.value)
            .join("");
          if (code)
            hashes.add(
              `sha256-${createHash("sha256").update(code).digest("base64")}`,
            );
        }
        if (node.tagName === "style")
          throw new Error(
            `Inline stylesheet in ${relative}; extract it before release.`,
          );
        for (const child of node.childNodes || []) inspect(child);
      }
      inspect(parse(html));
      pages[route] = { file: relative, script_hashes: [...hashes] };
    } else if (types[path.extname(relative)])
      assets[relative] = types[path.extname(relative)];
  }
}
await walk(destination);
if (Object.keys(pages).length !== 6)
  throw new Error("Expected all six dashboard pages.");
await writeFile(
  path.join(destination, "manifest.json"),
  JSON.stringify({ version: 1, pages, assets }, null, 2),
);
const pointer = path.join(root, `active-${randomUUID()}.tmp`);
await writeFile(pointer, JSON.stringify({ release }));
await rename(pointer, path.join(root, "active.json"));
console.log(
  `Exported ${Object.keys(pages).length} pages and ${Object.keys(assets).length} assets to ${release}.`,
);
