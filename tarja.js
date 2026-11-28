/* Operaciones › Tarja General (Registro Diario).
 * Grilla operador × día: cada celda lleva el ID de un centro de costo o un código de estado. Las horas, los cambios de turno
 * y la columna Obra los calcula el servidor (tarja.py). Carga rápida (escribir, seleccionar/arrastrar un rango, copiar/pegar),
 * autofiltros tipo Excel en cada columna, panel de horas por día, configuración y exportación a Excel.
 * Usa los componentes del design system (/ui/app.css: .sheet-grid, .modal, .menu) y showToast / opPuede de operaciones.html.
 */
(() => {
  'use strict';
  const API = '/api/tarja';
  const PANEL = 'tarja_general';
  const MESES = ['Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio', 'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre'];
  const INI = 'LMMJVSD';
  const CATS = { trabajado: 'Trabajado (por obra)', cambio: 'Cambio de turno', descanso: 'Descanso', vacaciones: 'Vacaciones', ausencia: 'Ausencia / licencia', otro: 'Otro estado', vacia: '(Sin color)' };
  const TOTALES = [['trabajados', 'Trab.', 'Días trabajados'], ['cambios_turno', 'C.T.', 'Días de cambio de turno'], ['descansos', 'Desc.', 'Descansos'],
    ['vacaciones', 'Vac.', 'Vacaciones'], ['ausencias', 'Aus.', 'Ausencias'], ['horas_efectivas', 'Hs ef.', 'Horas efectivas'], ['horas_break', 'Hs br.', 'Horas de break']];
  const OPS_TXT = [['eq', 'Es igual a'], ['ne', 'No es igual a'], ['starts', 'Comienza con'], ['ends', 'Termina con'], ['contains', 'Contiene'], ['notcontains', 'No contiene']];
  // Obra (hoy) sin centro de costo: hoy sin carga y sin ningún centro trabajado antes. Valor '-' en el filtro de arriba.
  const SIN_CC = '(Sin centro de costo)';
  const ESTADOS_HOY = [['', 'Todos los estados de hoy'], ['obra', 'En obra'], ['descanso', 'En descanso'], ['otro', 'Otro estado (vacaciones, licencia…)'], ['pendiente', 'Pendiente (hoy sin cargar)']];
  const OPS_NUM = [['neq', 'Igual a'], ['gt', 'Mayor que'], ['lt', 'Menor que'], ['between', 'Entre'], ['top', '10 mejores']];

  const T = {
    op: null, g: null, desde: '', hasta: '', cargado: false, ocupado: false,
    filtros: {}, orden: null, rapidos: { cc: '', estado: '', regimen: '', q: '' },
    visibles: [], rects: [], act: null, arrastrando: false, editor: null, timerPref: null,
    mostrarTot: false,  // columnas de totales ocultas hasta que el usuario elija mostrarlas (se guarda por usuario)
  };

  // ---------------------------------------------------------------- utilidades
  const $ = (id) => document.getElementById(id);
  const e = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const puede = (accion) => (typeof opPuede === 'function' ? opPuede(PANEL, accion) : true);
  const toast = (msg, tipo = 'info') => (typeof showToast === 'function' ? showToast(e(msg), tipo) : alert(msg));
  const nf = (v) => Number(v || 0).toLocaleString('es-AR', { maximumFractionDigits: 2 });
  const fFecha = (iso) => (iso ? `${iso.slice(8, 10)}/${iso.slice(5, 7)}/${iso.slice(0, 4)}` : '');
  const MES_CORTO = ['ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sep', 'oct', 'nov', 'dic'];
  const MAX_DIAS = 186;
  // Fechas ISO sin zona horaria (evita corrimientos de un día).
  const iso = (a, m, d) => `${a}-${String(m).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
  const finDeMes = (a, m) => new Date(Date.UTC(a, m, 0)).getUTCDate();
  const partes = (f) => [Number(f.slice(0, 4)), Number(f.slice(5, 7)), Number(f.slice(8, 10))];
  const utc = (f) => { const [a, m, d] = partes(f); return Date.UTC(a, m - 1, d); };
  const diasEntre = (a, b) => Math.round((utc(b) - utc(a)) / 86400000);
  const sumarMeses = (f, n) => {
    const [a, m, d] = partes(f); const t = a * 12 + (m - 1) + n; const na = Math.floor(t / 12); const nm = (t % 12) + 1;
    return iso(na, nm, Math.min(d, finDeMes(na, nm)));
  };
  const mesCompleto = (desde, hasta) => {
    const [a, m, d] = partes(desde); return d === 1 && hasta === iso(a, m, finDeMes(a, m));
  };
  const rangoMes = (a, m) => [iso(a, m, 1), iso(a, m, finDeMes(a, m))];
  const root = () => $('tj-root');
  const ic = (n, c = '') => `<svg class="i ${c}" aria-hidden="true"><use href="#i-${n}"/></svg>`;
  const opts = (lista, sel) => lista.map(([v, t]) => `<option value="${e(v)}" ${String(v) === String(sel ?? '') ? 'selected' : ''}>${e(t)}</option>`).join('');

  async function api(url, metodo = 'GET', cuerpo) {
    const op = { method: metodo, headers: {} };
    if (cuerpo !== undefined) { op.headers['Content-Type'] = 'application/json'; op.body = JSON.stringify(cuerpo); }
    let res;
    try { res = await fetch(url, op); } catch (err) { throw new Error('No se pudo conectar con el servidor.'); }
    let data = null;
    try { data = await res.json(); } catch (err) { data = null; }
    if (!res.ok) {
      const d = data && data.detail;
      const err = new Error(typeof d === 'string' ? d : (Array.isArray(d) ? d.map((x) => x.msg).join('; ') : `Error ${res.status} del servidor.`));
      err.errores = data && data.errores;
      throw err;
    }
    return data;
  }
  async function ejecutar(fn, okMsg) {
    if (T.ocupado) return false;
    T.ocupado = true;
    document.body.style.cursor = 'progress';
    try { await fn(); if (okMsg) toast(okMsg, 'success'); return true; } catch (err) {
      toast(err.message, 'error');
      if (err.errores && err.errores.length > 1) console.warn('Tarja: celdas rechazadas', err.errores);
      return false;
    } finally { T.ocupado = false; document.body.style.cursor = ''; }
  }

  // ---------------------------------------------------------------- catálogos
  const codigo = (c) => (T.op.codigos || []).find((x) => x.codigo === c);
  const centro = (id) => (T.op.centros_costos || []).find((x) => x.id === Number(id));
  const paleta = (id) => { const c = centro(id); return c ? c.color : '#e2e8f0'; };
  const descCodigo = (c) => {
    if (c === '' || c === undefined || c === null) return '(Vacías)';
    if (/^\d+$/.test(String(c))) { const cc = centro(c); return cc ? cc.nombre : 'centro de costo inexistente'; }
    const k = codigo(c); return k ? k.descripcion : '';
  };
  const textoOscuro = (hex) => {
    const h = String(hex || '#ffffff').replace('#', '');
    const [r, g, b] = [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16) / 255);
    return 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.5;
  };
  const catDe = (cel) => {
    if (!cel) return 'vacia';
    if (cel.ct) return 'cambio';
    if (!cel.e) return 'trabajado';
    const k = codigo(cel.c); return k ? k.categoria.toLowerCase() : 'otro';
  };
  // Obra (hoy) se filtra por el código del centro de costo: el de hoy o, si hoy está de descanso, con otro estado o sin
  // cargar, el último trabajado. El estado de hoy (en obra / descanso / ...) es un filtro aparte.
  const ccHoy = (o) => (o.obra.cc_id === null || o.obra.cc_id === undefined || o.obra.cc_id === '' ? '' : Number(o.obra.cc_id));
  const etiquetaCC = (id) => (id === '' ? SIN_CC : `${id} - ${descCodigo(id)}`);
  const estadoHoy = (o) => {
    if (o.obra.pendiente) return 'pendiente';
    if (!o.obra.estado) return 'obra';
    const k = codigo(o.obra.estado); return k && k.categoria === 'DESCANSO' ? 'descanso' : 'otro';
  };
  const colorDe = (cel) => (!cel ? null : (cel.e ? ((codigo(cel.c) || {}).color || '#cbd5e1') : paleta(cel.c)));

  // ---------------------------------------------------------------- estilos propios de la grilla
  // Los estilos son los del design system (app.css › .sheet-grid, .modal, .menu). Solo se pasa el color configurado
  // para el cambio de turno.
  function estilos() {
    const r = root();
    if (r && T.op) r.style.setProperty('--sg-shift', T.op.color_cambio_turno);
  }

  // ---------------------------------------------------------------- modal (componente .modal del design system)
  function modal(titulo, cuerpo, pie = '', ancho = 'sm') {
    let bg = $('tj-modal');
    if (!bg) {
      bg = document.createElement('div');
      bg.id = 'tj-modal';
      bg.className = 'modal is-toggle encima';
      bg.setAttribute('translate', 'no');
      document.body.appendChild(bg);
      bg.addEventListener('click', (ev) => { if (ev.target === bg || ev.target.closest('[data-tj-cerrar]')) cerrarModal(); });
    }
    bg.innerHTML = `<div class="modal-panel ${ancho}" role="dialog" aria-modal="true"><div class="modal-head"><h3>${titulo}</h3><span class="spacer"></span><button class="btn btn-ghost btn-icon" type="button" data-tj-cerrar aria-label="Cerrar">${ic('x')}</button></div>
      <div class="modal-body ctl-auto" id="tj-modal-b">${cuerpo}</div>${pie ? `<div class="modal-foot">${pie}</div>` : ''}</div>`;
    bg.classList.add('open');
    return bg;
  }
  function cerrarModal() { const bg = $('tj-modal'); if (bg) bg.classList.remove('open'); }
  const modalAbierto = () => { const bg = $('tj-modal'); return !!(bg && bg.classList.contains('open')); };

  // ---------------------------------------------------------------- columnas (para filtros y orden)
  function columnas() {
    const cols = [
      { key: 'legajo', label: 'Legajo', num: true, valor: (o) => (/^\d+$/.test(o.legajo) ? Number(o.legajo) : o.legajo) },
      { key: 'nombre', label: 'Apellido y Nombre', valor: (o) => o.nombre || '' },
      { key: 'obra', label: 'Obra (hoy)', num: true, ambos: true, valor: ccHoy, etiqueta: etiquetaCC, vacias: SIN_CC,
        texto: (o) => { const v = ccHoy(o); return v === '' ? '' : etiquetaCC(v); } },
    ];
    // Clave por posición en el período (d1 = primera columna): el filtro se conserva al cambiar de período.
    (T.g ? T.g.dias : []).forEach((d, i) => cols.push({
      key: `d${i + 1}`, label: `Día ${fFecha(d.fecha).slice(0, 5)}`, dia: d, mixto: true,
      valor: (o) => { const c = o.dias[d.fecha]; return c ? (/^\d+$/.test(c.c) ? Number(c.c) : c.c) : ''; },
      color: (o) => catDe(o.dias[d.fecha]),
    }));
    TOTALES.forEach(([k, , l]) => cols.push({ key: k, label: l, num: true, tot: true, valor: (o) => o.totales[k] }));
    return cols;
  }
  const colPorKey = (k) => columnas().find((c) => c.key === k);

  function cumpleCond(v, cond, col, todos, txt = v) {
    const op = cond.op;
    if (['eq', 'ne', 'starts', 'ends', 'contains', 'notcontains'].includes(op)) {
      const s = String(txt ?? '').toLowerCase(); const a = String(cond.a ?? '').toLowerCase();
      return { eq: s === a, ne: s !== a, starts: s.startsWith(a), ends: s.endsWith(a), contains: s.includes(a), notcontains: !s.includes(a) }[op];
    }
    if (typeof v !== 'number') return false;
    const a = Number(String(cond.a ?? '').replace(',', '.')); const b = Number(String(cond.b ?? '').replace(',', '.'));
    if (op === 'neq') return v === a;
    if (op === 'gt') return v > a;
    if (op === 'lt') return v < a;
    if (op === 'between') return v >= Math.min(a, b) && v <= Math.max(a, b);
    if (op === 'top') {
      const n = Math.max(1, Math.floor(a) || 10);
      const nums = todos.map((o) => col.valor(o)).filter((x) => typeof x === 'number').sort((x, y) => y - x);
      return nums.length ? v >= nums[Math.min(n, nums.length) - 1] : false;
    }
    return true;
  }
  function pasaColumna(o, key, f, todos) {
    const col = colPorKey(key);
    if (!col || !f) return true;
    const v = col.valor(o);
    if (f.vals && !f.vals.includes(String(v ?? ''))) return false;
    if (f.cond && f.cond.op && !cumpleCond(v, f.cond, col, todos, col.texto ? col.texto(o) : v)) return false;
    if (f.color && f.color.length && !f.color.includes(col.color ? col.color(o) : null)) return false;
    return true;
  }
  function pasaRapidos(o) {
    const r = T.rapidos;
    if (r.regimen && o.regimen !== r.regimen) return false;
    // Mismo criterio que la columna Obra (hoy): así los dos filtros dan el mismo resultado para el mismo centro.
    if (r.cc && String(ccHoy(o)) !== (r.cc === '-' ? '' : String(r.cc))) return false;
    if (r.estado && estadoHoy(o) !== r.estado) return false;
    if (r.q) {
      const q = r.q.toLowerCase().trim();
      if (q && !o.legajo.toLowerCase().includes(q) && !(o.nombre || '').toLowerCase().includes(q)) return false;
    }
    return true;
  }
  function filtrar(excepto) {
    const todos = T.g.operadores;
    let lista = todos.filter((o) => pasaRapidos(o) && Object.entries(T.filtros).every(([k, f]) => k === excepto || pasaColumna(o, k, f, todos)));
    if (T.orden && excepto === undefined) {
      const col = colPorKey(T.orden.key);
      if (col) {
        const s = T.orden.dir === 'desc' ? -1 : 1;
        lista = lista.slice().sort((a, b) => {
          const va = col.valor(a); const vb = col.valor(b);
          if (va === '' && vb !== '') return 1;
          if (vb === '' && va !== '') return -1;
          if (typeof va === 'number' && typeof vb === 'number') return (va - vb) * s;
          return String(va).localeCompare(String(vb), 'es', { numeric: true }) * s;
        });
      }
    }
    return lista;
  }
  const filtroActivo = (k) => { const f = T.filtros[k]; return !!(f && (f.vals || (f.cond && f.cond.op) || (f.color && f.color.length))); };

  // ---------------------------------------------------------------- preferencias por usuario
  function guardarPrefs() {
    clearTimeout(T.timerPref);
    T.timerPref = setTimeout(() => {
      api(`${API}/preferencias`, 'PUT', { filtros: T.filtros, orden: T.orden, rapidos: T.rapidos, periodo: { desde: T.desde, hasta: T.hasta }, mostrarTotales: T.mostrarTot }).catch(() => {});
    }, 700);
  }

  // ---------------------------------------------------------------- pantalla
  async function abrir() {
    estilos();
    if (!T.cargado) {
      root().innerHTML = '<section class="card"><div class="empty-state">Cargando la tarja…</div></section>';
      try {
        const [op, prefs] = await Promise.all([api(`${API}/opciones`), api(`${API}/preferencias`).catch(() => ({}))]);
        T.op = op;
        const h = op.hoy;
        const per = prefs && prefs.periodo;
        const fechaOk = (f) => /^\d{4}-\d{2}-\d{2}$/.test(f || '');
        // El período elegido queda establecido para el usuario; si no hay uno guardado, el mes actual.
        if (per && fechaOk(per.desde) && fechaOk(per.hasta) && per.desde <= per.hasta) {
          T.desde = per.desde; T.hasta = per.hasta;
        } else {
          [T.desde, T.hasta] = rangoMes(Number(h.slice(0, 4)), Number(h.slice(5, 7)));
        }
        T.filtros = (prefs && prefs.filtros) || {};
        // Filtros guardados antes de que Obra (hoy) filtrara por código: tenían el texto de la celda y ya no coinciden.
        const fo = T.filtros.obra;
        if (fo && fo.vals && fo.vals.some((v) => v !== '' && !/^\d+$/.test(v))) { delete fo.vals; if (!Object.keys(fo).length) delete T.filtros.obra; }
        T.orden = (prefs && prefs.orden) || null;
        T.rapidos = { cc: '', estado: '', regimen: '', q: '', ...((prefs && prefs.rapidos) || {}) };
        T.mostrarTot = !!(prefs && prefs.mostrarTotales);
        T.cargado = true;
        estilos(); const st = $('tj-estilos'); if (st) { st.remove(); estilos(); }
      } catch (err) { root().innerHTML = `<section class="card"><div class="empty-state is-danger-text">${e(err.message)}</div></section>`; return; }
    }
    await cargarMes();
  }

  async function cargarMes() {
    try {
      T.g = await api(`${API}/grilla?desde=${T.desde}&hasta=${T.hasta}`);
    } catch (err) { toast(err.message, 'error'); return; }
    T.rects = []; T.act = null;
    render();
  }

  function render() {
    const r = T.rapidos;
    const [aD, mD] = partes(T.desde);
    const anios = []; for (let a = 2025; a <= Number(T.op.hoy.slice(0, 4)) + 2; a += 1) anios.push([a, a]);
    if (!anios.some(([a]) => a === aD)) anios.push([aD, aD]);
    const completo = mesCompleto(T.desde, T.hasta);
    // Acciones del encabezado de la página (fuera de la grilla): Exportar ▾ y Configuración.
    $('tj-head-acciones').innerHTML = `
      <button type="button" class="btn" id="tj-exp" aria-haspopup="menu" aria-expanded="false">${ic('download')}Exportar${ic('chevron-down', 'i-sm')}</button>
      ${puede('configurar') ? `<button type="button" class="btn btn-icon" id="tj-conf" title="Configuración de la tarja" aria-label="Configuración de la tarja">${ic('settings')}</button>` : ''}
      <div class="menu" id="tj-exp-menu" role="menu" hidden>
        <button type="button" class="menu-item" role="menuitem" id="tj-exp-f" data-cerrar-menu>${ic('filter')}Filtrado (lo que se ve en pantalla)</button>
        <button type="button" class="menu-item" role="menuitem" id="tj-exp-t" data-cerrar-menu>${ic('calendar-days')}Todo el período</button>
      </div>`;
    const ayuda = puede('cargar')
      ? ['Clic o arrastre: seleccionar celdas (Ctrl agrega, Shift extiende)', 'Escriba un código o centro de costo y Enter para cargar', 'Supr: borra la selección · F2: edita la celda',
        'Ctrl+C / Ctrl+V: copiar y pegar rangos', 'Doble clic: horas del día y horas a mano', 'Flechas, Enter y Tab: moverse por la grilla']
      : ['Solo consulta: su usuario no tiene habilitada la carga.', 'Doble clic en un día: ver horas e historial'];
    root().innerHTML = `
      <section class="card" aria-label="Grilla de la tarja">
        <div class="toolbar">
          <button type="button" class="btn btn-icon btn-sm" id="tj-prev" title="Correr el período un mes atrás" aria-label="Período anterior">${ic('left', 'i-sm')}</button>
          <select class="chip-select" id="tj-mes" aria-label="Mes">${completo ? '' : '<option value="" selected>Período personalizado</option>'}${opts(MESES.map((m, i) => [i + 1, m]), completo ? mD : '')}</select>
          <select class="chip-select" id="tj-anio" aria-label="Año">${opts(anios, aD)}</select>
          <button type="button" class="btn btn-icon btn-sm" id="tj-next" title="Correr el período un mes adelante" aria-label="Período siguiente">${ic('right', 'i-sm')}</button>
          <button type="button" class="btn btn-sm" id="tj-hoy" title="Mes actual">Hoy</button>
          <span class="range"><input type="date" class="input input-sm" id="tj-desde" value="${T.desde}" aria-label="Desde">–<input type="date" class="input input-sm" id="tj-hasta" value="${T.hasta}" aria-label="Hasta">
            <button type="button" class="btn btn-sm" id="tj-aplicar">Aplicar</button></span>
          <select class="chip-select ${r.cc ? 'on' : ''}" id="tj-f-cc" aria-label="Obra (hoy): centro de costo" title="Obra (hoy): centro de costo de hoy o, si hoy no está en obra, el último trabajado">${opts([['', 'Todos los centros de costo']].concat(T.op.centros_costos.slice().sort((a, b) => a.id - b.id).map((c) => [c.id, `${c.id} - ${c.nombre}`]), [['-', SIN_CC]]), r.cc)}</select>
          <select class="chip-select ${r.estado ? 'on' : ''}" id="tj-f-est" aria-label="Estado hoy">${opts(ESTADOS_HOY, r.estado)}</select>
          <select class="chip-select ${r.regimen ? 'on' : ''}" id="tj-f-reg" aria-label="Régimen de turno">${opts([['', 'Todos los regímenes'], ['Rotativo', 'Rotativo'], ['Diario', 'Diario']], r.regimen)}</select>
          <div class="search">${ic('search')}<input type="search" class="input" id="tj-f-q" value="${e(r.q)}" placeholder="Legajo o nombre" aria-label="Buscar legajo o nombre" autocomplete="off"></div>
          <button type="button" class="btn btn-sm" id="tj-tot"></button>
          <button type="button" class="btn btn-ghost btn-sm" id="tj-borrar-todo">Borrar filtros</button>
          <span class="spacer"></span>
          <span class="muted num" id="tj-cuenta"></span>
          <button type="button" class="btn btn-ghost btn-icon btn-sm" id="tj-ayuda" aria-haspopup="dialog" aria-expanded="false" title="Atajos de teclado y mouse" aria-label="Atajos de teclado y mouse">${ic('help', 'i-sm')}</button>
          <div class="menu pop" id="tj-ayuda-pop" role="dialog" aria-label="Atajos" hidden>
            <div class="menu-head">${puede('cargar') ? 'Carga rápida' : 'Solo consulta'}</div>
            <ul class="list-plain help" style="padding: 0 12px 8px">${ayuda.map((t) => `<li>${t}</li>`).join('')}</ul>
          </div>
        </div>
        <div class="toolbar" style="padding-top: 0">
          <details class="legend-codes"><summary>Ver códigos${ic('chevron-down', 'i-sm')}</summary><div class="chip-row" id="tj-ley"></div></details>
        </div>
        <div class="sheet-wrap tj-scroll" id="tj-scroll"></div>
      </section>
      <section class="card tj-resumen">
        <div class="card-head"><div><h3 class="card-title">Resumen por centro de costo del ${fFecha(T.desde)} al ${fFecha(T.hasta)}</h3><p class="card-sub">Operadores mostrados en la grilla</p></div></div>
        <div class="card-body" id="tj-res-cc"></div>
      </section>`;
    $('tj-exp').addEventListener('click', (ev) => SMG.abrirMenu($('tj-exp-menu'), ev.currentTarget));
    $('tj-ayuda').addEventListener('click', (ev) => SMG.abrirMenu($('tj-ayuda-pop'), ev.currentTarget, { foco: false }));
    $('tj-prev').addEventListener('click', () => mover(-1));
    $('tj-next').addEventListener('click', () => mover(1));
    $('tj-hoy').addEventListener('click', () => fijarPeriodo(...rangoMes(Number(T.op.hoy.slice(0, 4)), Number(T.op.hoy.slice(5, 7)))));
    const porMes = () => { const m = Number($('tj-mes').value); if (m) fijarPeriodo(...rangoMes(Number($('tj-anio').value), m)); };
    $('tj-mes').addEventListener('change', porMes);
    $('tj-anio').addEventListener('change', porMes);
    $('tj-aplicar').addEventListener('click', () => fijarPeriodo($('tj-desde').value, $('tj-hasta').value));
    ['tj-desde', 'tj-hasta'].forEach((id) => $(id).addEventListener('keydown', (ev) => { if (ev.key === 'Enter') $('tj-aplicar').click(); }));
    $('tj-f-cc').addEventListener('change', (ev) => { T.rapidos.cc = ev.target.value; aplicarFiltros(); });
    $('tj-f-est').addEventListener('change', (ev) => { T.rapidos.estado = ev.target.value; aplicarFiltros(); });
    $('tj-f-reg').addEventListener('change', (ev) => { T.rapidos.regimen = ev.target.value; aplicarFiltros(); });
    let tq = null;
    $('tj-f-q').addEventListener('input', (ev) => { clearTimeout(tq); tq = setTimeout(() => { T.rapidos.q = ev.target.value; aplicarFiltros(); }, 200); });
    $('tj-tot').addEventListener('click', () => { T.mostrarTot = !T.mostrarTot; renderGrilla(); guardarPrefs(); });
    $('tj-borrar-todo').addEventListener('click', () => { T.filtros = {}; T.orden = null; T.rapidos = { cc: '', estado: '', regimen: '', q: '' }; render(); guardarPrefs(); });
    $('tj-exp-f').addEventListener('click', () => exportar(true));
    $('tj-exp-t').addEventListener('click', () => exportar(false));
    if ($('tj-conf')) $('tj-conf').addEventListener('click', () => configuracion('jornada'));
    leyenda();
    aplicarFiltros(false);
  }

  function fijarPeriodo(desde, hasta) {
    const ok = (f) => /^\d{4}-\d{2}-\d{2}$/.test(f || '');
    if (!ok(desde) || !ok(hasta)) { toast('Complete las fechas Desde y Hasta.', 'error'); return; }
    if (hasta < desde) { toast('La fecha Hasta es anterior a Desde.', 'error'); return; }
    if (diasEntre(desde, hasta) + 1 > MAX_DIAS) { toast(`El período no puede superar ${MAX_DIAS} días.`, 'error'); return; }
    T.desde = desde; T.hasta = hasta;
    guardarPrefs();
    cargarMes();
  }
  // ◀ ▶ corren el período un mes (20/10–20/12 → 20/11–20/01); un mes completo pasa al mes completo siguiente.
  function mover(delta) {
    if (mesCompleto(T.desde, T.hasta)) { const [a, m] = partes(sumarMeses(T.desde, delta)); fijarPeriodo(...rangoMes(a, m)); return; }
    fijarPeriodo(sumarMeses(T.desde, delta), sumarMeses(T.hasta, delta));
  }

  function leyenda() {
    const k = T.op.codigos.filter((c) => c.activo);
    const cc0 = T.op.centros_costos[0] ? T.op.centros_costos[0].color : '#3B82F6';
    $('tj-ley').innerHTML = `<span class="chip"><i class="swatch" style="--cell:${e(cc0)}"></i>Centro de costo (color por obra)</span>
      <span class="chip"><i class="swatch" style="--cell:${e(T.op.color_cambio_turno)}"></i>Cambio de turno (línea inferior)</span>
      <span class="chip"><i class="swatch" style="--cell:var(--danger)"></i>Horas a mano (esquina)</span>
      ${k.map((c) => `<span class="chip" title="${e(c.descripcion)}"><i class="swatch" style="--cell:${e(c.color)}"></i><b>${e(c.codigo)}</b>${e(c.descripcion)}</span>`).join('')}`;
  }

  function aplicarFiltros(guardar = true) {
    T.visibles = filtrar();
    T.rects = []; T.act = null;
    renderGrilla();
    if (guardar) guardarPrefs();
  }

  const ICONO_F = '<svg viewBox="0 0 10 10"><path d="M0 1h10L6 5.5V10L4 8.8V5.5z"/></svg>';
  const ICONO_V = '<svg viewBox="0 0 10 10"><path d="M1 3h8L5 8z"/></svg>';
  const fbtn = (k) => `<button type="button" class="tj-fbtn sg-filter ${filtroActivo(k) || (T.orden && T.orden.key === k) ? 'on' : ''}" data-tj-f="${k}" title="Filtrar y ordenar">${filtroActivo(k) ? ICONO_F : ICONO_V}</button>`;

  function renderGrilla() {
    const g = T.g; const hoy = g.hoy;
    const cab = `<tr><th class="fx fx1 sg-fix sg-fix-1"><div class="tj-h sg-head">Legajo ${fbtn('legajo')}</div></th><th class="fx fx2 sg-fix sg-fix-2"><div class="tj-h sg-head">Apellido y nombre ${fbtn('nombre')}</div></th>
      <th class="fx fx3 sg-fix sg-fix-3"><div class="tj-h sg-head">Obra (hoy) ${fbtn('obra')}</div></th>
      ${g.dias.map((d, i) => `<th class="dia sg-day ${d.fecha === hoy ? 'hoy sg-today' : ''} ${d.feriado ? 'fer sg-holiday' : (d.dow >= 5 ? 'fin sg-weekend' : '')} ${d.dia === 1 && i > 0 ? 'm1 sg-month-start' : ''}" title="${fFecha(d.fecha)}${d.feriado ? ` · Feriado: ${e(d.feriado)}` : ''}">
        <div class="tj-h sg-head">${i === 0 || d.dia === 1 ? `<em class="tj-mes sg-month">${MES_CORTO[d.mes - 1]}</em>` : ''}<b>${d.dia}</b><small>${INI[d.dow]}</small>${fbtn(`d${i + 1}`)}</div></th>`).join('')}
      ${T.mostrarTot ? TOTALES.map(([k, c, l]) => `<th class="tot sg-total" title="${l}"><div class="tj-h sg-head">${c} ${fbtn(k)}</div></th>`).join('') : ''}</tr>`;
    const filas = T.visibles.map((o, ri) => {
      const obra = o.obra.pendiente
        ? `<span class="chip chip-warn sg-tag tj-pend" title="Pendiente: hoy sin cargar${o.obra.cc_id ? ` (último centro: ${o.obra.cc_id} - ${e(descCodigo(o.obra.cc_id))})` : ''}">P</span><span class="muted tj-est">Pendiente${o.obra.cc_id ? ` · ${e(o.obra.cc_id)}` : ''}</span>`
        // Con un estado hoy (D, V, ...): badge con el código + el centro de costo (el sufijo largo se cortaba por el ancho).
        : (o.obra.estado ? `<span class="chip chip-muted sg-tag tj-pend" title="Hoy: ${e(descCodigo(o.obra.estado))}">${e(o.obra.estado)}</span><span class="muted tj-est">${e(ccHoy(o) === '' ? '—' : etiquetaCC(ccHoy(o)))}</span>` : e(o.obra.texto));
      const dias = g.dias.map((d, ci) => {
        const cel = o.dias[d.fecha];
        let cls = `d sg-cell${d.fecha === hoy ? ' hoyc sg-today' : ''}${d.dow >= 5 || d.feriado ? ' fin sg-weekend' : ''}${d.dia === 1 && ci > 0 ? ' m1 sg-month-start' : ''}`;
        if (!cel) return `<td class="${cls}" data-r="${ri}" data-c="${ci}"></td>`;
        if (cel.ct) cls += ' ct is-shift';
        if (cel.m) cls += ' man is-manual';
        const bg = colorDe(cel);
        const tip = `${cel.c} - ${descCodigo(cel.c)}\n${nf(cel.h)} h efectivas + ${nf(cel.b)} h break${cel.ct ? '\nCambio de turno (subida / bajada)' : ''}${cel.m ? '\nHoras editadas a mano' : ''}${cel.o ? `\nObs.: ${cel.o}` : ''}`;
        return `<td class="${cls}" data-r="${ri}" data-c="${ci}" style="--cell:${e(bg)}" title="${e(tip)}">${e(cel.c)}</td>`;
      }).join('');
      return `<tr><td class="fx fx1 sg-fix sg-fix-1">${e(o.legajo)}</td><td class="fx fx2 sg-fix sg-fix-2 sg-link" data-tj-op="${e(o.legajo)}" title="${e(o.nombre)} · Régimen ${e(o.regimen)}"><span class="chip ${o.regimen === 'Diario' ? 'chip-accent' : 'chip-muted'} sg-tag tj-reg">${o.regimen === 'Diario' ? 'DIA' : 'ROT'}</span>${e(o.nombre)}</td>
        <td class="fx fx3 sg-fix sg-fix-3" title="${e(o.obra.pendiente ? 'Pendiente: hoy sin cargar' : o.obra.texto)}">${obra}</td>${dias}
        ${T.mostrarTot ? TOTALES.map(([k]) => `<td class="tot sg-total">${nf(o.totales[k])}</td>`).join('') : ''}</tr>`;
    }).join('');
    $('tj-scroll').innerHTML = `<table class="tj-t sheet-grid"><thead>${cab}</thead><tbody>${filas || `<tr><td class="fx fx1 sg-fix sg-fix-1"></td><td class="fx fx2 sg-fix sg-fix-2 muted">Sin operadores para los filtros elegidos.</td><td class="fx fx3 sg-fix sg-fix-3"></td></tr>`}</tbody></table>`;
    $('tj-cuenta').textContent = `Mostrando ${T.visibles.length} de ${g.operadores.length} operadores`;
    const [aP, mP] = partes(T.desde);
    $('tj-sub').textContent = `${g.operadores.length} operadores · ${mesCompleto(T.desde, T.hasta) ? `${MESES[mP - 1]} ${aP}` : `${fFecha(T.desde)} al ${fFecha(T.hasta)}`}`;
    // Un filtro u orden sobre una columna de totales oculta sigue aplicado: el botón lo avisa.
    const totFiltrado = TOTALES.some(([k]) => filtroActivo(k) || (T.orden && T.orden.key === k));
    $('tj-tot').textContent = T.mostrarTot ? 'Ocultar totales' : `Mostrar totales${totFiltrado ? ' (con filtro)' : ''}`;
    $('tj-tot').classList.toggle('btn-primary', !T.mostrarTot && totFiltrado);
    $('tj-tot').setAttribute('aria-pressed', String(T.mostrarTot));
    resumenCentros();
  }

  function resumenCentros() {
    const acc = new Map();
    T.visibles.forEach((o) => Object.values(o.dias).forEach((c) => {
      if (c.e) return;
      const r = acc.get(c.c) || { cc: c.c, ops: new Set(), dh: 0, h: 0, b: 0 };
      r.ops.add(o.legajo); r.dh += 1; r.h += c.h; r.b += c.b; acc.set(c.c, r);
    }));
    const lista = [...acc.values()].sort((a, b) => Number(a.cc) - Number(b.cc));
    const tot = lista.reduce((t, r) => ({ dh: t.dh + r.dh, h: t.h + r.h, b: t.b + r.b }), { dh: 0, h: 0, b: 0 });
    $('tj-res-cc').innerHTML = lista.length ? `<div class="table-scroll"><table class="tbl tbl-static tbl-auto"><thead><tr><th>Centro de costo</th><th>Descripción</th><th class="num">Operadores</th><th class="num">Días-hombre</th><th class="num">Hs efectivas</th><th class="num">Hs break</th></tr></thead><tbody>
      ${lista.map((r) => `<tr><td class="mono"><i class="swatch" style="--cell:${e(paleta(r.cc))};margin-right:6px"></i>${e(r.cc)}</td><td>${e(descCodigo(r.cc))}</td><td class="num">${r.ops.size}</td><td class="num">${r.dh}</td><td class="num">${nf(r.h)}</td><td class="num">${nf(r.b)}</td></tr>`).join('')}
      <tr class="tot"><td colspan="3">Total</td><td class="num">${tot.dh}</td><td class="num">${nf(tot.h)}</td><td class="num">${nf(tot.b)}</td></tr></tbody></table></div>`
      : '<p class="help">No hay días trabajados en el período para los operadores mostrados.</p>';
  }

  // ---------------------------------------------------------------- selección
  const celdaTd = (r, c) => $('tj-scroll').querySelector(`td.d[data-r="${r}"][data-c="${c}"]`);
  const enRect = (q, r, c) => r >= Math.min(q.r0, q.r1) && r <= Math.max(q.r0, q.r1) && c >= Math.min(q.c0, q.c1) && c <= Math.max(q.c0, q.c1);
  function pintarSeleccion() {
    const sc = $('tj-scroll'); if (!sc) return;
    sc.querySelectorAll('td.d.sel, td.d.act').forEach((td) => td.classList.remove('sel', 'act', 'is-selected', 'is-active'));
    T.rects.forEach((q) => {
      for (let r = Math.min(q.r0, q.r1); r <= Math.max(q.r0, q.r1); r += 1) {
        for (let c = Math.min(q.c0, q.c1); c <= Math.max(q.c0, q.c1); c += 1) { const td = celdaTd(r, c); if (td) td.classList.add('sel', 'is-selected'); }
      }
    });
    if (T.act) { const td = celdaTd(T.act.r, T.act.c); if (td) td.classList.add('act', 'is-active'); }
  }
  function celdasSeleccionadas() {
    const vistas = new Set(); const salida = [];
    T.rects.forEach((q) => {
      for (let r = Math.min(q.r0, q.r1); r <= Math.max(q.r0, q.r1); r += 1) {
        for (let c = Math.min(q.c0, q.c1); c <= Math.max(q.c0, q.c1); c += 1) {
          const k = `${r}:${c}`; if (vistas.has(k)) continue; vistas.add(k);
          const o = T.visibles[r]; const d = T.g.dias[c];
          if (o && d) salida.push({ r, c, o, d });
        }
      }
    });
    return salida;
  }
  function seleccionar(r, c, extender = false, agregar = false) {
    if (extender && T.rects.length) { const q = T.rects[T.rects.length - 1]; q.r1 = r; q.c1 = c; } else if (agregar) {
      T.rects.push({ r0: r, c0: c, r1: r, c1: c }); T.act = { r, c };
    } else { T.rects = [{ r0: r, c0: c, r1: r, c1: c }]; T.act = { r, c }; }
    if (!extender) T.act = { r, c };
    pintarSeleccion();
  }
  function asegurarVisible(r, c) {
    const td = celdaTd(r, c); const sc = $('tj-scroll'); if (!td || !sc) return;
    const a = td.getBoundingClientRect(); const b = sc.getBoundingClientRect();
    const izq = b.left + 540; const arriba = b.top + 40;
    if (a.left < izq) sc.scrollLeft -= izq - a.left;
    else if (a.right > b.right - 14) sc.scrollLeft += a.right - b.right + 14;
    if (a.top < arriba) sc.scrollTop -= arriba - a.top;
    else if (a.bottom > b.bottom - 14) sc.scrollTop += a.bottom - b.bottom + 14;
  }

  // ---------------------------------------------------------------- guardar celdas
  async function guardar(cambios, msg) {
    if (!puede('cargar')) { toast('Su usuario no tiene habilitada la carga de la tarja.', 'error'); return false; }
    if (!cambios.length) return false;
    return ejecutar(async () => {
      const res = await api(`${API}/celdas`, 'POST', { desde: T.desde, hasta: T.hasta, cambios });
      (res.operadores || []).forEach((n) => {
        const i = T.g.operadores.findIndex((o) => o.legajo === n.legajo); if (i >= 0) T.g.operadores[i] = n;
        const j = T.visibles.findIndex((o) => o.legajo === n.legajo); if (j >= 0) T.visibles[j] = n;
      });
      const sc = $('tj-scroll'); const pos = sc ? [sc.scrollLeft, sc.scrollTop] : null;
      renderGrilla();
      if (pos) { $('tj-scroll').scrollLeft = pos[0]; $('tj-scroll').scrollTop = pos[1]; }
      pintarSeleccion();
    }, msg);
  }
  function aplicarASeleccion(texto) {
    const cods = celdasSeleccionadas().map(({ o, d }) => ({ legajo: o.legajo, fecha: d.fecha, codigo: texto }));
    const n = cods.length;
    return guardar(cods, texto ? `${n} día(s) cargados con ${texto.toUpperCase()}` : `${n} día(s) borrados`);
  }

  // ---------------------------------------------------------------- editor con autocompletar
  function sugerencias(t) {
    const q = String(t || '').trim().toUpperCase();
    const est = T.op.codigos.filter((c) => c.activo).map((c) => ({ v: c.codigo, d: c.descripcion, col: c.color }));
    const ccs = T.op.centros_costos.map((c) => ({ v: String(c.id), d: c.nombre, col: c.color }));
    const todos = /^\d/.test(q) ? ccs.concat(est) : est.concat(ccs);
    if (!q) return todos.slice(0, 40);
    const empieza = todos.filter((x) => x.v.toUpperCase().startsWith(q));
    const contiene = todos.filter((x) => !x.v.toUpperCase().startsWith(q) && x.d.toUpperCase().includes(q));
    return empieza.concat(contiene).slice(0, 40);
  }
  function abrirEditor(inicial) {
    if (!puede('cargar') || !T.act) return;
    cerrarEditor();
    const td = celdaTd(T.act.r, T.act.c); if (!td) return;
    const rc = td.getBoundingClientRect();
    const box = document.createElement('div');
    box.className = 'tj-ed sheet-editor';
    box.setAttribute('translate', 'no');
    box.style.left = `${Math.min(rc.left, window.innerWidth - 360)}px`;
    box.style.top = `${rc.top}px`;
    box.innerHTML = '<input type="text" class="input" autocomplete="off" spellcheck="false" aria-label="Código o centro de costo"><div class="tj-sug menu" role="listbox"></div>';
    document.body.appendChild(box);
    const inp = box.querySelector('input'); const sug = box.querySelector('.tj-sug');
    T.editor = { box, inp, sug, idx: 0, lista: [] };
    const pintar = () => {
      const l = sugerencias(inp.value); T.editor.lista = l;
      if (T.editor.idx >= l.length) T.editor.idx = 0;
      sug.innerHTML = l.map((x, i) => `<div data-i="${i}" role="option" class="menu-item combo-opt ${i === T.editor.idx ? 'on' : ''}" style="justify-content:flex-start"><i class="swatch" style="--cell:${e(x.col)}"></i><b>${e(x.v)}</b><span class="trunc muted">${e(x.d)}</span></div>`).join('')
        || '<div class="menu-head">Sin coincidencias: no es un centro de costo ni un código de estado</div>';
    };
    const confirmar = (valor) => {
      const v = String(valor ?? '').trim().toUpperCase();
      cerrarEditor();
      if (!v) return;
      const ok = /^\d+$/.test(v) ? !!centro(v) : T.op.codigos.some((c) => c.activo && c.codigo === v);
      if (!ok) { toast(`«${v}» no es un centro de costo existente ni un código de estado activo.`, 'error'); return; }
      aplicarASeleccion(v).then(() => moverAct(1, 0));
    };
    inp.value = inicial || '';
    inp.addEventListener('input', () => { T.editor.idx = 0; pintar(); });
    inp.addEventListener('keydown', (ev) => {
      const ed = T.editor; if (!ed) return;
      if (ev.key === 'ArrowDown') { ev.preventDefault(); ed.nav = true; ed.idx = Math.min(ed.idx + 1, ed.lista.length - 1); pintar(); }
      else if (ev.key === 'ArrowUp') { ev.preventDefault(); ed.nav = true; ed.idx = Math.max(ed.idx - 1, 0); pintar(); }
      else if (ev.key === 'Enter' || ev.key === 'Tab') {
        ev.preventDefault();
        // Exacto > elegido con flechas > primer código que empieza con lo escrito; si no, se valida lo escrito tal cual.
        const q = inp.value.trim().toUpperCase();
        const exacto = ed.lista.find((x) => x.v.toUpperCase() === q);
        const prefijo = q && ed.lista[0] && ed.lista[0].v.toUpperCase().startsWith(q) ? ed.lista[0] : null;
        confirmar(exacto ? exacto.v : (ed.nav && ed.lista[ed.idx] ? ed.lista[ed.idx].v : (prefijo ? prefijo.v : inp.value)));
      } else if (ev.key === 'Escape') { ev.preventDefault(); cerrarEditor(); }
      ev.stopPropagation();
    });
    sug.addEventListener('mousedown', (ev) => { const it = ev.target.closest('[data-i]'); if (it) { ev.preventDefault(); confirmar(T.editor.lista[Number(it.dataset.i)].v); } });
    inp.addEventListener('blur', () => setTimeout(() => { if (T.editor && T.editor.inp === inp) cerrarEditor(); }, 150));
    pintar();
    inp.focus();
  }
  function cerrarEditor() { if (T.editor) { T.editor.box.remove(); T.editor = null; } }
  function moverAct(dr, dc, extender = false) {
    if (!T.act) return;
    const r = Math.max(0, Math.min(T.visibles.length - 1, (extender && T.rects.length ? T.rects[T.rects.length - 1].r1 : T.act.r) + dr));
    const c = Math.max(0, Math.min(T.g.dias.length - 1, (extender && T.rects.length ? T.rects[T.rects.length - 1].c1 : T.act.c) + dc));
    if (extender) seleccionar(r, c, true); else seleccionar(r, c);
    asegurarVisible(r, c);
  }

  // ---------------------------------------------------------------- copiar / pegar
  function copiar(ev) {
    if (!T.rects.length) return;
    const q = T.rects[T.rects.length - 1];
    const filas = [];
    for (let r = Math.min(q.r0, q.r1); r <= Math.max(q.r0, q.r1); r += 1) {
      const fila = [];
      for (let c = Math.min(q.c0, q.c1); c <= Math.max(q.c0, q.c1); c += 1) { const cel = T.visibles[r].dias[T.g.dias[c].fecha]; fila.push(cel ? cel.c : ''); }
      filas.push(fila.join('\t'));
    }
    ev.clipboardData.setData('text/plain', filas.join('\n'));
    ev.preventDefault();
    toast(`${filas.length} fila(s) copiadas`, 'info');
  }
  function pegar(ev) {
    if (!T.act) return;
    const texto = (ev.clipboardData && ev.clipboardData.getData('text/plain')) || '';
    ev.preventDefault();
    const matriz = texto.replace(/\r/g, '').replace(/\n+$/, '').split('\n').map((l) => l.split('\t').map((x) => x.trim()));
    if (!matriz.length) return;
    let cambios = [];
    if (matriz.length === 1 && matriz[0].length === 1) {
      cambios = celdasSeleccionadas().map(({ o, d }) => ({ legajo: o.legajo, fecha: d.fecha, codigo: matriz[0][0] }));
    } else {
      const q = T.rects[T.rects.length - 1] || { r0: T.act.r, c0: T.act.c };
      const r0 = Math.min(q.r0, q.r1 ?? q.r0); const c0 = Math.min(q.c0, q.c1 ?? q.c0);
      matriz.forEach((fila, i) => fila.forEach((v, j) => {
        const o = T.visibles[r0 + i]; const d = T.g.dias[c0 + j];
        if (o && d) cambios.push({ legajo: o.legajo, fecha: d.fecha, codigo: v });
      }));
    }
    guardar(cambios, `${cambios.length} celda(s) pegadas`);
  }

  // ---------------------------------------------------------------- eventos de la grilla
  function panelVisible() { const r = root(); return !!(r && r.offsetParent !== null); }
  function enInput() { const a = document.activeElement; return !!(a && (a.tagName === 'INPUT' || a.tagName === 'TEXTAREA' || a.tagName === 'SELECT' || a.isContentEditable)); }

  function instalarEventos() {
    document.addEventListener('mousedown', (ev) => {
      if (!panelVisible()) return;
      if (!ev.target.closest('.tj-menu') && !ev.target.closest('[data-tj-f]')) cerrarMenu();
      const td = ev.target.closest('#tj-scroll td.d');
      if (!td || ev.button !== 0) return;
      ev.preventDefault();
      if (document.activeElement && document.activeElement !== document.body) document.activeElement.blur();
      cerrarEditor();
      const r = Number(td.dataset.r); const c = Number(td.dataset.c);
      seleccionar(r, c, ev.shiftKey && !!T.act, ev.ctrlKey || ev.metaKey);
      T.arrastrando = true;
    });
    document.addEventListener('mouseover', (ev) => {
      if (!T.arrastrando) return;
      const td = ev.target.closest('#tj-scroll td.d'); if (!td) return;
      const q = T.rects[T.rects.length - 1]; if (!q) return;
      q.r1 = Number(td.dataset.r); q.c1 = Number(td.dataset.c);
      pintarSeleccion();
    });
    document.addEventListener('mouseup', () => { T.arrastrando = false; });
    document.addEventListener('dblclick', (ev) => {
      const td = ev.target.closest('#tj-scroll td.d'); if (!td) return;
      const o = T.visibles[Number(td.dataset.r)]; const d = T.g.dias[Number(td.dataset.c)];
      if (o && d) detalleDia(o, d);
    });
    document.addEventListener('click', (ev) => {
      if (!panelVisible()) return;
      const fb = ev.target.closest('[data-tj-f]');
      if (fb) { ev.stopPropagation(); abrirMenu(fb.dataset.tjF, fb); return; }
      const op = ev.target.closest('[data-tj-op]');
      if (op) detalleOperador(op.dataset.tjOp);
    });
    document.addEventListener('keydown', (ev) => {
      if (!panelVisible() || modalAbierto() || T.editor || enInput() || !T.act) return;
      const k = ev.key;
      const mov = { ArrowUp: [-1, 0], ArrowDown: [1, 0], ArrowLeft: [0, -1], ArrowRight: [0, 1] }[k];
      if (mov) { ev.preventDefault(); moverAct(mov[0], mov[1], ev.shiftKey); return; }
      if (k === 'Enter') { ev.preventDefault(); moverAct(ev.shiftKey ? -1 : 1, 0); return; }
      if (k === 'Tab') { ev.preventDefault(); moverAct(0, ev.shiftKey ? -1 : 1); return; }
      if (k === 'Delete' || k === 'Backspace') { ev.preventDefault(); aplicarASeleccion(''); return; }
      if (k === 'F2') { ev.preventDefault(); const cel = T.visibles[T.act.r].dias[T.g.dias[T.act.c].fecha]; abrirEditor(cel ? cel.c : ''); return; }
      if (k === 'Escape') { T.rects = []; T.act = null; pintarSeleccion(); return; }
      if (k.length === 1 && /[0-9a-zA-ZñÑ]/.test(k) && !ev.ctrlKey && !ev.metaKey && !ev.altKey) { ev.preventDefault(); abrirEditor(k); }
    });
    document.addEventListener('copy', (ev) => { if (panelVisible() && !modalAbierto() && !enInput() && T.rects.length) copiar(ev); });
    document.addEventListener('paste', (ev) => { if (panelVisible() && !modalAbierto() && !enInput() && T.act && puede('cargar')) pegar(ev); });
    window.addEventListener('resize', cerrarMenu);
    // Un desplazamiento justo después de abrir el menú (el navegador acomoda la columna) no lo cierra.
    document.addEventListener('scroll', (ev) => { if (ev.target && ev.target.id === 'tj-scroll' && performance.now() - (T.menuEn || 0) > 400) { cerrarMenu(); cerrarEditor(); } }, true);
  }

  // ---------------------------------------------------------------- menú de autofiltro (como Excel)
  function cerrarMenu() { const m = $('tj-menu'); if (m) m.remove(); }
  function abrirMenu(key, boton) {
    cerrarMenu();
    const col = colPorKey(key); if (!col) return;
    const f = JSON.parse(JSON.stringify(T.filtros[key] || {}));
    const base = filtrar(key);
    const valores = new Map();
    let vacias = false;
    base.forEach((o) => { const v = col.valor(o); if (v === '' || v === null || v === undefined) vacias = true; else valores.set(String(v), v); });
    const lista = [...valores.values()].sort((a, b) => (typeof a === 'number' && typeof b === 'number' ? a - b : String(a).localeCompare(String(b), 'es', { numeric: true })));
    const elegidos = f.vals ? new Set(f.vals) : null;
    const ops = col.mixto || col.ambos ? [['', 'Sin condición']].concat(OPS_TXT, OPS_NUM) : (col.num ? [['', 'Sin condición']].concat(OPS_NUM) : [['', 'Sin condición']].concat(OPS_TXT));
    const colores = col.mixto ? Object.keys(CATS) : [];
    const muestra = (cat) => ({ trabajado: '#cfe3fb', cambio: T.op.color_cambio_turno, descanso: (codigo('D') || {}).color || '#cbd5e1', vacaciones: (codigo('V') || {}).color || '#9be3b0',
      ausencia: (codigo('F') || {}).color || '#fca5a5', otro: (codigo('VJ') || {}).color || '#c4b5fd', vacia: '#ffffff' }[cat]);
    const m = document.createElement('div');
    m.id = 'tj-menu';
    m.className = 'tj-menu menu pop pop-stack ctl-auto';
    m.setAttribute('translate', 'no');
    const asc = col.num || col.mixto ? 'Ordenar de menor a mayor' : 'Ordenar de A a Z';
    const desc = col.num || col.mixto ? 'Ordenar de mayor a menor' : 'Ordenar de Z a A';
    m.innerHTML = `<div class="menu-head" style="padding: 4px 4px 0"><b>${e(col.label)}</b></div>
      <div><button type="button" class="menu-item it" data-ord="asc">${ic('up', 'i-sm')}${asc}${T.orden && T.orden.key === key && T.orden.dir === 'asc' ? ` ${ic('check', 'i-sm')}` : ''}</button>
      <button type="button" class="menu-item it" data-ord="desc">${ic('down', 'i-sm')}${desc}${T.orden && T.orden.key === key && T.orden.dir === 'desc' ? ` ${ic('check', 'i-sm')}` : ''}</button></div>
      <div class="menu-sep"></div>
      ${colores.length ? `<div class="kpi-label">Filtrar por color</div><div class="chip-row cols">${colores.map((c) => `<label><input type="checkbox" data-col="${c}" ${(f.color || []).includes(c) ? 'checked' : ''}><i class="swatch" style="--cell:${muestra(c)}"></i>${CATS[c]}</label>`).join('')}</div><div class="menu-sep"></div>` : ''}
      <div class="kpi-label">${col.mixto || col.ambos ? 'Filtros de texto o número' : (col.num ? 'Filtros de número' : 'Filtros de texto')}</div>
      <div class="pop-row fila"><select id="tj-m-op" class="select" aria-label="Condición">${col.mixto || col.ambos ? `<option value="">Sin condición</option><optgroup label="Texto">${opts(OPS_TXT, f.cond && f.cond.op)}</optgroup><optgroup label="Número">${opts(OPS_NUM, f.cond && f.cond.op)}</optgroup>` : opts(ops, f.cond && f.cond.op)}</select></div>
      <div class="pop-row fila"><input type="text" class="input" id="tj-m-a" placeholder="Valor" aria-label="Valor" value="${e(f.cond ? f.cond.a ?? '' : '')}"><input type="text" class="input" id="tj-m-b" placeholder="y" aria-label="Hasta" value="${e(f.cond ? f.cond.b ?? '' : '')}"></div>
      <div class="menu-sep"></div>
      <input type="search" class="input" id="tj-m-q" placeholder="Buscar" aria-label="Buscar valores">
      <div class="pop-list vals" id="tj-m-vals">
        <label><input type="checkbox" id="tj-m-todo"> <b>(Seleccionar todo)</b></label>
        ${vacias ? `<label data-v=""><input type="checkbox" value="" ${!elegidos || elegidos.has('') ? 'checked' : ''}> ${e(col.vacias || '(Vacías)')}</label>` : ''}
        ${lista.map((v) => { const s = String(v); const lab = col.etiqueta ? col.etiqueta(v) : s; const d = col.mixto ? descCodigo(s) : ''; return `<label data-v="${e(lab.toLowerCase())} ${e(d.toLowerCase())}"><input type="checkbox" value="${e(s)}" ${!elegidos || elegidos.has(s) ? 'checked' : ''}> ${e(lab)} ${d ? `<small>${e(d)}</small>` : ''}</label>`; }).join('')}
      </div>
      <div class="pop-foot pie"><button type="button" class="btn btn-ghost btn-sm" id="tj-m-borrar">Borrar filtro</button>
        <span class="head-actions"><button type="button" class="btn btn-sm" id="tj-m-cancel">Cancelar</button><button type="button" class="btn btn-sm btn-primary" id="tj-m-ok">Aceptar</button></span></div>`;
    document.body.appendChild(m);
    T.menuEn = performance.now();
    const rc = boton.getBoundingClientRect();
    const left = Math.max(8, Math.min(rc.left - 140, window.innerWidth - 330));
    m.style.left = `${left}px`;
    m.style.top = `${Math.min(rc.bottom + 4, Math.max(8, window.innerHeight - m.offsetHeight - 8))}px`;
    const checks = () => [...m.querySelectorAll('#tj-m-vals input[type=checkbox][value]')];
    const syncTodo = () => { const vis = checks().filter((c) => c.closest('label').style.display !== 'none'); $('tj-m-todo').checked = vis.length > 0 && vis.every((c) => c.checked); };
    const syncB = () => { const op = $('tj-m-op').value; $('tj-m-b').style.display = op === 'between' ? '' : 'none'; $('tj-m-a').style.display = op ? '' : 'none'; if (op === 'top' && !$('tj-m-a').value) $('tj-m-a').value = '10'; };
    syncTodo(); syncB();
    $('tj-m-op').addEventListener('change', syncB);
    $('tj-m-todo').addEventListener('change', (ev) => { checks().filter((c) => c.closest('label').style.display !== 'none').forEach((c) => { c.checked = ev.target.checked; }); });
    m.querySelector('#tj-m-vals').addEventListener('change', (ev) => { if (ev.target.id !== 'tj-m-todo') syncTodo(); });
    $('tj-m-q').addEventListener('input', (ev) => {
      const q = ev.target.value.toLowerCase().trim();
      checks().forEach((c) => { const lab = c.closest('label'); const vis = !q || (lab.dataset.v || '').includes(q); lab.style.display = vis ? '' : 'none'; if (q) c.checked = vis; });
      syncTodo();
    });
    m.querySelectorAll('[data-ord]').forEach((b) => b.addEventListener('click', () => {
      T.orden = (T.orden && T.orden.key === key && T.orden.dir === b.dataset.ord) ? null : { key, dir: b.dataset.ord };
      cerrarMenu(); aplicarFiltros();
    }));
    $('tj-m-cancel').addEventListener('click', cerrarMenu);
    $('tj-m-borrar').addEventListener('click', () => { delete T.filtros[key]; if (T.orden && T.orden.key === key) T.orden = null; cerrarMenu(); aplicarFiltros(); });
    $('tj-m-ok').addEventListener('click', () => {
      const nuevo = {};
      const cs = checks();
      if (cs.some((c) => !c.checked)) nuevo.vals = cs.filter((c) => c.checked).map((c) => c.value);
      const op = $('tj-m-op').value;
      if (op) nuevo.cond = { op, a: $('tj-m-a').value, b: $('tj-m-b').value };
      const colSel = [...m.querySelectorAll('[data-col]')].filter((c) => c.checked).map((c) => c.dataset.col);
      if (colSel.length) nuevo.color = colSel;
      if (Object.keys(nuevo).length) T.filtros[key] = nuevo; else delete T.filtros[key];
      cerrarMenu(); aplicarFiltros();
    });
    $('tj-m-q').focus();
  }

  // ---------------------------------------------------------------- panel del día (horas a mano)
  async function detalleDia(o, d) {
    const cel = o.dias[d.fecha];
    const editable = puede('editar_horas') && !!cel;
    let hist = [];
    try { hist = await api(`${API}/historial?legajo=${encodeURIComponent(o.legajo)}&fecha=${d.fecha}`); } catch (err) { hist = []; }
    const fmtH = (h) => {
      const a = h.anterior_json ? JSON.parse(h.anterior_json) : null; const n = h.nuevo_json ? JSON.parse(h.nuevo_json) : null;
      const txt = (x) => (x ? Object.entries(x).map(([k, v]) => `${k.replace(/_/g, ' ')}: ${v ?? '—'}`).join(', ') : '—');
      return `<li><b>${e(h.accion.replace(/_/g, ' ').toLowerCase())}</b> <span class="muted">· ${e(h.usuario)} · ${e(SMG.fmtDiaHora(h.creado_en, { utc: true }))}</span><br><span class="muted">${e(txt(a))} → ${e(txt(n))}</span></li>`;
    };
    const cuerpo = `
      <div class="form-grid" style="grid-template-columns:1fr 1fr">
        <div class="field"><label>Operador</label><div class="field-val">${e(o.legajo)} · ${e(o.nombre)}</div></div>
        <div class="field"><label>Fecha</label><div class="field-val">${fFecha(d.fecha)} (${INI[d.dow]})${d.feriado ? ` · Feriado: ${e(d.feriado)}` : ''}</div></div>
        <div class="field" style="grid-column:span 2"><label>Código</label><div class="field-val">${cel ? `<b>${e(cel.c)}</b> - ${e(descCodigo(cel.c))}` : '<span class="muted">Sin cargar</span>'}
          ${cel && cel.ct ? ' <span class="chip chip-warn">Cambio de turno</span>' : ''}${cel && cel.m ? ' <span class="chip chip-bad">Horas a mano</span>' : ''}</div></div>
        ${cel ? `<div class="field"><label>Horas efectivas</label><input type="number" step="0.5" min="0" max="24" id="tj-d-h" value="${cel.h}" ${editable ? '' : 'disabled'}></div>
        <div class="field"><label>Horas de break</label><input type="number" step="0.5" min="0" max="24" id="tj-d-b" value="${cel.b}" ${editable ? '' : 'disabled'}></div>
        <div class="field" style="grid-column:span 2"><label>Observaciones</label><textarea id="tj-d-o" rows="2" ${editable ? '' : 'disabled'}>${e(cel.o || '')}</textarea></div>` : ''}
      </div>
      ${cel ? '<p class="muted" style="margin:8px 0 0">Al cambiar las horas, el día queda marcado como editado a mano y los recálculos no lo modifican.</p>' : ''}
      <div class="sec-title">Historial del día</div>
      <ul class="list-plain">${hist.map(fmtH).join('') || '<li class="muted">Sin cambios registrados.</li>'}</ul>`;
    const pie = `${editable && cel.m ? '<button type="button" class="btn" id="tj-d-rest">Restaurar cálculo automático</button>' : ''}
      <button type="button" class="btn" data-tj-cerrar>Cerrar</button>${editable ? '<button type="button" class="btn btn-primary" id="tj-d-ok">Guardar</button>' : ''}`;
    modal(`Día ${fFecha(d.fecha)}`, cuerpo, pie, 'sm');
    const enviar = (datos, msg) => ejecutar(async () => {
      const res = await api(`${API}/registro`, 'PUT', { legajo: o.legajo, fecha: d.fecha, desde: T.desde, hasta: T.hasta, ...datos });
      (res.operadores || []).forEach((n) => {
        const i = T.g.operadores.findIndex((x) => x.legajo === n.legajo); if (i >= 0) T.g.operadores[i] = n;
        const j = T.visibles.findIndex((x) => x.legajo === n.legajo); if (j >= 0) T.visibles[j] = n;
      });
      cerrarModal(); renderGrilla(); pintarSeleccion();
    }, msg);
    if ($('tj-d-ok')) $('tj-d-ok').addEventListener('click', () => enviar({ horas_efectivas: $('tj-d-h').value, horas_break: $('tj-d-b').value, observaciones: $('tj-d-o').value }, 'Día actualizado'));
    if ($('tj-d-rest')) $('tj-d-rest').addEventListener('click', () => enviar({ restaurar: true, observaciones: $('tj-d-o').value }, 'Horas recalculadas automáticamente'));
  }

  // ---------------------------------------------------------------- operador (régimen de turno)
  async function detalleOperador(legajo) {
    const o = T.g.operadores.find((x) => x.legajo === legajo); if (!o) return;
    const editable = puede('configurar');
    const cuerpo = `<div class="form-grid" style="grid-template-columns:1fr 1fr">
        <div class="field"><label>Legajo</label><div class="field-val">${e(o.legajo)}</div></div>
        <div class="field"><label>Apellido y Nombre</label><div class="field-val">${e(o.nombre)}</div></div>
        <div class="field"><label>Obra (hoy)</label><div class="field-val">${o.obra.pendiente ? '<span class="chip chip-warn">P</span> Pendiente: hoy sin cargar' : e(o.obra.texto)}</div></div>
        <div class="field"><label>Régimen de turno</label><select id="tj-o-reg" ${editable ? '' : 'disabled'}>${opts([['Rotativo', 'Rotativo (subida y bajada con media jornada)'], ['Diario', 'Diario (5x2, oficina / taller)']], o.regimen)}</select></div>
      </div>
      <div class="sec-title">Totales del ${fFecha(T.desde)} al ${fFecha(T.hasta)}</div>
      <div class="kpis kpis-tight">${TOTALES.map(([k, , l]) => `<div class="card kpi"><span class="kpi-label">${l}</span><span class="kpi-value sm">${nf(o.totales[k])}</span></div>`).join('')}</div>`;
    modal(`Operador ${e(o.legajo)}`, cuerpo, `<button type="button" class="btn" data-tj-cerrar>Cerrar</button>${editable ? '<button type="button" class="btn btn-primary" id="tj-o-ok">Guardar régimen</button>' : ''}`, 'sm');
    if ($('tj-o-ok')) $('tj-o-ok').addEventListener('click', () => ejecutar(async () => {
      await api(`${API}/regimen`, 'PUT', { legajo: o.legajo, regimen: $('tj-o-reg').value });
      cerrarModal(); await cargarMes();
    }, 'Régimen actualizado; horas recalculadas'));
  }

  // ---------------------------------------------------------------- configuración
  function configuracion(tab) {
    const c = T.op.config;
    const tabs = [['jornada', 'Jornada'], ['codigos', 'Códigos de estado'], ['feriados', 'Feriados'], ['importar', 'Importar histórico']];
    let cuerpo = `<div class="seg seg-lg" role="tablist" style="margin-bottom:14px">${tabs.map(([k, l]) => `<button type="button" data-tj-tab="${k}" class="${k === tab ? 'on' : ''}">${l}</button>`).join('')}</div>`;
    if (tab === 'jornada') {
      cuerpo += `<div class="form-grid" style="grid-template-columns:repeat(2,minmax(0,1fr))">
        <div class="field"><label>Jornada normal: horas efectivas</label><input type="number" step="0.5" id="tj-c-1" value="${c.horas_efectivas}"></div>
        <div class="field"><label>Jornada normal: horas de break</label><input type="number" step="0.5" id="tj-c-2" value="${c.horas_break}"></div>
        <div class="field"><label>Cambio de turno (subida / bajada): horas efectivas</label><input type="number" step="0.5" id="tj-c-3" value="${c.horas_efectivas_cambio_turno}"></div>
        <div class="field"><label>Cambio de turno (subida / bajada): horas de break</label><input type="number" step="0.5" id="tj-c-4" value="${c.horas_break_cambio_turno}"></div></div>
        <p class="muted" style="margin-top:10px">Al guardar se recalculan todos los días, salvo los editados a mano. La media jornada solo se aplica al régimen Rotativo.</p>
        <div class="head-actions" style="justify-content:flex-end"><button type="button" class="btn btn-primary" id="tj-c-ok">Guardar jornada</button></div>`;
    } else if (tab === 'codigos') {
      const fila = (k) => `<tr data-id="${k ? k.id : ''}">
        <td><input type="text" class="tj-k-cod" value="${e(k ? k.codigo : '')}" style="width:70px;text-transform:uppercase" maxlength="10"></td>
        <td><input type="text" class="tj-k-desc" value="${e(k ? k.descripcion : '')}" style="width:220px"></td>
        <td class="center"><input type="color" class="tj-k-color" value="${e(k ? k.color : '#cbd5e1')}"></td>
        <td><select class="tj-k-cat">${opts(T.op.categorias.map((x) => [x, x.charAt(0) + x.slice(1).toLowerCase()]), k ? k.categoria : 'OTRO')}</select></td>
        <td class="center"><input type="checkbox" class="tj-k-trab" ${k && k.cuenta_como_trabajado ? 'checked' : ''}></td>
        <td class="center"><input type="checkbox" class="tj-k-act" ${!k || k.activo ? 'checked' : ''}></td>
        <td class="center"><button type="button" class="btn btn-sm ${k ? '' : 'btn-primary'}" data-tj-kok>${k ? 'Guardar' : 'Agregar'}</button></td></tr>`;
      cuerpo += `<div class="table-scroll"><table class="tbl tbl-static tbl-auto"><thead><tr><th>Código</th><th>Descripción</th><th>Color</th><th>Categoría (totales)</th><th>Cuenta como trabajado</th><th>Activo</th><th></th></tr></thead>
        <tbody>${T.op.codigos.map(fila).join('')}${fila(null)}</tbody></table></div>
        <p class="muted" style="margin-top:8px">Los números son siempre centros de costo, por eso los códigos empiezan con letra. Un código que cuenta como trabajado suma jornada y continúa el turno. Los códigos no se borran: se desactivan.</p>`;
    } else if (tab === 'importar') {
      cuerpo += `<div class="callout info" style="margin-bottom:12px">${ic('info')}<div><b>Histórico desde la planilla «TARJA GENERAL»</b> 
        Elija el Excel (Logistica SMG.xlsm). Primero se simula y se muestra qué entra y qué no; recién al confirmar se graba.
        Se cruza por legajo; los centros de costo deben existir en el sistema; los días ya cargados no se pisan. Antes de grabar se respalda la base.</div></div>
        <div class="head-actions" style="margin-bottom:12px"><input type="file" id="tj-i-arch" accept=".xlsx,.xlsm" style="width:auto">
          <button type="button" class="btn btn-primary" id="tj-i-sim">Simular</button></div>
        <div id="tj-i-res"></div>`;
    } else {
      const fer = T.op.feriados || [];
      cuerpo += `<div class="head-actions" style="margin-bottom:10px"><input type="date" id="tj-f-fecha"><input type="text" id="tj-f-desc" placeholder="Descripción" style="width:260px">
        <button type="button" class="btn btn-primary" id="tj-f-add">Agregar feriado</button></div>
        <div class="table-scroll" style="max-height:360px;overflow:auto"><table class="tbl tbl-static tbl-auto"><thead><tr><th>Fecha</th><th>Descripción</th><th></th></tr></thead><tbody>
        ${fer.map((f) => `<tr><td>${fFecha(f.fecha)}</td><td>${e(f.descripcion)}</td><td class="center"><button type="button" class="btn btn-sm btn-danger" data-tj-fdel="${f.fecha}">Quitar</button></td></tr>`).join('')
          || '<tr><td colspan="3" class="muted">No hay feriados cargados.</td></tr>'}</tbody></table></div>
        <p class="muted" style="margin-top:8px">El feriado resalta la columna del día. Quien no trabaja ese día se carga con el código FER; el resto con su centro de costo.</p>`;
    }
    modal('Configuración de la Tarja', cuerpo, '<button type="button" class="btn" data-tj-cerrar>Cerrar</button>', 'md');
    const b = $('tj-modal-b');
    b.querySelectorAll('[data-tj-tab]').forEach((x) => x.addEventListener('click', () => configuracion(x.dataset.tjTab)));
    const recargar = async (pestana) => { T.op = await api(`${API}/opciones`); configuracion(pestana); leyenda(); await cargarMes(); };
    if (tab === 'importar') {
      importarHistorico();
    } else if (tab === 'jornada') {
      $('tj-c-ok').addEventListener('click', () => ejecutar(async () => {
        await api(`${API}/config`, 'PUT', { horas_efectivas: $('tj-c-1').value, horas_break: $('tj-c-2').value,
          horas_efectivas_cambio_turno: $('tj-c-3').value, horas_break_cambio_turno: $('tj-c-4').value });
        await recargar('jornada');
      }, 'Jornada guardada; días recalculados'));
    } else if (tab === 'codigos') {
      b.querySelectorAll('[data-tj-kok]').forEach((btn) => btn.addEventListener('click', () => {
        const tr = btn.closest('tr'); const id = tr.dataset.id;
        const datos = { codigo: tr.querySelector('.tj-k-cod').value, descripcion: tr.querySelector('.tj-k-desc').value, color: tr.querySelector('.tj-k-color').value,
          categoria: tr.querySelector('.tj-k-cat').value, cuenta_como_trabajado: tr.querySelector('.tj-k-trab').checked, activo: tr.querySelector('.tj-k-act').checked };
        ejecutar(async () => {
          await api(id ? `${API}/codigos/${id}` : `${API}/codigos`, id ? 'PUT' : 'POST', datos);
          await recargar('codigos');
        }, id ? 'Código actualizado' : 'Código agregado');
      }));
    } else {
      $('tj-f-add').addEventListener('click', () => ejecutar(async () => {
        await api(`${API}/feriados`, 'POST', { fecha: $('tj-f-fecha').value, descripcion: $('tj-f-desc').value });
        await recargar('feriados');
      }, 'Feriado guardado'));
      b.querySelectorAll('[data-tj-fdel]').forEach((btn) => btn.addEventListener('click', () => ejecutar(async () => {
        await api(`${API}/feriados/${btn.dataset.tjFdel}`, 'DELETE');
        await recargar('feriados');
      }, 'Feriado quitado')));
    }
  }

  // ---------------------------------------------------------------- importar histórico (Configuración)
  function importarHistorico() {
    let ultimo = null;
    const enviar = async (aplicar) => {
      const arch = $('tj-i-arch').files[0];
      if (!arch) { toast('Elija primero el archivo Excel.', 'error'); return; }
      const fd = new FormData();
      fd.append('archivo', arch);
      fd.append('aplicar', aplicar ? 'true' : 'false');
      $('tj-i-res').innerHTML = `<p class="muted">${aplicar ? 'Importando' : 'Leyendo la planilla y simulando'}… puede tardar un minuto.</p>`;
      ['tj-i-sim', 'tj-i-apl'].forEach((id) => { if ($(id)) $(id).disabled = true; });
      let res; let data = null;
      try {
        res = await fetch(`${API}/importar`, { method: 'POST', body: fd });
        try { data = await res.json(); } catch (err) { data = null; }
      } catch (err) { data = { detail: 'No se pudo conectar con el servidor.' }; res = { ok: false }; }
      if ($('tj-i-sim')) $('tj-i-sim').disabled = false;
      if (!res.ok) { $('tj-i-res').innerHTML = ''; toast((data && data.detail) || 'Error al importar.', 'error'); return; }
      ultimo = data;
      mostrar(data, arch.name);
      if (aplicar) { toast(`Importados ${data.resumen.dias_a_importar} días.`, 'success'); await cargarMes(); }
    };
    const descargar = () => {
      const bin = atob(ultimo.reporte_b64); const bytes = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i += 1) bytes[i] = bin.charCodeAt(i);
      const a = document.createElement('a');
      a.href = URL.createObjectURL(new Blob([bytes], { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' }));
      a.download = ultimo.reporte_nombre; document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 2000);
    };
    const mostrar = (d, nombre) => {
      const r = d.resumen;
      const kv = (k, v) => `<div class="kv-row"><span>${k}</span><b>${v}</b></div>`;
      const aplicado = r.aplicado;
      $('tj-i-res').innerHTML = `
        <div class="callout ${aplicado ? 'ok' : 'info'}" style="margin-bottom:10px">${ic(aplicado ? 'check' : 'info')}
          <div><b>${aplicado ? 'Importación aplicada' : 'Simulación: todavía no se grabó nada'}</b> ${e(nombre)} · días del ${fFecha(r.desde)} al ${fFecha(r.hasta)}${d.respaldo ? ` · respaldo: ${e(d.respaldo)}` : ''}</div></div>
        <div class="grid2" style="grid-template-columns:1fr 1fr">
          <div>${kv('Operadores que se importan', nf(r.filas_importables))}${kv(aplicado ? 'Días importados' : 'Días a importar', nf(r.dias_a_importar))}
            ${kv('Días ya cargados (no se pisan)', nf(r.ya_cargados))}${kv('Excepciones (ver reporte)', nf(r.excepciones))}
            ${d.filas_excluidas.length ? `<div class="sec-title">Filas excluidas</div>${d.filas_excluidas.map(([m, n]) => kv(e(m), n)).join('')}` : ''}</div>
          <div><div class="sec-title" style="margin-top:0">Celdas sin importar</div>
            <div class="table-scroll" style="max-height:230px;overflow:auto"><table class="tbl tbl-static tbl-auto"><thead><tr><th>Valor</th><th>Motivo</th><th>Celdas</th></tr></thead><tbody>
            ${d.sin_importar.map((x) => `<tr><td>${e(x.valor)}</td><td style="white-space:normal">${e(x.motivo)}</td><td class="num">${nf(x.celdas)}</td></tr>`).join('') || '<tr><td colspan="3" class="muted">Ninguna.</td></tr>'}
            </tbody></table></div></div>
        </div>
        <div class="head-actions" style="justify-content:flex-end;margin-top:12px">
          <button type="button" class="btn" id="tj-i-rep">Descargar reporte de excepciones</button>
          ${!aplicado && r.dias_a_importar > 0 ? `<button type="button" class="btn btn-primary" id="tj-i-apl">Importar ${nf(r.dias_a_importar)} días</button>` : ''}
        </div>`;
      $('tj-i-rep').addEventListener('click', descargar);
      if ($('tj-i-apl')) $('tj-i-apl').addEventListener('click', () => {
        if (!window.confirm(`Se van a grabar ${r.dias_a_importar} días en la tarja (antes se respalda la base). ¿Continuar?`)) return;
        enviar(true);
      });
    };
    $('tj-i-sim').addEventListener('click', () => enviar(false));
    $('tj-i-arch').addEventListener('change', () => { $('tj-i-res').innerHTML = ''; ultimo = null; });
  }

  // ---------------------------------------------------------------- exportar
  async function exportar(filtrado) {
    await ejecutar(async () => {
      const cuerpo = { desde: T.desde, hasta: T.hasta };
      if (filtrado) cuerpo.legajos = T.visibles.map((o) => o.legajo);
      let res;
      try {
        res = await fetch(`${API}/exportar`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(cuerpo) });
      } catch (err) { throw new Error('No se pudo conectar con el servidor.'); }
      if (!res.ok) { let d = null; try { d = await res.json(); } catch (err) { d = null; } throw new Error((d && d.detail) || `Error ${res.status} del servidor.`); }
      const blob = await res.blob();
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = `Tarja_General_${T.desde}_a_${T.hasta}${filtrado ? '_filtrado' : ''}.xlsx`;
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 2000);
    }, 'Excel generado');
  }

  instalarEventos();
  window.tjAbrir = () => { abrir().catch((err) => toast(err.message, 'error')); };
})();

/* ==================================================================================================
 * Tarja General › Próximos movimientos.
 * Pestaña extra dentro del panel de Tarja: a partir de una fecha (por defecto hoy) lista quién sube y quién baja
 * de cada proyecto (centro de costo), día por día, para planificar los cambios de turno.
 *
 * Cómo lee la tarja: cada día de cada operador tiene un código. Si es un número es el centro de costo donde
 * está trabajando; si son letras es un estado (D, V, LIC, BAJA, ...). Un movimiento es cualquier cambio de código
 * respecto del día anterior:
 *   D → 106          Sube a 106
 *   106 → D          Baja de 106
 *   27 → 107         Sale de 27 y entra a 107 (traslado; figura en los dos proyectos)
 *   107 → 27 / D     Vuelve a 27 / baja de 107 (fin del traslado)
 *   NUE → 106        Ingreso nuevo        106 → BAJA  Egreso
 *   106 → V/LIC/...  Sale por estado      V → 106     Regresa
 * Fechas (iguales al subrayado de cambio de turno de la grilla):
 *   · la entrada a un proyecto se fecha el PRIMER día con su código;
 *   · la salida se fecha el ÚLTIMO día con su código (el día anterior al D o al estado siguiente), porque ese día
 *     todavía está en obra y se cruza con el grupo que sube. En un traslado, sale de 27 su último día en 27 y entra a
 *     107 el primer día cargado con 107 (el día siguiente).
 * Dotación de un día = los que quedan en el proyecto al cierre (ingresan + permanecen, sin los que bajan ese día);
 * cruce = dotación + los que bajan ese día (todos los que estuvieron en obra ese día).
 * Los días sin cargar no cuentan como movimiento.
 *
 * Solo lee datos (GET /api/tarja/grilla y /api/tarja/opciones); no modifica nada.
 * ================================================================================================== */
(() => {
  'use strict';
  const API = '/api/tarja';
  const ATRAS = 45;   // días hacia atrás que se leen para saber hace cuánto está cada uno en obra
  const MARGEN = 60;  // días extra hacia adelante para saber hasta cuándo dura cada tramo
  const MAX_PERIODO = 186; // tope de días por consulta de /grilla (tarja.MAX_DIAS_PERIODO)
  const DIAS = ['dom', 'lun', 'mar', 'mié', 'jue', 'vie', 'sáb'];

  const M = {
    vista: 'grilla',
    desde: null,
    horizonte: 30,
    regimen: 'Rotativo',
    cc: '',
    ver: new Set(['subidas', 'bajadas', 'permanecen', 'traslados']), // qué mostrar (filtro Mostrar)
    buscar: '',
    op: null,       // /opciones
    datos: null,    // resultado procesado
    cargado: null,  // clave desde|horizonte ya cargada
  };
  let abrirGrilla = null; // window.tjAbrir original de la grilla

  // ------------------------------------------------------------------ utilidades
  const $ = (id) => document.getElementById(id);
  const e = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const toast = (msg, tipo) => { if (window.showToast) window.showToast(msg, tipo); else console.log(msg); };
  const ic = (n, c = '') => `<svg class="i ${c}" aria-hidden="true"><use href="#i-${n}"/></svg>`;

  const aFecha = (iso) => { const [y, m, d] = iso.split('-').map(Number); return new Date(Date.UTC(y, m - 1, d)); };
  const aIso = (dt) => dt.toISOString().slice(0, 10);
  const sumar = (iso, n) => { const d = aFecha(iso); d.setUTCDate(d.getUTCDate() + n); return aIso(d); };
  const difDias = (a, b) => Math.round((aFecha(b) - aFecha(a)) / 86400000);
  const corta = (iso) => { const [, m, d] = iso.split('-'); return `${d}/${m}`; };
  const larga = (iso) => `${DIAS[aFecha(iso).getUTCDay()]} ${corta(iso)}`;
  const esCC = (c) => /^\d+$/.test(c || '');

  async function api(url) {
    let res;
    try { res = await fetch(url, { credentials: 'same-origin' }); } catch (err) { throw new Error('No se pudo conectar con el servidor.'); }
    if (!res.ok) { let d = null; try { d = await res.json(); } catch (err) { d = null; } throw new Error((d && d.detail) || `Error ${res.status} del servidor.`); }
    return res.json();
  }

  function nombreCC(id) {
    const cc = (M.op?.centros_costos || []).find((c) => String(c.id) === String(id));
    return cc ? cc.nombre : '';
  }
  function colorCC(id) {
    const cc = (M.op?.centros_costos || []).find((c) => String(c.id) === String(id));
    return cc?.color || '#e2e8f0';
  }
  function codigo(c) { return (M.op?.codigos || []).find((x) => x.codigo === c); }
  function categoria(c) { return codigo(c)?.categoria || ''; }
  function etiqueta(c) {
    if (!c) return 'sin cargar';
    if (esCC(c)) return c;
    const k = codigo(c);
    return k ? `${c} (${k.descripcion})` : c;
  }

  // ------------------------------------------------------------------ cálculo
  /* Agrupa los días de un operador en tramos consecutivos con el mismo código. */
  function tramos(fechas, dias) {
    const t = [];
    fechas.forEach((f) => {
      const c = String(dias?.[f]?.c ?? '').trim().toUpperCase();
      const u = t[t.length - 1];
      if (u && u.c === c) u.fin = f; else t.push({ c, ini: f, fin: f });
    });
    // Tramo abierto: después solo hay días sin cargar (todavía sin planificar), igual que la grilla (no marca bajada).
    t.forEach((x, i) => { x.abierto = i === t.length - 1 || (i === t.length - 2 && t[i + 1].c === ''); });
    return t;
  }

  function tipoEntrada(de, cat) {
    if (esCC(de)) return 'traslado_in';
    if (!de || de === 'NUE') return 'ingreso';
    if (cat(de) === 'DESCANSO') return 'sube';
    return 'regresa';
  }
  function tipoSalida(a, cat) {
    if (esCC(a)) return 'traslado_out';
    if (a === 'BAJA') return 'egreso';
    if (cat(a) === 'DESCANSO') return 'baja';
    return 'sale_estado';
  }

  /* Recorre la grilla y arma, por centro de costo y por fecha, quién entra y quién sale.
   * La entrada se fecha el primer día del tramo nuevo; la salida, el último día del tramo en el proyecto (el día del
   * subrayado de cambio de turno), así quien sube y quien baja el mismo miércoles quedan en la misma fila.
   * cat(codigo) devuelve la categoría del código de estado (DESCANSO, VACACIONES, ...). Función pura. */
  function procesar(g, desde, limite, cat = categoria) {
    const fechas = g.dias.map((d) => d.fecha);
    const primera = fechas[0];
    const movs = [];
    const ops = [];
    g.operadores.forEach((o) => {
      const t = tramos(fechas, o.dias);
      ops.push({ legajo: o.legajo, nombre: o.nombre, regimen: o.regimen, t });
      for (let k = 1; k < t.length; k += 1) {
        const cur = t[k];
        if (cur.c === '') continue;                         // pasar a "sin cargar" no es un movimiento
        let p = k - 1;                                       // código real anterior (salteando días sin cargar)
        while (p >= 0 && t[p].c === '') p -= 1;
        const prev = p >= 0 ? t[p] : null;
        const de = prev ? prev.c : '';
        if (de === cur.c) continue;
        const sigue = t[k + 1];
        const base = { legajo: o.legajo, nombre: o.nombre, regimen: o.regimen, de, a: cur.c };
        if (esCC(cur.c) && cur.ini >= desde && cur.ini <= limite) {
          movs.push({ ...base, cc: cur.c, lado: 'entra', tipo: tipoEntrada(de, cat), fecha: cur.ini,
            // tramo nuevo: hasta cuándo dura (último día en obra) y a qué pasa después
            hasta: cur.fin, hastaAbierto: cur.abierto, despues: sigue ? sigue.c : null });
        }
        if (esCC(de) && prev.fin >= desde && prev.fin <= limite) {
          prev.sale = true; // ese último día baja: no cuenta en "Permanecen"
          movs.push({ ...base, cc: de, lado: 'sale', tipo: tipoSalida(cur.c, cat), fecha: prev.fin,
            // tramo que termina: desde cuándo venía (los días en obra incluyen el de la baja)
            venia: prev.ini, veniaAbierto: prev.ini === primera, diasObra: difDias(prev.ini, prev.fin) + 1 });
        }
      }
    });
    return { movs, ops };
  }

  /* Los que ese día siguen en el proyecto: están en cc ese día, ya lo estaban el día anterior y no bajan ese día.
   * incluir(o) filtra por régimen / búsqueda. Función pura. */
  function permanecen(ops, cc, f, incluir = coincide) {
    return ops.filter(incluir)
      .map((o) => {
        const r = o.t.find((x) => x.ini <= f && x.fin >= f);
        const baja = r && r.sale && r.fin === f;
        return r && r.c === cc && r.ini < f && !baja ? { ...o, hasta: r.fin, abierto: r.abierto, desde: r.ini } : null;
      })
      .filter(Boolean)
      .sort((a, b2) => a.nombre.localeCompare(b2.nombre));
  }
  function coincide(o) {
    const b = M.buscar.trim().toLowerCase();
    return (!M.regimen || o.regimen === M.regimen) && (!b || `${o.legajo} ${o.nombre}`.toLowerCase().includes(b));
  }

  const TIPOS = {
    sube: { txt: 'Sube', cls: 'up', grupo: 'subidas' },
    ingreso: { txt: 'Ingreso nuevo', cls: 'up', grupo: 'subidas' },
    regresa: { txt: 'Regresa', cls: 'up', grupo: 'subidas' },
    traslado_in: { txt: 'Entra por traslado', cls: 'tr', grupo: 'traslados' },
    baja: { txt: 'Baja', cls: 'down', grupo: 'bajadas' },
    egreso: { txt: 'Egreso', cls: 'down', grupo: 'bajadas' },
    sale_estado: { txt: 'Sale', cls: 'down', grupo: 'bajadas' },
    traslado_out: { txt: 'Sale por traslado', cls: 'tr', grupo: 'traslados' },
  };

  function filtrar(movs) {
    const b = M.buscar.trim().toLowerCase();
    return movs.filter((m) => (!M.regimen || m.regimen === M.regimen)
      && (!M.cc || m.cc === M.cc)
      && (!b || `${m.legajo} ${m.nombre}`.toLowerCase().includes(b)));
  }

  /* Aplica el filtro "Mostrar". Si solo se tildó "Permanecen", las fechas salen de todos los movimientos. */
  function seleccion() {
    const base = filtrar(M.datos.movs);
    const hayMov = ['subidas', 'bajadas', 'traslados'].some((g) => M.ver.has(g));
    return { hayMov, base, movs: hayMov ? base.filter((m) => M.ver.has(TIPOS[m.tipo].grupo)) : base };
  }

  const OPCIONES_VER = [['subidas', 'Ingresan'], ['bajadas', 'Salen'], ['permanecen', 'Permanecen'], ['traslados', 'Traslados']];
  function textoVer() {
    if (M.ver.size === OPCIONES_VER.length) return 'Todos';
    if (!M.ver.size) return 'Nada';
    return OPCIONES_VER.filter(([v]) => M.ver.has(v)).map(([, t]) => t).join(', ');
  }

  // ------------------------------------------------------------------ carga
  async function cargar(forzar) {
    const clave = `${M.desde}|${M.horizonte}`;
    if (!forzar && M.cargado === clave && M.datos) { render(); return; }
    cuerpo().innerHTML = '<section class="card"><div class="empty-state">Cargando movimientos…</div></section>';
    try {
      if (!M.op) M.op = await api(`${API}/opciones`);
      if (!M.desde) M.desde = M.op.hoy;
      const limite = sumar(M.desde, M.horizonte - 1);
      const ini = sumar(M.desde, -ATRAS);
      // El servidor acepta hasta MAX_PERIODO días por consulta: el margen hacia adelante se recorta si hace falta.
      const fin = [sumar(limite, MARGEN), sumar(ini, MAX_PERIODO - 1)].sort()[0];
      const g = await api(`${API}/grilla?${new URLSearchParams({ desde: ini, hasta: fin })}`);
      M.datos = { ...procesar(g, M.desde, limite), limite };
      M.cargado = clave;
    } catch (err) {
      cuerpo().innerHTML = `<section class="card"><div class="empty-state is-danger-text">${e(err.message)}</div></section>`;
      toast(err.message, 'error');
      return;
    }
    render();
  }

  // ------------------------------------------------------------------ dibujo
  const cuerpo = () => $('tjm-cuerpo');

  const VARIANTE = { up: 'ok', down: 'bad', tr: 'accent' };
  function chip(m) {
    const t = TIPOS[m.tipo];
    let det;
    if (m.lado === 'entra') {
      const dias = difDias(m.fecha, m.hasta) + 1;
      const fin = m.hastaAbierto ? `más de ${dias} d` : `hasta ${corta(m.hasta)} (${dias} d)`;
      const luego = m.hastaAbierto || m.despues == null ? '' : ` → ${e(etiqueta(m.despues))}`;
      const origen = m.tipo === 'traslado_in' ? `desde ${e(m.de)} · ` : (m.tipo === 'regresa' ? `de ${e(etiqueta(m.de))} · ` : '');
      det = `${origen}${fin}${luego}`;
    } else {
      const dias = m.diasObra;
      const enObra = m.venia ? (m.veniaAbierto ? `${dias}+ d en obra` : `${dias} d en obra`) : '';
      const destino = m.tipo === 'traslado_out' ? `a ${e(m.a)}` : `a ${e(etiqueta(m.a))}`;
      det = [destino, enObra].filter(Boolean).join(' · ');
    }
    return `<div class="line-item ${VARIANTE[t.cls]}" title="${e(t.txt)}: ${e(etiqueta(m.de))} → ${e(etiqueta(m.a))}">
      <span class="line-tag">${e(t.txt)}</span>
      <span><b>${e(m.legajo)}</b> ${e(m.nombre)}${m.regimen !== 'Rotativo' ? ' <span class="muted">(diario)</span>' : ''}</span>
      <span class="muted">${det}</span></div>`;
  }

  function chipPermanece(p) {
    const dias = difDias(p.desde, p.hasta) + 1;
    const fin = p.abierto ? 'sin fecha de bajada cargada' : `baja el ${corta(p.hasta)}`;
    return `<span class="chip chip-muted" title="${e(p.legajo)} ${e(p.nombre)} · en obra desde ${corta(p.desde)} · ${e(fin)}">
      <b>${e(p.legajo)}</b> ${e(p.nombre)}${p.regimen !== 'Rotativo' ? ' (diario)' : ''}
      <span class="muted">${p.abierto ? `${dias}+ d` : `hasta ${corta(p.hasta)}`}</span></span>`;
  }

  function render() {
    if (!M.datos) return;
    const { movs, hayMov, base } = seleccion();
    const hoy = M.op.hoy;

    // Totales
    const personas = (g) => new Set(movs.filter((m) => TIPOS[m.tipo].grupo === g).map((m) => m.legajo + m.fecha)).size;
    const ccs = [...new Set(movs.map((m) => m.cc))].sort((a, b) => Number(a) - Number(b));
    const prox = movs.map((m) => m.fecha).sort()[0];
    const kpi = (v, l) => `<div class="card kpi"><span class="kpi-label">${l}</span><span class="kpi-value">${v}</span></div>`;
    $('tjm-kpis').innerHTML = kpi(personas('subidas'), 'Ingresan') + kpi(personas('bajadas'), 'Salen')
      + kpi(new Set(movs.filter((m) => m.tipo === 'traslado_in').map((m) => m.legajo + m.fecha)).size, 'Traslados entre proyectos')
      + kpi(ccs.length, 'Proyectos con cambios') + kpi(prox ? (prox === hoy ? 'Hoy' : larga(prox)) : '—', 'Próximo cambio');

    // Combo de centros de costo (solo los que tienen movimientos en el período)
    const todos = [...new Set(M.datos.movs.map((m) => m.cc))].sort((a, b) => Number(a) - Number(b));
    $('tjm-cc').innerHTML = `<option value="">Todos los centros de costo</option>${todos.map((c) => `<option value="${e(c)}" ${c === M.cc ? 'selected' : ''}>${e(c)} - ${e(nombreCC(c))}</option>`).join('')}`;

    if (!M.ver.size) {
      cuerpo().innerHTML = '<section class="card"><div class="empty-state">Tildá al menos una opción en «Mostrar».</div></section>';
      return;
    }
    if (!movs.length) {
      cuerpo().innerHTML = `<section class="card"><div class="empty-state">No hay movimientos entre el ${corta(M.desde)} y el ${corta(M.datos.limite)} con estos filtros.</div></section>`;
      return;
    }

    // Columnas visibles según el filtro "Mostrar"
    const verE = M.ver.has('subidas') || M.ver.has('traslados');
    const verS = M.ver.has('bajadas') || M.ver.has('traslados');
    const verP = M.ver.has('permanecen');
    const nada = '<span class="muted">—</span>';
    const ancho = ` style="width:${Math.floor(88 / ([verE, verS, verP].filter(Boolean).length || 1))}%"`;

    const html = ccs.map((cc) => {
      const lista = movs.filter((m) => m.cc === cc);
      const fechas = [...new Set(lista.map((m) => m.fecha))].sort();
      const nE = lista.filter((m) => m.lado === 'entra').length;
      const nS = lista.filter((m) => m.lado === 'sale').length;
      let nFilas = 0;
      const filas = fechas.map((f) => {
        const en = lista.filter((m) => m.fecha === f && m.lado === 'entra').sort((a, b) => a.nombre.localeCompare(b.nombre));
        const sa = lista.filter((m) => m.fecha === f && m.lado === 'sale').sort((a, b) => a.nombre.localeCompare(b.nombre));
        const enDias = difDias(hoy, f);
        const cuando = enDias === 0 ? 'hoy' : enDias === 1 ? 'mañana' : enDias === -1 ? 'ayer'
          : enDias < 0 ? `hace ${-enDias} días` : `en ${enDias} días`;
        const pe = permanecen(M.datos.ops, cc, f);
        // Dotación al cierre del día (sin los que bajan) y cruce (todos los que estuvieron ese día), sin depender de "Mostrar".
        const delDia = base.filter((m) => m.cc === cc && m.fecha === f);
        const enDot = new Set(delDia.filter((m) => m.lado === 'entra').map((m) => m.legajo)).size;
        const saDot = new Set(delDia.filter((m) => m.lado === 'sale').map((m) => m.legajo)).size;
        const dot = enDot + pe.length;
        if (!hayMov && !pe.length) return '';
        nFilas += 1;
        return `<tr>
          <td><b>${larga(f)}</b><div class="muted">${cuando}</div>
            <div class="muted" title="Dotación: quedan en el proyecto al cierre del día (ingresan + permanecen, sin los que salen).${saDot ? ' Cruce: todos los que estuvieron ese día (dotación + los que salen).' : ''}">Dotación: <b>${dot}</b>${saDot ? ` · cruce: <b>${dot + saDot}</b>` : ''}</div></td>
          ${verE ? `<td>${en.map(chip).join('') || nada}</td>` : ''}
          ${verS ? `<td>${sa.map(chip).join('') || nada}</td>` : ''}
          ${verP ? `<td>${pe.length ? `<div class="help" style="margin-bottom:4px">${pe.length} ${pe.length === 1 ? 'sigue' : 'siguen'} en obra</div><div class="chip-row">${pe.map(chipPermanece).join('')}</div>` : nada}</td>` : ''}</tr>`;
      }).join('');
      if (!nFilas) return '';
      return `<details class="card card-fold" open>
        <summary class="card-head" style="padding-bottom: 12px"><span class="chip-row"><i class="swatch" style="--cell:${e(colorCC(cc))}"></i>
          <b>${e(cc)} - ${e(nombreCC(cc))}</b>
          ${verE ? `<span class="chip chip-ok">${nE} ingresan</span>` : ''}${verS ? `<span class="chip chip-bad">${nS} salen</span>` : ''}
          <span class="muted">${fechas.length} ${fechas.length === 1 ? 'fecha' : 'fechas'} de cambio</span></span>${ic('chevron-down', 'i-sm fold-ico')}</summary>
        <div class="table-scroll"><table class="tbl tbl-static tbl-auto"><thead><tr><th style="width:130px">Fecha</th>${verE ? `<th${ancho}>Ingresan</th>` : ''}${verS ? `<th${ancho}>Salen</th>` : ''}${verP ? `<th${ancho}>Permanecen</th>` : ''}</tr></thead>
        <tbody>${filas}</tbody></table></div></details>`;
    }).join('');
    cuerpo().innerHTML = html || `<section class="card"><div class="empty-state">No hay datos entre el ${corta(M.desde)} y el ${corta(M.datos.limite)} con estos filtros.</div></section>`;
  }

  // ------------------------------------------------------------------ exportar
  function exportar() {
    if (!M.datos) return;
    const { movs: sel, hayMov } = seleccion();
    const movs = sel.slice().sort((a, b) => Number(a.cc) - Number(b.cc) || a.fecha.localeCompare(b.fecha) || a.lado.localeCompare(b.lado));
    const enc = ['Centro de costo', 'Proyecto', 'Fecha', 'Movimiento', 'Legajo', 'Apellido y Nombre', 'Régimen', 'Código anterior', 'Código nuevo', 'Tramo hasta', 'Días del tramo', 'Después', 'En obra desde'];
    const filas = (hayMov ? movs : []).map((m) => [m.cc, nombreCC(m.cc), m.fecha, TIPOS[m.tipo].txt, m.legajo, m.nombre, m.regimen, m.de, m.a,
      m.lado === 'entra' ? (m.hastaAbierto ? `${m.hasta}+` : m.hasta) : m.fecha,
      m.lado === 'entra' ? difDias(m.fecha, m.hasta) + 1 : (m.veniaAbierto ? `${m.diasObra}+` : m.diasObra),
      m.lado === 'entra' && !m.hastaAbierto ? (m.despues ?? '') : '',
      m.lado === 'sale' ? (m.venia || '') : '']);
    // Los que permanecen en cada proyecto en cada fecha de cambio
    const vistos = new Set();
    if (M.ver.has('permanecen')) movs.forEach((m) => {
      const k = `${m.cc}|${m.fecha}`;
      if (vistos.has(k)) return;
      vistos.add(k);
      permanecen(M.datos.ops, m.cc, m.fecha).forEach((p) => filas.push([m.cc, nombreCC(m.cc), m.fecha, 'Permanece', p.legajo, p.nombre, p.regimen,
        m.cc, m.cc, p.abierto ? `${p.hasta}+` : p.hasta, difDias(p.desde, p.hasta) + 1, '', p.desde]));
    });
    filas.sort((a, b) => Number(a[0]) - Number(b[0]) || String(a[2]).localeCompare(String(b[2])));
    const csv = [enc, ...filas].map((r) => r.map((v) => `"${String(v ?? '').replace(/"/g, '""')}"`).join(';')).join('\r\n');
    const blob = new Blob(['﻿' + csv], { type: 'text/csv;charset=utf-8' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `Proximos_movimientos_${M.desde}_a_${M.datos.limite}.csv`;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  }

  // ------------------------------------------------------------------ pestañas
  function estructura() {
    return `
      <section class="card" aria-label="Filtros de próximos movimientos">
        <div class="toolbar">
          <label class="range">Desde <input type="date" class="input input-sm" id="tjm-desde"></label>
          <select class="chip-select" id="tjm-hor" aria-label="Horizonte">
            ${[7, 14, 30, 45, 60, 90].map((n) => `<option value="${n}">Próximos ${n} días</option>`).join('')}</select>
          <select class="chip-select" id="tjm-reg" aria-label="Régimen">
            <option value="Rotativo">Solo rotativos</option><option value="Diario">Solo diarios</option><option value="">Todos los regímenes</option></select>
          <select class="chip-select" id="tjm-cc" aria-label="Centro de costo"><option value="">Todos los centros de costo</option></select>
          <details class="dropdown" id="tjm-ver"><summary class="chip-select" id="tjm-ver-txt" aria-label="Mostrar"></summary>
            <div class="menu pop-stack ctl-auto"><div class="menu-head">Mostrar</div><div class="pop-list" style="border: 0">
              ${OPCIONES_VER.map(([v, t]) => `<label><input type="checkbox" value="${v}"> ${t}</label>`).join('')}</div>
              <div class="pop-foot" style="margin-top: 0"><button type="button" class="btn btn-sm" data-todo="1">Todos</button><button type="button" class="btn btn-sm btn-ghost" data-todo="0">Ninguno</button></div>
            </div></details>
          <div class="search">${ic('search')}<input type="search" class="input" id="tjm-bus" placeholder="Legajo o nombre" aria-label="Buscar legajo o nombre" autocomplete="off"></div>
          <span class="spacer"></span>
          <button type="button" class="btn btn-sm" id="tjm-hoy">Hoy</button>
          <button type="button" class="btn btn-sm btn-icon" id="tjm-rec" title="Actualizar" aria-label="Actualizar">${ic('refresh', 'i-sm')}</button>
          <button type="button" class="btn btn-sm" id="tjm-exp" title="Exporta los movimientos filtrados (CSV compatible con Excel)">${ic('download', 'i-sm')}Exportar</button>
        </div>
        <p class="help" style="padding: 0 16px 12px">Cambios de turno desde la fecha elegida: quién sube, quién baja, quién se traslada de un centro de costo a otro (y hasta cuándo) y quién permanece en obra.</p>
      </section>
      <div class="kpis kpis-tight" id="tjm-kpis"></div>
      <div id="tjm-cuerpo" class="stack"></div>`;
  }

  function enlazar() {
    $('tjm-desde').value = M.desde || '';
    $('tjm-hor').value = String(M.horizonte);
    $('tjm-reg').value = M.regimen;
    $('tjm-bus').value = M.buscar;
    $('tjm-desde').addEventListener('change', (ev) => { if (ev.target.value) { M.desde = ev.target.value; cargar(); } });
    $('tjm-hor').addEventListener('change', (ev) => { M.horizonte = Number(ev.target.value); cargar(); });
    $('tjm-reg').addEventListener('change', (ev) => { M.regimen = ev.target.value; render(); });
    $('tjm-cc').addEventListener('change', (ev) => { M.cc = ev.target.value; render(); });
    const ver = $('tjm-ver');
    const marcar = () => {
      ver.querySelectorAll('input').forEach((c) => { c.checked = M.ver.has(c.value); });
      $('tjm-ver-txt').textContent = `Mostrar: ${textoVer().toLowerCase()}`;
    };
    marcar();
    ver.addEventListener('change', (ev) => {
      if (ev.target.checked) M.ver.add(ev.target.value); else M.ver.delete(ev.target.value);
      marcar(); render();
    });
    ver.addEventListener('click', (ev) => {
      const b = ev.target.closest('[data-todo]');
      if (!b) return;
      M.ver = new Set(b.dataset.todo === '1' ? OPCIONES_VER.map(([v]) => v) : []);
      marcar(); render();
    });
    document.addEventListener('click', (ev) => { if (ver.open && !ver.contains(ev.target)) ver.open = false; });
    $('tjm-bus').addEventListener('input', (ev) => { M.buscar = ev.target.value; render(); });
    $('tjm-hoy').addEventListener('click', () => { M.desde = M.op?.hoy || M.desde; $('tjm-desde').value = M.desde; cargar(); });
    $('tjm-rec').addEventListener('click', () => cargar(true));
    $('tjm-exp').addEventListener('click', exportar);
  }

  /* Cambia de pestaña sin recargar la grilla (abrir=true la vuelve a pedir al servidor). */
  function mostrar(vista, abrir) {
    M.vista = vista;
    document.querySelectorAll('#tjm-tabs .tjm-tab').forEach((b) => b.classList.toggle('on', b.dataset.v === vista));
    const grilla = $('tj-root');
    if (grilla) grilla.style.display = vista === 'grilla' ? '' : 'none';
    const acciones = $('tj-head-acciones');
    if (acciones) acciones.style.display = vista === 'grilla' ? '' : 'none';
    document.querySelectorAll('#tjm-tabs .tjm-tab').forEach((b) => b.setAttribute('aria-selected', String(b.dataset.v === vista)));
    $('tjm-root').style.display = vista === 'mov' ? '' : 'none';
    if (vista === 'mov') {
      if (!$('tjm-cuerpo')) { $('tjm-root').innerHTML = estructura(); enlazar(); }
      cargar().then(() => { if ($('tjm-desde') && !$('tjm-desde').value) $('tjm-desde').value = M.desde; });
    } else if (abrir && abrirGrilla) {
      abrirGrilla();
    }
  }

  // Los estilos son los del design system (app.css): no se inyecta CSS propio.
  function estilos() {}

  function instalar() {
    const panel = $('panel-tarja');
    const grilla = $('tj-root');
    if (!panel || !grilla || $('tjm-root')) return;
    estilos();
    // Las pestañas Grilla / Próximos movimientos están en el encabezado de la página (.seg); si no, se crean acá.
    let tabs = $('tjm-tabs');
    if (!tabs) {
      tabs = document.createElement('div');
      tabs.id = 'tjm-tabs';
      tabs.className = 'seg';
      tabs.innerHTML = '<button type="button" class="tjm-tab on" data-v="grilla">Grilla</button><button type="button" class="tjm-tab" data-v="mov">Próximos movimientos</button>';
      grilla.parentNode.insertBefore(tabs, grilla);
    }
    const raiz = document.createElement('div');
    raiz.id = 'tjm-root';
    raiz.className = 'stack';
    raiz.style.display = 'none';
    grilla.parentNode.insertBefore(raiz, grilla.nextSibling);
    // La grilla ya quedó dibujada al volver a su pestaña: no hace falta pedirla de nuevo.
    tabs.addEventListener('click', (ev) => { const b = ev.target.closest('.tjm-tab'); if (b) mostrar(b.dataset.v, !grilla.childElementCount); });
    // El botón "Tarja General" de la barra lateral llama a window.tjAbrir: siempre vuelve a la pestaña Grilla.
    abrirGrilla = window.tjAbrir;
    window.tjAbrir = () => mostrar('grilla', true);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', instalar); else instalar();
  window.tjmAbrir = () => mostrar('mov');
  // Cálculo puro, para las pruebas (tests/test_tarja_movimientos.py).
  window.TarjaMovimientos = { procesar, permanecen };
})();
