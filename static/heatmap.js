/** Lettore PORCAMP1 / PORCAMP2 + paint canvas da binario precalcolato. */
(function (global) {
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
    if (magic !== "PORCAMP1" && magic !== "PORCAMP2") throw new Error("magic heatmap");
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
    const nDays = magic === "PORCAMP2" ? Math.max(1, u8[58] || (meta.n_days || 1)) : 1;
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
      nDays,
      header,
      species,
      horizon_start: meta.horizon_start || null,
      fetched_at: meta.fetched_at
    };
  }

  function speciesDay(grid, off, s, d) {
    const o = off + 3 + (s * grid.nDays + d) * 2;
    return { stage: grid.u8[o], p: grid.u8[o + 1] / 100 };
  }

  function edgeSouth(grid) {
    return grid.south - grid.step / 2;
  }

  function edgeWest(grid) {
    return grid.west - grid.step / 2;
  }

  function rcAt(grid, lat, lon) {
    const row = Math.floor((lat - edgeSouth(grid)) / grid.step);
    const col = Math.floor((lon - edgeWest(grid)) / grid.step);
    if (row < 0 || col < 0 || row >= grid.nrows || col >= grid.ncols) return null;
    return [row, col];
  }

  function cellAt(grid, row, col, dayOffset) {
    if (row < 0 || col < 0 || row >= grid.nrows || col >= grid.ncols) return null;
    const off = grid.header + (row * grid.ncols + col) * grid.cellBytes;
    const elev = grid.dv.getInt16(off, true);
    if (elev === -32768) return null;
    const leaf = grid.u8[off + 2];
    const nDays = grid.nDays || 1;
    const day = Math.max(0, Math.min(nDays - 1, dayOffset || 0));
    const by = {};
    for (let s = 0; s < grid.nsp; s++) {
      const horizon = [];
      for (let d = 0; d < nDays; d++) horizon.push(speciesDay(grid, off, s, d));
      const now = horizon[day] || horizon[0];
      by[grid.species[s]] = { stage: now.stage, p: now.p, horizon };
    }
    const lat = grid.south + row * grid.step;
    const lon = grid.west + col * grid.step;
    return { lat, lon, elev, leaf: LEAF[leaf] || "altro", leaf_code: leaf, by };
  }

  function latLonToCell(grid, lat, lon, dayOffset) {
    const rc = rcAt(grid, lat, lon);
    if (!rc) return null;
    return cellAt(grid, rc[0], rc[1], dayOffset);
  }

  function stageAt(grid, row, col, spIdx, day) {
    const off = grid.header + (row * grid.ncols + col) * grid.cellBytes;
    if (grid.dv.getInt16(off, true) === -32768) return null;
    const nDays = grid.nDays || 1;
    const o = off + 3 + (spIdx * nDays + day) * 2;
    return { stage: grid.u8[o], p: grid.u8[o + 1] / 100 };
  }

  function makeLayer(grid, stageRgba) {
    const Heat = L.GridLayer.extend({
      options: {
        opacity: 0.42,
        updateWhenZooming: false,
        keepBuffer: 1
      },
      initialize: function (g, rgba) {
        L.GridLayer.prototype.initialize.call(this, {});
        this._g = g;
        this._rgba = rgba;
        this._sp = 0;
        this._day = 0;
      },
      setQuery: function (species, day) {
        this._sp = Math.max(0, this._g.species.indexOf(species));
        this._day = Math.max(0, Math.min((this._g.nDays || 1) - 1, day || 0));
        if (this._map) this.redraw();
      },
      createTile: function (coords) {
        const g = this._g;
        const tile = L.DomUtil.create("canvas", "leaflet-tile");
        const size = this.getTileSize();
        tile.width = size.x;
        tile.height = size.y;
        const ctx = tile.getContext("2d");
        const map = this._map;
        if (!map) return tile;
        const z = coords.z;
        const ox = coords.x * size.x;
        const oy = coords.y * size.y;
        const nw = map.unproject(L.point(ox, oy), z);
        const se = map.unproject(L.point(ox + size.x, oy + size.y), z);
        const pad = g.step;
        const south = Math.min(nw.lat, se.lat) - pad;
        const north = Math.max(nw.lat, se.lat) + pad;
        const west = Math.min(nw.lng, se.lng) - pad;
        const east = Math.max(nw.lng, se.lng) + pad;
        const es = edgeSouth(g);
        const ew = edgeWest(g);
        const r0 = Math.max(0, Math.floor((south - es) / g.step));
        const r1 = Math.min(g.nrows - 1, Math.floor((north - es) / g.step));
        const c0 = Math.max(0, Math.floor((west - ew) / g.step));
        const c1 = Math.min(g.ncols - 1, Math.floor((east - ew) / g.step));
        const sp = this._sp;
        const day = this._day;
        const rgbaMap = this._rgba;
        for (let r = r0; r <= r1; r++) {
          const lat0 = es + r * g.step;
          const lat1 = lat0 + g.step;
          for (let c = c0; c <= c1; c++) {
            const st = stageAt(g, r, c, sp, day);
            if (!st || st.stage <= 0) continue;
            const lon0 = ew + c * g.step;
            const lon1 = lon0 + g.step;
            const p0 = map.project([lat0, lon0], z);
            const p1 = map.project([lat1, lon1], z);
            const x = p0.x - ox;
            const y = p1.y - oy;
            const rw = p1.x - p0.x;
            const rh = p0.y - p1.y;
            const rgba = rgbaMap[st.stage] || rgbaMap[1];
            ctx.fillStyle = "rgb(" + rgba[0] + "," + rgba[1] + "," + rgba[2] + ")";
            ctx.fillRect(x, y, rw + 0.6, rh + 0.6);
          }
        }
        return tile;
      }
    });
    return new Heat(grid, stageRgba);
  }

  function cellsInRegion(grid, species, pointInRegion, reg, limitScan, dayOffset) {
    const out = [];
    const b = reg.bbox;
    const r0 = Math.max(0, Math.floor((b.s - grid.south) / grid.step));
    const r1 = Math.min(grid.nrows - 1, Math.ceil((b.n - grid.south) / grid.step));
    const c0 = Math.max(0, Math.floor((b.w - grid.west) / grid.step));
    const c1 = Math.min(grid.ncols - 1, Math.ceil((b.e - grid.west) / grid.step));
    let n = 0;
    const day = dayOffset || 0;
    for (let r = r0; r <= r1; r++) {
      for (let c = c0; c <= c1; c++) {
        const cell = cellAt(grid, r, c, day);
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
          p: v.p,
          horizon: v.horizon,
          forecast: day > 0
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
    makeLayer,
    latLonToCell,
    cellsInRegion,
    gunzip
  };
})(window);
