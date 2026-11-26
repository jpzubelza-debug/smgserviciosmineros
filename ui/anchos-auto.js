/* Anchos de columna editables en TODAS las tablas de la página, sin configuración.
   Incluir con <script src="/ui/anchos-auto.js" defer></script>.
   - Cada encabezado recibe un asa en su borde derecho: arrastrar cambia el ancho; doble clic lo restablece;
     con foco en el asa, flechas ←/→ (Shift = de a 64px).
   - Los anchos se guardan por navegador (localStorage "smg.anchosAuto.<ruta>|<tabla>") y se reaplican cuando
     la tabla se vuelve a dibujar o se hace visible.
   - Se saltean: tablas ya manejadas por SMG.asasColumnas (data-anchos), tablas anidadas, tablas sin fila de
     encabezado <th>, y cualquier tabla (o contenedor) con data-sin-anchos. */
(function () {
  if (window.__smgAnchosAuto) return;
  window.__smgAnchosAuto = true;

  const MIN = 40;
  const PREF = 'smg.anchosAuto.';
  const ASA = 'ca-asa';

  const css = document.createElement('style');
  css.textContent = `
    :where(th.ca-th) { position: relative; }
    .${ASA} { position: absolute; top: 0; right: 0; width: 8px; height: 100%; cursor: col-resize; touch-action: none; z-index: 1; }
    .${ASA}::after { content: ""; position: absolute; top: 20%; bottom: 20%; right: 3px; width: 2px; border-radius: 1px; background: transparent; }
    th:hover > .${ASA}::after { background: currentColor; opacity: .25; }
    .${ASA}:hover::after, .${ASA}:focus-visible::after, .${ASA}.is-drag::after { background: #3b82f6 !important; opacity: 1 !important; }
    .${ASA}:focus-visible { outline: none; }
    table.ca-fija > thead > tr > th, table.ca-fija > tbody > tr > td, table.ca-fija > tfoot > tr > td { overflow: hidden; text-overflow: ellipsis; }
    body.ca-arrastrando, body.ca-arrastrando * { cursor: col-resize !important; user-select: none !important; }
    @media print { .${ASA} { display: none !important; } }`;
  document.head.appendChild(css);

  const leer = (k) => { try { return JSON.parse(localStorage.getItem(PREF + k) || 'null') || {}; } catch (e) { return {}; } };
  const escribir = (k, v) => {
    try {
      if (v && Object.keys(v).length) localStorage.setItem(PREF + k, JSON.stringify(v));
      else localStorage.removeItem(PREF + k);
    } catch (e) { /* sin almacenamiento: los anchos duran hasta recargar */ }
  };
  const texto = (el) => (el.textContent || '').replace(/\s+/g, ' ').trim();

  /* Fila de encabezado que define las columnas: la última fila del thead (o la primera fila de th si no hay thead)
     sin colspan, con tantas celdas como columnas tiene la tabla. */
  function filaClave(tabla) {
    let filas = tabla.tHead ? [...tabla.tHead.rows] : [];
    if (!filas.length) {
      const primera = tabla.rows[0];
      if (!primera || !primera.cells.length || [...primera.cells].some((c) => c.tagName !== 'TH')) return null;
      filas = [primera];
    }
    const total = Math.max(...[...tabla.rows].slice(0, 30).map((r) => [...r.cells].reduce((s, c) => s + (c.colSpan || 1), 0)));
    for (let i = filas.length - 1; i >= 0; i--) {
      const celdas = [...filas[i].cells];
      if (celdas.length > 1 && celdas.length === total && celdas.every((c) => c.colSpan === 1 && c.tagName === 'TH')) return filas[i];
    }
    return null;
  }

  function claveTabla(tabla, fila) {
    const base = tabla.id || 'h:' + [...fila.cells].map(texto).join('|').slice(0, 200);
    return location.pathname + '|' + base;
  }
  function clavesColumnas(fila) {
    const vistas = {};
    return [...fila.cells].map((th, i) => {
      let k = th.dataset.colKey || texto(th) || `#${i}`;
      if (vistas[k]) k = `${k}#${i}`;
      vistas[k] = 1;
      return k;
    });
  }

  function colgroupDe(tabla, n) {
    let cg = [...tabla.children].find((c) => c.tagName === 'COLGROUP');
    const cols = cg ? [...cg.children].filter((c) => c.tagName === 'COL') : [];
    if (cg && cols.length === n && cols.every((c) => (c.span || 1) === 1)) return cols;
    if (!cg) { cg = document.createElement('colgroup'); tabla.insertBefore(cg, tabla.firstChild); }
    cg.innerHTML = '<col>'.repeat(n);
    return [...cg.children];
  }

  // Estado por tabla: estilos originales para poder volver atrás
  const estados = new WeakMap();
  function estado(tabla) {
    let e = estados.get(tabla);
    if (!e) {
      e = { orig: { width: tabla.style.width, minWidth: tabla.style.minWidth, maxWidth: tabla.style.maxWidth, tableLayout: tabla.style.tableLayout }, cols: new WeakMap() };
      estados.set(tabla, e);
    }
    return e;
  }
  function soltarTabla(tabla, cols) {
    const e = estado(tabla);
    Object.assign(tabla.style, e.orig);
    tabla.classList.remove('ca-fija');
    cols.forEach((col) => { if (e.cols.has(col)) col.style.width = e.cols.get(col); });
  }
  function fijarTabla(tabla, cols, anchos) {
    const e = estado(tabla);
    cols.forEach((col, i) => {
      if (!e.cols.has(col)) e.cols.set(col, col.style.width);
      col.style.width = `${anchos[i]}px`;
    });
    const suma = anchos.reduce((s, w) => s + w, 0);
    tabla.style.tableLayout = 'fixed';
    tabla.style.width = `${suma}px`;
    tabla.style.minWidth = `${suma}px`;
    tabla.style.maxWidth = 'none';
    tabla.classList.add('ca-fija');
  }

  /* Recalcula la tabla: anchos naturales + los guardados. Devuelve false si no está visible (se reintenta luego). */
  function aplicar(tabla) {
    const fila = filaClave(tabla);
    if (!fila) return true;
    const guardados = leer(claveTabla(tabla, fila));
    const claves = clavesColumnas(fila);
    const hay = claves.some((k) => guardados[k]);
    // Sin anchos guardados no se toca el colgroup de la página (salvo para deshacer lo que se fijó antes)
    if (!hay && !tabla.classList.contains('ca-fija')) return true;
    const cols = colgroupDe(tabla, fila.cells.length);
    soltarTabla(tabla, cols);
    if (!hay) return true;
    if (!tabla.offsetWidth) return false;
    const naturales = [...fila.cells].map((th) => th.getBoundingClientRect().width);
    fijarTabla(tabla, cols, naturales.map((w, i) => Math.max(MIN, Math.round(guardados[claves[i]] || w))));
    return true;
  }

  function prepararTabla(tabla) {
    if (tabla.closest('[data-sin-anchos]') || tabla.dataset.anchos) return;
    if (tabla.parentElement && tabla.parentElement.closest('table')) return;
    const fila = filaClave(tabla);
    if (!fila) return;
    let nuevos = false;
    [...fila.cells].forEach((th, i) => {
      th.classList.add('ca-th');
      const asa = [...th.children].find((c) => c.classList.contains(ASA));
      if (asa) { asa.dataset.col = i; return; }
      const s = document.createElement('span');
      s.className = ASA;
      s.dataset.col = i;
      s.tabIndex = 0;
      s.setAttribute('role', 'separator');
      s.setAttribute('aria-orientation', 'vertical');
      s.setAttribute('aria-label', `Ancho de la columna ${texto(th) || i + 1}`);
      s.title = 'Arrastrá para cambiar el ancho · doble clic restablece';
      th.appendChild(s);
      nuevos = true;
    });
    // Reaplicar si cambió el encabezado o el colgroup (la página volvió a dibujar la tabla)
    const firma = fila.cells.length + '|' + [...fila.cells].map(texto).join('|');
    const cg = [...tabla.children].find((c) => c.tagName === 'COLGROUP');
    const e = estado(tabla);
    if (nuevos || e.firma !== firma || e.cg !== cg || e.pendiente) {
      e.firma = firma;
      e.pendiente = !aplicar(tabla);
      e.cg = [...tabla.children].find((c) => c.tagName === 'COLGROUP');
      if (e.pendiente) visibles.observe(tabla);
    }
  }

  const visibles = 'IntersectionObserver' in window
    ? new IntersectionObserver((entradas) => entradas.forEach((en) => {
        if (!en.isIntersecting) return;
        const e = estado(en.target);
        e.pendiente = !aplicar(en.target);
        if (!e.pendiente) visibles.unobserve(en.target);
      }))
    : { observe() {}, unobserve() {} };

  function escanear() { document.querySelectorAll('table').forEach(prepararTabla); }

  // Cambia el ancho de una columna (la tabla queda con anchos fijos) y opcionalmente lo guarda
  function cambiarAncho(asa, px, guardar) {
    const th = asa.parentElement, tabla = th.closest('table');
    const fila = filaClave(tabla);
    if (!fila) return;
    const i = Number(asa.dataset.col);
    const cols = colgroupDe(tabla, fila.cells.length);
    if (!tabla.classList.contains('ca-fija')) {
      fijarTabla(tabla, cols, [...fila.cells].map((c) => Math.max(MIN, Math.round(c.getBoundingClientRect().width))));
    }
    const w = Math.max(MIN, Math.round(px));
    const anchos = cols.map((c) => parseFloat(c.style.width) || 0);
    anchos[i] = w;
    fijarTabla(tabla, cols, anchos);
    if (guardar) {
      const clave = claveTabla(tabla, fila);
      escribir(clave, { ...leer(clave), [clavesColumnas(fila)[i]]: w });
    }
  }

  const asaDe = (e) => (e.target && e.target.closest ? e.target.closest('.' + ASA) : null);

  document.addEventListener('pointerdown', (e) => {
    const asa = asaDe(e);
    if (!asa || e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    const x0 = e.clientX, w0 = asa.parentElement.getBoundingClientRect().width;
    let ultimo = w0;
    asa.classList.add('is-drag');
    document.body.classList.add('ca-arrastrando');
    try { asa.setPointerCapture(e.pointerId); } catch (err) { /* nada */ }
    const mover = (ev) => { ultimo = w0 + ev.clientX - x0; cambiarAncho(asa, ultimo, false); };
    const soltar = () => {
      asa.removeEventListener('pointermove', mover);
      asa.removeEventListener('pointerup', soltar);
      asa.removeEventListener('pointercancel', soltar);
      asa.classList.remove('is-drag');
      document.body.classList.remove('ca-arrastrando');
      if (Math.abs(ultimo - w0) >= 1) cambiarAncho(asa, ultimo, true);
    };
    asa.addEventListener('pointermove', mover);
    asa.addEventListener('pointerup', soltar);
    asa.addEventListener('pointercancel', soltar);
  }, true);

  // El asa no dispara el orden por columna ni otros clics del encabezado
  ['click', 'mousedown', 'mouseup'].forEach((t) => document.addEventListener(t, (e) => { if (asaDe(e)) e.stopPropagation(); }, true));

  document.addEventListener('dblclick', (e) => {
    const asa = asaDe(e);
    if (!asa) return;
    e.preventDefault();
    e.stopPropagation();
    const tabla = asa.closest('table'), fila = filaClave(tabla);
    if (!fila) return;
    const clave = claveTabla(tabla, fila), g = leer(clave);
    delete g[clavesColumnas(fila)[Number(asa.dataset.col)]];
    escribir(clave, g);
    aplicar(tabla);
  }, true);

  document.addEventListener('keydown', (e) => {
    const asa = asaDe(e);
    if (!asa || (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight')) return;
    e.preventDefault();
    e.stopPropagation();
    const paso = (e.shiftKey ? 64 : 16) * (e.key === 'ArrowRight' ? 1 : -1);
    cambiarAncho(asa, asa.parentElement.getBoundingClientRect().width + paso, true);
  }, true);

  // Las páginas dibujan sus tablas dinámicamente: volver a escanear cuando cambia el DOM
  let timer = null;
  const propia = (n) => n.nodeType === 1 && (n.classList.contains(ASA) || n.tagName === 'COL' || n.tagName === 'COLGROUP');
  new MutationObserver((ms) => {
    if (ms.every((m) => [...m.addedNodes, ...m.removedNodes].every(propia))) return;
    clearTimeout(timer);
    timer = setTimeout(escanear, 60);
  }).observe(document.documentElement, { childList: true, subtree: true });

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', escanear);
  else escanear();
})();
