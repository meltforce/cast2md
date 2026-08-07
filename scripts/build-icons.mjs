/**
 * Builds the app icon set in src/cast2md/static/app-icon/ from the mark defined
 * below.
 *
 * The output is committed. The script is not part of any build: the icons
 * change once a year at most, and it needs Chrome and ImageMagick, neither of
 * which CI has.
 *
 *   node scripts/build-icons.mjs
 *   CHROME=/path/to/chrome node scripts/build-icons.mjs
 *
 * The mark is "c2" in Archivo 800, the page ground (#f3f2f2) on a full-bleed
 * accent field (#ec3013), with a rule bar beneath it on the same measure. Both
 * colours are the light-theme `--color-bg` and `--color-accent` of
 * static/homelab.css; vimmary and freereps carry the same treatment.
 *
 * The mark must stay LIGHTER than the field. iOS 18 derives the dark and tinted
 * home screen variants from this one file; with a dark mark on a mid field both
 * collapse toward black and the icon reads as an empty rounded square. This is
 * the defect FreeReps fixed in c7319b0 and the reason the artwork is not ink on
 * accent.
 *
 * No rounded corners are baked in — iOS and Android apply their own mask.
 */

import { execFileSync } from "node:child_process";
import { mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const repo = join(here, "..");
const staticDir = join(repo, "src", "cast2md", "static");
const outDir = join(staticDir, "app-icon");
const tmpDir = join(here, ".icon-build");

const CHROME =
  process.env.CHROME ??
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";

const FIELD = "#ec3013";
const MARK = "#f3f2f2";

/* Geometry on the 512 canvas, measured rather than guessed: at font-size 353
   the inked block of "c2" is 397 × 252 with its top-left at (61, 98), which is
   the widest the mark can be and still leave a margin on both edges. "c2" is
   taller than a pure x-height mark because the figure reaches cap height, so
   the block sits higher than it does in vimmary. The bar takes the mark's
   measure and its left edge, and the 98px above the mark equals the 98px below
   the bar. */
const GEOM = {
  fontSize: 353,
  baseline: 345,
  barX: 61,
  barY: 384,
  barW: 397,
  barH: 30,
};

function svg({ scale = 1 } = {}) {
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" width="512" height="512">
  <rect width="512" height="512" fill="${FIELD}"/>
  <g transform="translate(256 256) scale(${scale}) translate(-256 -256)">
    <text x="256" y="${GEOM.baseline}" text-anchor="middle" fill="${MARK}"
          font-family="Archivo Variable" font-weight="800"
          font-size="${GEOM.fontSize}" letter-spacing="-0.03em">c2</text>
    <rect x="${GEOM.barX}" y="${GEOM.barY}" width="${GEOM.barW}" height="${GEOM.barH}" fill="${MARK}"/>
  </g>
</svg>`;
}

/* Chrome renders the SVG, so the mark comes out in the real variable face
   rather than a fallback. The font is the one the web UI already ships, inlined
   because the render runs from a file:// URL. */
const font = readFileSync(
  join(staticDir, "fonts", "Archivo-Variable.woff2"),
).toString("base64");

function page(markup) {
  return `<!DOCTYPE html><html><head><meta charset="utf-8"><style>
@font-face{font-family:"Archivo Variable";font-style:normal;font-weight:100 900;
  src:url(data:font/woff2;base64,${font}) format("woff2-variations");}
html,body{margin:0;padding:0;width:512px;height:512px;overflow:hidden}
svg{display:block}
</style></head><body>${markup}</body></html>`;
}

function render(name, markup) {
  const html = join(tmpDir, `${name}.html`);
  const png = join(tmpDir, `${name}.png`);
  writeFileSync(html, page(markup));
  execFileSync(CHROME, [
    "--headless",
    "--disable-gpu",
    `--screenshot=${png}`,
    "--window-size=512,512",
    "--hide-scrollbars",
    "--force-device-scale-factor=1",
    `file://${html}`,
  ], { stdio: "ignore" });
  return png;
}

function resize(from, to, size) {
  execFileSync("magick", [from, "-resize", `${size}x${size}`, to]);
}

rmSync(tmpDir, { recursive: true, force: true });
mkdirSync(tmpDir, { recursive: true });
mkdirSync(outDir, { recursive: true });

const full = render("icon", svg());
/* Android maskable icons are cropped to a shape that can eat the outer 20%, so
   the mark shrinks into the safe zone while the field keeps bleeding. */
const maskable = render("maskable", svg({ scale: 0.78 }));

execFileSync("magick", [full, join(outDir, "icon-512.png")]);
execFileSync("magick", [maskable, join(outDir, "icon-maskable-512.png")]);
resize(full, join(outDir, "icon-192.png"), 192);
resize(full, join(outDir, "apple-touch-icon-180.png"), 180);
resize(full, join(outDir, "favicon-32.png"), 32);

rmSync(tmpDir, { recursive: true, force: true });

console.log(`wrote 5 icons to ${outDir}`);
