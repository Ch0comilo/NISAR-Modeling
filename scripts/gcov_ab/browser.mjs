// Drive the real Explorer through local FastAPI and the Modal preview.
import { mkdir, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import { performance } from "node:perf_hooks";

const frontend = path.resolve(process.env.NISAR_FRONTEND ?? "../frontend");
const require = createRequire(path.join(frontend, "package.json"));
const { chromium } = require("@playwright/test");
const ui = "http://127.0.0.1:5195";
const api = "http://127.0.0.1:8025";
const gateway = "https://ch0comilo--nisar-hazard-watch-ml-gcov-ab.us-east.modal.run";
const output = path.resolve(process.argv[2] ?? "../gcov-ab-results");
const points = { choco: "-76.68431,5.66861", qadirabad: "73.713,32.329" };
const rounds = [
  ["choco", "pyfive", 10], ["qadirabad", "h5py", 10],
  ["choco", "h5py", 40], ["qadirabad", "pyfive", 40],
  ["choco", "pyfive", 40], ["qadirabad", "h5py", 40],
  ["choco", "h5py", 10], ["qadirabad", "pyfive", 10],
];
const tag = process.env.NISAR_RUN_TAG ?? "r1";
const runs = process.env.NISAR_SINGLE
  ? [process.env.NISAR_SINGLE.split(",")]
  : (tag === "r2" ? [...rounds].reverse() : rounds).slice(Number(process.env.NISAR_START ?? 0));
const browser = await chromium.launch({ args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"] });
await mkdir(output, { recursive: true });

async function fetchJson(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) throw new Error(`HTTP ${response.status} for ${new URL(url).pathname}`);
  return response.json();
}

async function run(site, reader, posting, window = 384, chips) {
  const id = `${tag}-${site}-${reader}-${posting}-${window}`;
  const selector = `${reader}:${posting}:${id}:${chips ?? id}`;
  await fetchJson(api + "/benchmark/variant", {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ selector, window: Number(window) }),
  });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, reducedMotion: "reduce" });
  await page.addInitScript(() => { window.__nisarFirstDrape = null; });
  await page.route(/\/src\/features\/globe\/frameEntities\.ts(?:\?|$)/, async (route) => {
    const response = await route.fetch();
    const original = await response.text();
    const marker = "viewer.imageryLayers.add(layer);";
    if (!original.includes(marker)) throw new Error("Explorer drape instrumentation anchor is absent");
    await route.fulfill({ response, body: original.replace(marker, marker + `
      const stop = viewer.scene.postRender.addEventListener(() => {
        if (!layer.show || layer.alpha <= 0) return;
        const tiles = viewer.scene.globe._surface._tilesToRender;
        const rendered = tiles.some((tile) => tile.data?.imagery.some(({ readyImagery }) =>
          readyImagery?.imageryLayer === layer && (readyImagery.texture || readyImagery.textureWebMercator)));
        if (rendered) {
          window.__nisarFirstDrape ??= performance.timeOrigin + performance.now();
          stop();
        }
      });
    `) });
  });
  await page.route("**/benchmark-launch", (route) => route.fulfill({
    contentType: "text/html",
    body: `<a href="/explore?point=${points[site]}&lens=flood_wetland">Open measured exploration</a>`,
  }));
  const result = { site, reader, posting: Number(posting), window: Number(window), selector };
  const images = [];
  const pendingImages = [];
  let started;
  page.on("response", (response) => {
    if (!started || !response.ok() || !response.url().includes("/layers/") ||
        !response.headers()["content-type"]?.includes("image/png")) return;
    const seconds = (performance.now() - started) / 1000;
    pendingImages.push(response.body().then((bytes) => {
      if (bytes.toString("hex", 0, 8) !== "89504e470d0a1a0a") return;
      images.push({ name: new URL(response.url()).pathname.split("/").at(-1), bytes: bytes.length,
        width: bytes.readUInt32BE(16), height: bytes.readUInt32BE(20), seconds });
    }).catch(() => {}));
  });
  try {
    await page.goto(ui + "/benchmark-launch");
    const submit = page.waitForResponse((r) => r.request().method() === "POST" &&
      new URL(r.url()).pathname === "/api/v1/explorations", { timeout: 60_000 });
    started = performance.now();
    const clickEpoch = Date.now();
    await page.getByRole("link").click({ noWaitAfter: true });
    const response = await submit;
    result.backendHttp = response.status();
    result.backendSubmit = await response.json();
    const variant = await fetchJson(api + "/benchmark/variant");
    result.modalSubmit = variant.last;
    if (![200, 202].includes(response.status())) throw new Error("FastAPI rejected the exploration");
    const jobPath = "/api/v1/explorations/" + result.backendSubmit.id;
    const modalPath = variant.last.modal.status_url;
    let backend = result.backendSubmit;
    const keep = page.getByTestId("job-keep");
    if (await keep.isVisible()) await keep.click();
    const terminal = new Set(["succeeded", "partial", "failed", "cancelled", "no_coverage", "insufficient_history"]);
    for (let i = 0; !terminal.has(backend.status) && i < 148; i++) {
      await new Promise((resolve) => setTimeout(resolve, 2000));
      backend = await fetchJson(api + jobPath);
    }
    result.backendStatus = backend.status;
    result.backendError = backend.error;
    result.modal = await fetchJson(gateway + modalPath);
    if (backend.result?.layers?.length) {
      result.webglUnavailable = await page.getByTestId("no-webgl").isVisible();
      const stamp = await page.waitForFunction(() => window.__nisarFirstDrape, undefined, { timeout: 12_000 })
        .then((handle) => handle.jsonValue(), () => null);
      result.firstVisibleSeconds = stamp ? (stamp - clickEpoch) / 1000 : null;
      await page.getByTestId("more-info").click({ timeout: 15_000 });
      const sheet = page.getByRole("dialog", { name: "More information" });
      await sheet.waitFor();
      const image = sheet.locator("img").first();
      await image.waitFor({ state: "visible" });
      await image.evaluate((node) => node.decode());
      await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      result.firstPanelVisibleSeconds = (performance.now() - started) / 1000;
      result.panelImages = await sheet.locator("img").evaluateAll(async (nodes) => {
        await Promise.allSettled(nodes.map((node) => node.decode()));
        return nodes.filter((node) => node.naturalWidth).map((node) => ({
          width: node.naturalWidth, height: node.naturalHeight, alt: node.alt,
        }));
      });
      if (site === "choco") await page.screenshot({ path: path.join(output, id + ".png") });
    }
  } catch (error) {
    result.error = String(error);
  } finally {
    await Promise.allSettled(pendingImages);
    result.pngs = images;
    result.browserElapsed = started ? (performance.now() - started) / 1000 : null;
    await writeFile(path.join(output, id + ".json"), JSON.stringify(result, null, 2));
    console.log(JSON.stringify({ id, backend: result.backendStatus, job: result.modal?.job_id,
      science: result.modal?.result?.science_status, timing: result.modal?.result?.science?.timings,
      visible: result.firstVisibleSeconds, images: images.length, error: result.error }));
    await page.close();
  }
}

try {
  for (const args of runs) await run(...args);
} finally {
  await browser.close();
}
