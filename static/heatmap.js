/** Lettore PORCHMAP1 + paint canvas da binario precalcolato. */
(function (global) {
  const MAGIC = "PORCAMP1";
  const LEAF = { 0: "fuori", 1: "altro", 2: "latifoglie", 3: "conifere", 4: "misto" };

  async function gunzip(buf) {
    if (typeof DecompressionStream === "undefined") {
      throw new Error("DecompressionStream assente");
    }
    const ds = new DecompressionStream("gzip");
    const stream = new Response(buf).body.pipeThrough(ds);
    return new Uint8Array(await new Response(stream).arrayBuffer());
  }

  function parseHeatmap(raw, meta) {
    const src = raw instanceof Uint8Array ? raw : new Uint8Array(raw);
    // Copia su ArrayBuffer proprio: DecompressionStream può dare byteOffset ≠ 0.
    const copy = src.buffer.slice(src.byteOffset, src.byteOffset + src.byteLength);
    const u8 = new Uint8Array(copy);
    const magic = String.fromCharCode(...u8.slice(0, 8));
    if (magic !== MAGIC) throw new Error("magic heatmap");
    const dv = new DataView(copy);
    const south = dv.getFloat64(8, true);
    const west = dv.getFloat64(16, true);
    const north = dv.getFloat64(24, true);
    const east = dv.getFloat64(32, true);
    const step = dv.getFloat64(40, true);
    const nrows = dv.getUint32(48, true);
    const ncols = dv.getUint32(52, true);
    const nsp = u8[56];
    const cellBytes = u8[57];
    const header = meta.header_bytes || 64;
    const species = meta.species || ["edulis", "pinophilus", "aestivalis", "aereus"];
    return {
      u8,
      dv,
      south,
      west,
      north,
      east,
      step,
      nrows,
      ncols,
      nsp,
      cellBytes,
      header,
      species,
      fetched_at: meta.fetched_at
    };
  }

  function cellAt(grid, row, col) {
    if (row < 0 || col < 0 || row >= grid.nrows || col >= grid.ncols) return null;
    const off = grid.header + (row * grid.ncols + col) * grid.cellBytes;
    const elev = grid.dv.getInt16(off, true);
    if (elev === -32768) return null;
    const leaf = grid.u8[off + 2];
    const by = {};
    for (let s = 0; s < grid.nsp; s++) {
      const o = off + 3 + s * 2;
      by[grid.species[s]] = { stage: grid.u8[o], p: grid.u8[o + 1] / 100 };
    }
    const lat = grid.south + row * grid.step;
    const lon = grid.west + col * grid.step;
    return { lat, lon, elev, leaf: LEAF[leaf] || "altro", leaf_code: leaf, by };
  }

  function latLonToCell(grid, lat, lon) {
    const row = Math.round((lat - grid.south) / grid.step);
    const col = Math.round((lon - grid.west) / grid.step);
    return cellAt(grid, row, col);
  }

  function sampleCells(grid, bounds, species, maxPts) {
    const pad = grid.step;
    const r0 = Math.max(0, Math.floor((bounds.s - pad - grid.south) / grid.step));
    const r1 = Math.min(grid.nrows - 1, Math.ceil((bounds.n + pad - grid.south) / grid.step));
    const c0 = Math.max(0, Math.floor((bounds.w - pad - grid.west) / grid.step));
    const c1 = Math.min(grid.ncols - 1, Math.ceil((bounds.e + pad - grid.west) / grid.step));
    const rows = Math.max(1, r1 - r0 + 1);
    const cols = Math.max(1, c1 - c0 + 1);
    let stride = 1;
    while ((rows / stride) * (cols / stride) > maxPts) stride += 1;
    const out = [];
    for (let r = r0; r <= r1; r += stride) {
      for (let c = c0; c <= c1; c += stride) {
        const cell = cellAt(grid, r, c);
        if (!cell) continue;
        const v = cell.by[species] || { stage: 0, p: 0 };
        out.push({
          lat: cell.lat,
          lon: cell.lon,
          elev: cell.elev,
          leaf: cell.leaf,
          leaf_note: cell.leaf,
          stage: v.stage,
          p: v.p,
          decision: v.stage >= 4 ? "vai" : v.stage >= 2 ? "aspetta" : "lascia"
        });
      }
    }
    return { cells: out, stride, step: grid.step * stride };
  }

  function cellsInRegion(grid, species, pointInRegion, reg, limitScan) {
    const out = [];
    const b = reg.bbox;
    const r0 = Math.max(0, Math.floor((b.s - grid.south) / grid.step));
    const r1 = Math.min(grid.nrows - 1, Math.ceil((b.n - grid.south) / grid.step));
    const c0 = Math.max(0, Math.floor((b.w - grid.west) / grid.step));
    const c1 = Math.min(grid.ncols - 1, Math.ceil((b.e - grid.west) / grid.step));
    let n = 0;
    for (let r = r0; r <= r1; r++) {
      for (let c = c0; c <= c1; c++) {
        const cell = cellAt(grid, r, c);
        if (!cell) continue;
        if (!pointInRegion(cell.lat, cell.lon, reg)) continue;
        const v = cell.by[species] || { stage: 0, p: 0 };
        if ((v.stage || 0) <= 0) continue;
        out.push({
          lat: cell.lat,
          lon: cell.lon,
          elev: cell.elev,
          leaf: cell.leaf,
          leaf_note: cell.leaf,
          stage: v.stage,
          p: v.p
        });
        n++;
        if (limitScan && n > limitScan) break;
      }
      if (limitScan && n > limitScan) break;
    }
    return out;
  }

  async function loadBundle(base) {
    const bust = "?t=" + Date.now();
    const meta = await (await fetch(base + "data/meta.json" + bust)).json();
    const gz = await (await fetch(base + "data/heatmap.bin.gz" + bust)).arrayBuffer();
    const raw = await gunzip(gz);
    const grid = parseHeatmap(raw, meta);
    let bulletins = null;
    try {
      bulletins = await (await fetch(base + "data/bulletins.json" + bust)).json();
    } catch (_) {}
    return { meta, grid, bulletins };
  }

  global.PorciniHeat = {
    loadBundle,
    sampleCells,
    latLonToCell,
    cellsInRegion,
    gunzip
  };
})(window);
