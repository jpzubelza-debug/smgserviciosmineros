/* Operaciones › Tarja General (Registro Diario).
 * Grilla operador × día: cada celda lleva el ID de un centro de costo o un código de estado. Las horas, los cambios de turno
 * y la columna Obra los calcula el servidor (tarja.py). Carga rápida (escribir, seleccionar/arrastrar un rango, copiar/pegar),
 * autofiltros tipo Excel en cada columna, panel de horas por día, configuración y exportación a Excel.
 * Usa los estilos cert-* del tema claro y showToast / opPuede de operaciones.html.
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
  const OPS_NUM = [['neq', 'Igual a'], ['gt', 'Mayor que'], ['lt', 'Menor que'], ['between', 'Entre'], ['top', '10 mejores']];

  const T = {
    op: null, g: null, desde: '', hasta: '', cargado: false, ocupado: false,
    filtros: {}, orden: null, rapidos: { cc: '', regimen: '', q: '' },
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
  const colorDe = (cel) => (!cel ? null : (cel.e ? ((codigo(cel.c) || {}).color || '#cbd5e1') : paleta(cel.c)));

  // ---------------------------------------------------------------- estilos propios de la grilla
  function estilos() {
    if ($('tj-estilos')) return;
    const s = document.createElement('style');
    s.id = 'tj-estilos';
    s.textContent = `
      #tj-root .tj-bar { display:flex; flex-wrap:wrap; gap:8px 10px; align-items:flex-end; }
      #tj-root .tj-bar .cert-field select, #tj-root .tj-bar .cert-field input { height:32px; }
      #tj-root .tj-info { display:flex; flex-wrap:wrap; gap:8px 14px; align-items:center; justify-content:space-between; margin:10px 0 8px; }
      #tj-root .tj-ley { display:flex; flex-wrap:wrap; gap:4px 10px; font-size:11.5px; color:var(--c-muted); }
      #tj-root .tj-ley i { display:inline-block; width:14px; height:12px; border-radius:2px; vertical-align:-2px; margin-right:4px; border:1px solid rgba(0,0,0,.08); }
      #tj-root .tj-scroll { overflow:auto; max-height:calc(100vh - 300px); min-height:280px; border:1px solid var(--c-line); border-radius:6px; background:#fff; position:relative; user-select:none; }
      #tj-root table.tj-t { border-collapse:separate; border-spacing:0; font-size:12px; }
      #tj-root .tj-t th, #tj-root .tj-t td { border-right:1px solid var(--c-line); border-bottom:1px solid var(--c-line); padding:0 4px; height:26px; white-space:nowrap; background:#fff; color:var(--c-text); }
      #tj-root .tj-t thead th { position:sticky; top:0; z-index:3; background:var(--c-blue-head); color:var(--c-navy); font-weight:700; height:40px; vertical-align:middle; }
      #tj-root .tj-t .fx { position:sticky; z-index:2; }
      #tj-root .tj-t thead th.fx { z-index:4; }
      #tj-root .tj-t .fx1 { left:0; width:70px; min-width:70px; max-width:70px; }
      #tj-root .tj-t .fx2 { left:70px; width:220px; min-width:220px; max-width:220px; overflow:hidden; text-overflow:ellipsis; }
      #tj-root .tj-t .fx3 { left:290px; width:250px; min-width:250px; max-width:250px; overflow:hidden; text-overflow:ellipsis; box-shadow:3px 0 4px -2px rgba(15,36,64,.18); }
      #tj-root .tj-t th .tj-h { display:flex; align-items:center; justify-content:space-between; gap:2px; }
      #tj-root .tj-t th.dia { width:40px; min-width:40px; max-width:40px; padding:0 2px; text-align:center; line-height:1.05; }
      #tj-root .tj-t th.dia .tj-h { flex-direction:column; justify-content:center; gap:1px; }
      #tj-root .tj-t th.dia small { font-weight:600; font-size:10.5px; color:var(--c-muted); }
      #tj-root .tj-t th.dia .tj-mes { font-style:normal; font-size:9.5px; font-weight:800; text-transform:uppercase; color:var(--c-blue); line-height:1; }
      #tj-root .tj-t th.dia.hoy .tj-mes { color:#fff; }
      #tj-root .tj-t th.m1, #tj-root .tj-t td.m1 { border-left:2px solid var(--c-navy); }
      #tj-root .tj-t thead th.dia { height:46px; }
      #tj-root .tj-t th.dia.fin, #tj-root .tj-t td.fin:not([style]) { background:#eef2f7; }
      #tj-root .tj-t th.dia.fer { background:#f5d0fe; }
      #tj-root .tj-t th.dia.hoy { background:var(--c-blue); color:#fff; } #tj-root .tj-t th.dia.hoy small { color:#dbeafe; }
      #tj-root .tj-t td.hoyc { box-shadow: inset 2px 0 0 var(--c-blue), inset -2px 0 0 var(--c-blue); }
      #tj-root .tj-t th.tot, #tj-root .tj-t td.tot { text-align:right; min-width:54px; background:#f3f7fd; }
      #tj-root .tj-t th.tot { background:#cfdff3; }
      #tj-root .tj-t td.d { text-align:center; cursor:cell; font-weight:600; font-size:11.5px; padding:0; }
      #tj-root .tj-t td.d.ct { box-shadow: inset 0 -4px 0 ${T.op ? T.op.color_cambio_turno : '#d97706'}; }
      #tj-root .tj-t td.d.hoyc.ct { box-shadow: inset 0 -4px 0 #d97706, inset 2px 0 0 var(--c-blue), inset -2px 0 0 var(--c-blue); }
      #tj-root .tj-t td.d.man::after { content:''; position:relative; display:inline-block; width:0; height:0; margin-left:1px; vertical-align:top;
        border-left:5px solid transparent; border-top:5px solid var(--c-red); }
      #tj-root .tj-t td.d.sel { outline:2px solid var(--c-blue); outline-offset:-2px; filter:brightness(.93); }
      #tj-root .tj-t td.d.act { outline:2px solid var(--c-navy); outline-offset:-2px; }
      #tj-root .tj-t tbody tr:hover td.fx { background:#f7faff; }
      #tj-root .tj-t td.fx2 { cursor:pointer; } #tj-root .tj-t td.fx2:hover { color:var(--c-blue); text-decoration:underline; }
      #tj-root .tj-reg { display:inline-block; font-size:10px; font-weight:700; padding:0 4px; border-radius:3px; margin-right:4px; background:var(--c-blue-soft); color:var(--c-blue); }
      #tj-root .tj-reg.dia { background:#ecfdf5; color:var(--c-green); }
      #tj-root .tj-pend { color:var(--c-red); font-weight:800; }
      #tj-root .tj-est { color:var(--c-muted); }
      .tj-fbtn { border:1px solid var(--c-line-strong); background:#fff; border-radius:3px; width:17px; height:16px; padding:0; cursor:pointer; display:inline-grid; place-items:center; flex:none; color:var(--c-muted); }
      .tj-fbtn svg { width:10px; height:10px; fill:currentColor; } .tj-fbtn.on { background:var(--c-blue); border-color:var(--c-blue); color:#fff; }
      .tj-menu { position:fixed; z-index:140; background:#fff; border:1px solid var(--c-line-strong); border-radius:6px; box-shadow:0 12px 32px rgba(15,36,64,.25);
        width:300px; font-size:12.5px; color:var(--c-text); padding:8px; display:flex; flex-direction:column; gap:6px; }
      .tj-menu button.it { border:0; background:none; text-align:left; padding:5px 6px; border-radius:4px; cursor:pointer; font-family:inherit; font-size:12.5px; color:var(--c-text); }
      .tj-menu button.it:hover { background:var(--c-blue-soft); }
      .tj-menu hr { border:0; border-top:1px solid var(--c-line); margin:2px 0; width:100%; }
      .tj-menu .sec { font-weight:700; color:var(--c-navy); font-size:12px; }
      .tj-menu select, .tj-menu input[type=text], .tj-menu input[type=number] { padding:4px 6px; font-size:12.5px; border:1px solid var(--c-line-strong); border-radius:4px; font-family:inherit; }
      .tj-menu .fila { display:flex; gap:4px; } .tj-menu .fila > * { flex:1; min-width:0; }
      .tj-menu .vals { max-height:210px; overflow:auto; border:1px solid var(--c-line); border-radius:4px; padding:4px; }
      .tj-menu .vals label { display:flex; gap:6px; align-items:center; padding:2px 2px; cursor:pointer; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
      .tj-menu .vals label small { color:var(--c-muted); overflow:hidden; text-overflow:ellipsis; }
      .tj-menu .cols { display:flex; flex-wrap:wrap; gap:4px; }
      .tj-menu .cols label { display:inline-flex; gap:4px; align-items:center; border:1px solid var(--c-line); border-radius:4px; padding:2px 6px; cursor:pointer; }
      .tj-menu .cols i { width:12px; height:10px; border-radius:2px; display:inline-block; }
      .tj-menu .pie { display:flex; justify-content:space-between; gap:6px; }
      .tj-ed { position:fixed; z-index:135; }
      .tj-ed input { width:110px; height:26px; padding:2px 6px; font-weight:700; font-size:12.5px; border:2px solid var(--c-blue); border-radius:3px; outline:none; text-transform:uppercase; }
      .tj-sug { background:#fff; border:1px solid var(--c-line-strong); border-radius:5px; box-shadow:0 10px 26px rgba(15,36,64,.22); max-height:260px; overflow:auto; width:340px; margin-top:2px; font-size:12px; }
      .tj-sug div { padding:4px 8px; cursor:pointer; display:flex; gap:8px; align-items:center; } .tj-sug div b { min-width:44px; }
      .tj-sug div.on, .tj-sug div:hover { background:var(--c-blue-soft); }
      .tj-sug i { width:12px; height:12px; border-radius:2px; flex:none; display:inline-block; }
      #tj-root .tj-resumen td.r { text-align:right; }
      /* La regla global de Operaciones (input, select { width:100%; fondo oscuro }) no aplica a estos controles. */
      #tj-root .tj-bar select, #tj-root .tj-bar input { width:auto; }
      #tj-root .tj-bar #tj-mes { width:120px; } #tj-root .tj-bar #tj-anio { width:84px; } #tj-root .tj-bar #tj-f-cc { width:280px; }
      #tj-root .tj-bar input[type=date] { width:140px; }
      #tj-root .tj-bar #tj-f-reg { width:120px; } #tj-root .tj-bar #tj-f-q { width:190px; }
      #tj-root .tj-bar .cert-actions { flex-wrap:nowrap; }
      #tj-modal input[type=checkbox], #tj-modal input[type=color] { width:auto; }
      #tj-modal input[type=color] { width:44px; height:28px; padding:1px; }
      #tj-modal .cert-t input[type=text], #tj-modal .cert-t select { width:auto; }
      #tj-modal input[type=date] { width:auto; }
      .tj-menu input, .tj-menu select, .tj-ed input { background:#fff !important; color:var(--c-text) !important; box-shadow:none; transition:none; }
      .tj-menu input[type=text], .tj-menu select { width:100%; }
      .tj-menu input[type=checkbox] { width:14px; height:14px; margin:0; padding:0; flex:none; accent-color:var(--c-blue); }
      .tj-menu .vals label { justify-content:flex-start; text-align:left; }
      .tj-menu .cols label { flex:0 0 auto; }
      .tj-menu .fila input[type=text] { width:auto; }
      .tj-menu select option { background:#fff; color:var(--c-text); }
    `;
    document.head.appendChild(s);
  }

  // ---------------------------------------------------------------- modal (estilo cert-modal)
  function modal(titulo, cuerpo, pie = '', ancho = 'sm') {
    let bg = $('tj-modal');
    if (!bg) {
      bg = document.createElement('div');
      bg.id = 'tj-modal';
      bg.className = 'cert-modal-bg encima';
      document.body.appendChild(bg);
      bg.addEventListener('click', (ev) => { if (ev.target === bg || ev.target.closest('[data-tj-cerrar]')) cerrarModal(); });
    }
    bg.innerHTML = `<div class="cert-modal ${ancho}"><div class="cert-modal-h"><h3>${titulo}</h3><button class="cert-close" type="button" data-tj-cerrar>×</button></div>
      <div class="cert-modal-b" id="tj-modal-b">${cuerpo}</div>${pie ? `<div class="cert-modal-f">${pie}</div>` : ''}</div>`;
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
      { key: 'obra', label: 'Obra', valor: (o) => o.obra.texto || '', color: (o) => (o.obra.pendiente ? 'pendiente' : null) },
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

  function cumpleCond(v, cond, col, todos) {
    const op = cond.op;
    if (['eq', 'ne', 'starts', 'ends', 'contains', 'notcontains'].includes(op)) {
      const s = String(v ?? '').toLowerCase(); const a = String(cond.a ?? '').toLowerCase();
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
    if (f.cond && f.cond.op && !cumpleCond(v, f.cond, col, todos)) return false;
    if (f.color && f.color.length && !f.color.includes(col.color ? col.color(o) : null)) return false;
    return true;
  }
  function pasaRapidos(o) {
    const r = T.rapidos;
    if (r.regimen && o.regimen !== r.regimen) return false;
    if (r.cc && String(o.obra.cc_id) !== String(r.cc) && !Object.values(o.dias).some((c) => !c.e && c.c === String(r.cc))) return false;
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
      root().innerHTML = '<p class="cert-muted" style="padding:20px">Cargando…</p>';
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
        T.orden = (prefs && prefs.orden) || null;
        T.rapidos = { cc: '', regimen: '', q: '', ...((prefs && prefs.rapidos) || {}) };
        T.mostrarTot = !!(prefs && prefs.mostrarTotales);
        T.cargado = true;
        estilos(); const st = $('tj-estilos'); if (st) { st.remove(); estilos(); }
      } catch (err) { root().innerHTML = `<p class="cert-muted" style="padding:20px">${e(err.message)}</p>`; return; }
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
    root().innerHTML = `
      <div class="seguimiento-header"><div><h2>Tarja General</h2><p>Registro diario de operadores: un centro de costo o un código de estado por día. Horas y cambios de turno se calculan solos.</p></div></div>
      <div class="seguimiento-main"><div class="cert-card"><div class="cert-card-b">
        <div class="tj-bar">
          <div class="cert-field"><label>Período (desde – hasta)</label><div class="cert-actions">
            <button type="button" class="cert-btn sm" id="tj-prev" title="Correr el período un mes atrás">◀</button>
            <input type="date" id="tj-desde" value="${T.desde}" title="Desde">
            <span class="cert-muted">al</span>
            <input type="date" id="tj-hasta" value="${T.hasta}" title="Hasta">
            <button type="button" class="cert-btn sm" id="tj-next" title="Correr el período un mes adelante">▶</button>
            <button type="button" class="cert-btn sm primary" id="tj-aplicar">Aplicar</button></div></div>
          <div class="cert-field"><label>Mes completo</label><div class="cert-actions">
            <select id="tj-mes">${completo ? '' : '<option value="" selected>—</option>'}${opts(MESES.map((m, i) => [i + 1, m]), completo ? mD : '')}</select>
            <select id="tj-anio">${opts(anios, aD)}</select>
            <button type="button" class="cert-btn sm" id="tj-hoy" title="Mes actual">Hoy</button></div></div>
          <div class="cert-field"><label>Centro de costo (obra de hoy o trabajado en el mes)</label>
            <select id="tj-f-cc" style="max-width:320px">${opts([['', 'Todos']].concat(T.op.centros_costos.map((c) => [c.id, `${c.id} - ${c.nombre}`])), r.cc)}</select></div>
          <div class="cert-field"><label>Régimen de turno</label><select id="tj-f-reg">${opts([['', 'Todos'], ['Rotativo', 'Rotativo'], ['Diario', 'Diario']], r.regimen)}</select></div>
          <div class="cert-field"><label>Buscar legajo o nombre</label><input type="text" id="tj-f-q" value="${e(r.q)}" placeholder="Ej.: 381 o CRUZ" style="width:190px"></div>
          <span style="flex:1"></span>
          <div class="cert-actions">
            <button type="button" class="cert-btn" id="tj-exp-f" title="Exporta lo que se ve en pantalla">Exportar filtrado</button>
            <button type="button" class="cert-btn" id="tj-exp-t">Exportar todo</button>
            ${puede('configurar') ? '<button type="button" class="cert-btn" id="tj-conf">Configuración</button>' : ''}
          </div>
        </div>
        <div class="tj-info">
          <div class="cert-actions"><b id="tj-cuenta"></b><button type="button" class="cert-btn sm" id="tj-borrar-todo">Borrar todos los filtros</button>
            <button type="button" class="cert-btn sm" id="tj-tot"></button>
            <span class="cert-muted" style="font-size:11.5px">${puede('cargar') ? 'Clic o arrastre para seleccionar · escriba el código y Enter · Supr borra · Ctrl+C / Ctrl+V · doble clic: horas del día' : 'Solo consulta'}</span></div>
          <div class="tj-ley" id="tj-ley"></div>
        </div>
        <div class="tj-scroll" id="tj-scroll"></div>
      </div></div>
      <div class="cert-card tj-resumen"><div class="cert-card-h"><h3>Resumen por centro de costo del ${fFecha(T.desde)} al ${fFecha(T.hasta)} (operadores mostrados)</h3></div><div class="cert-card-b" id="tj-res-cc"></div></div></div>`;
    $('tj-prev').addEventListener('click', () => mover(-1));
    $('tj-next').addEventListener('click', () => mover(1));
    $('tj-hoy').addEventListener('click', () => fijarPeriodo(...rangoMes(Number(T.op.hoy.slice(0, 4)), Number(T.op.hoy.slice(5, 7)))));
    const porMes = () => { const m = Number($('tj-mes').value); if (m) fijarPeriodo(...rangoMes(Number($('tj-anio').value), m)); };
    $('tj-mes').addEventListener('change', porMes);
    $('tj-anio').addEventListener('change', porMes);
    $('tj-aplicar').addEventListener('click', () => fijarPeriodo($('tj-desde').value, $('tj-hasta').value));
    ['tj-desde', 'tj-hasta'].forEach((id) => $(id).addEventListener('keydown', (ev) => { if (ev.key === 'Enter') $('tj-aplicar').click(); }));
    $('tj-f-cc').addEventListener('change', (ev) => { T.rapidos.cc = ev.target.value; aplicarFiltros(); });
    $('tj-f-reg').addEventListener('change', (ev) => { T.rapidos.regimen = ev.target.value; aplicarFiltros(); });
    let tq = null;
    $('tj-f-q').addEventListener('input', (ev) => { clearTimeout(tq); tq = setTimeout(() => { T.rapidos.q = ev.target.value; aplicarFiltros(); }, 200); });
    $('tj-tot').addEventListener('click', () => { T.mostrarTot = !T.mostrarTot; renderGrilla(); guardarPrefs(); });
    $('tj-borrar-todo').addEventListener('click', () => { T.filtros = {}; T.orden = null; T.rapidos = { cc: '', regimen: '', q: '' }; render(); guardarPrefs(); });
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
    $('tj-ley').innerHTML = `<span><i style="background:${T.op.centros_costos[0] ? T.op.centros_costos[0].color : '#cfe3fb'}"></i>Centro de costo (color por obra)</span>
      <span><i style="background:#fff;box-shadow:inset 0 -4px 0 ${T.op.color_cambio_turno}"></i>Cambio de turno</span>
      <span><i style="background:#fff;border-top:5px solid var(--c-red);border-left:5px solid transparent;width:0;height:0"></i>Horas a mano</span>
      ${k.map((c) => `<span title="${e(c.descripcion)}"><i style="background:${e(c.color)}"></i>${e(c.codigo)} ${e(c.descripcion)}</span>`).join('')}`;
  }

  function aplicarFiltros(guardar = true) {
    T.visibles = filtrar();
    T.rects = []; T.act = null;
    renderGrilla();
    if (guardar) guardarPrefs();
  }

  const ICONO_F = '<svg viewBox="0 0 10 10"><path d="M0 1h10L6 5.5V10L4 8.8V5.5z"/></svg>';
  const ICONO_V = '<svg viewBox="0 0 10 10"><path d="M1 3h8L5 8z"/></svg>';
  const fbtn = (k) => `<button type="button" class="tj-fbtn ${filtroActivo(k) || (T.orden && T.orden.key === k) ? 'on' : ''}" data-tj-f="${k}" title="Filtrar y ordenar">${filtroActivo(k) ? ICONO_F : ICONO_V}</button>`;

  function renderGrilla() {
    const g = T.g; const hoy = g.hoy;
    const cab = `<tr><th class="fx fx1"><div class="tj-h">Legajo ${fbtn('legajo')}</div></th><th class="fx fx2"><div class="tj-h">Apellido y Nombre ${fbtn('nombre')}</div></th>
      <th class="fx fx3"><div class="tj-h">Obra (hoy) ${fbtn('obra')}</div></th>
      ${g.dias.map((d, i) => `<th class="dia ${d.fecha === hoy ? 'hoy' : ''} ${d.feriado ? 'fer' : (d.dow >= 5 ? 'fin' : '')} ${d.dia === 1 && i > 0 ? 'm1' : ''}" title="${fFecha(d.fecha)}${d.feriado ? ` · Feriado: ${e(d.feriado)}` : ''}">
        <div class="tj-h">${i === 0 || d.dia === 1 ? `<em class="tj-mes">${MES_CORTO[d.mes - 1]}</em>` : ''}<span>${d.dia}</span><small>${INI[d.dow]}</small>${fbtn(`d${i + 1}`)}</div></th>`).join('')}
      ${T.mostrarTot ? TOTALES.map(([k, c, l]) => `<th class="tot" title="${l}"><div class="tj-h">${c} ${fbtn(k)}</div></th>`).join('') : ''}</tr>`;
    const filas = T.visibles.map((o, ri) => {
      const obra = o.obra.pendiente
        ? `<span class="tj-pend" title="Pendiente: hoy sin cargar${o.obra.cc_id ? ` (último centro: ${o.obra.cc_id} - ${e(descCodigo(o.obra.cc_id))})` : ''}">P</span> <span class="tj-est">Pendiente</span>`
        : (o.obra.estado ? `<span class="tj-est">${e(o.obra.texto)}</span>` : e(o.obra.texto));
      const dias = g.dias.map((d, ci) => {
        const cel = o.dias[d.fecha];
        let cls = `d${d.fecha === hoy ? ' hoyc' : ''}${d.dow >= 5 || d.feriado ? ' fin' : ''}${d.dia === 1 && ci > 0 ? ' m1' : ''}`;
        if (!cel) return `<td class="${cls}" data-r="${ri}" data-c="${ci}"></td>`;
        if (cel.ct) cls += ' ct';
        if (cel.m) cls += ' man';
        const bg = colorDe(cel);
        const tip = `${cel.c} - ${descCodigo(cel.c)}\n${nf(cel.h)} h efectivas + ${nf(cel.b)} h break${cel.ct ? '\nCambio de turno (subida / bajada)' : ''}${cel.m ? '\nHoras editadas a mano' : ''}${cel.o ? `\nObs.: ${cel.o}` : ''}`;
        return `<td class="${cls}" data-r="${ri}" data-c="${ci}" style="background:${bg};color:${textoOscuro(bg) ? '#0f2440' : '#fff'}" title="${e(tip)}">${e(cel.c)}</td>`;
      }).join('');
      return `<tr><td class="fx fx1">${e(o.legajo)}</td><td class="fx fx2" data-tj-op="${e(o.legajo)}" title="${e(o.nombre)} · Régimen ${e(o.regimen)}"><span class="tj-reg ${o.regimen === 'Diario' ? 'dia' : ''}">${o.regimen === 'Diario' ? 'DIA' : 'ROT'}</span>${e(o.nombre)}</td>
        <td class="fx fx3" title="${e(o.obra.pendiente ? 'Pendiente: hoy sin cargar' : o.obra.texto)}">${obra}</td>${dias}
        ${T.mostrarTot ? TOTALES.map(([k]) => `<td class="tot">${nf(o.totales[k])}</td>`).join('') : ''}</tr>`;
    }).join('');
    $('tj-scroll').innerHTML = `<table class="tj-t"><thead>${cab}</thead><tbody>${filas || `<tr><td class="fx fx1"></td><td class="fx fx2 cert-muted" style="cursor:default;text-decoration:none">Sin operadores para los filtros elegidos.</td><td class="fx fx3"></td></tr>`}</tbody></table>`;
    $('tj-cuenta').textContent = `Mostrando ${T.visibles.length} de ${g.operadores.length} operadores`;
    // Un filtro u orden sobre una columna de totales oculta sigue aplicado: el botón lo avisa.
    const totFiltrado = TOTALES.some(([k]) => filtroActivo(k) || (T.orden && T.orden.key === k));
    $('tj-tot').textContent = T.mostrarTot ? 'Ocultar totales' : `Mostrar totales${totFiltrado ? ' (con filtro)' : ''}`;
    $('tj-tot').classList.toggle('primary', !T.mostrarTot && totFiltrado);
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
    $('tj-res-cc').innerHTML = lista.length ? `<div class="cert-wrap"><table class="cert-t"><thead><tr><th>Centro de costo</th><th>Descripción</th><th>Operadores</th><th>Días-hombre</th><th>Hs efectivas</th><th>Hs break</th></tr></thead><tbody>
      ${lista.map((r) => `<tr><td class="c"><span style="display:inline-block;width:10px;height:10px;border-radius:2px;background:${paleta(r.cc)};margin-right:6px"></span>${e(r.cc)}</td><td>${e(descCodigo(r.cc))}</td><td class="r">${r.ops.size}</td><td class="r">${r.dh}</td><td class="r">${nf(r.h)}</td><td class="r">${nf(r.b)}</td></tr>`).join('')}
      <tr class="tot"><td colspan="3">Total</td><td class="r">${tot.dh}</td><td class="r">${nf(tot.h)}</td><td class="r">${nf(tot.b)}</td></tr></tbody></table></div>`
      : '<p class="cert-muted">No hay días trabajados en el período para los operadores mostrados.</p>';
  }

  // ---------------------------------------------------------------- selección
  const celdaTd = (r, c) => $('tj-scroll').querySelector(`td.d[data-r="${r}"][data-c="${c}"]`);
  const enRect = (q, r, c) => r >= Math.min(q.r0, q.r1) && r <= Math.max(q.r0, q.r1) && c >= Math.min(q.c0, q.c1) && c <= Math.max(q.c0, q.c1);
  function pintarSeleccion() {
    const sc = $('tj-scroll'); if (!sc) return;
    sc.querySelectorAll('td.d.sel, td.d.act').forEach((td) => td.classList.remove('sel', 'act'));
    T.rects.forEach((q) => {
      for (let r = Math.min(q.r0, q.r1); r <= Math.max(q.r0, q.r1); r += 1) {
        for (let c = Math.min(q.c0, q.c1); c <= Math.max(q.c0, q.c1); c += 1) { const td = celdaTd(r, c); if (td) td.classList.add('sel'); }
      }
    });
    if (T.act) { const td = celdaTd(T.act.r, T.act.c); if (td) td.classList.add('act'); }
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
    box.className = 'tj-ed';
    box.style.left = `${Math.min(rc.left, window.innerWidth - 360)}px`;
    box.style.top = `${rc.top}px`;
    box.innerHTML = '<input type="text" autocomplete="off" spellcheck="false"><div class="tj-sug"></div>';
    document.body.appendChild(box);
    const inp = box.querySelector('input'); const sug = box.querySelector('.tj-sug');
    T.editor = { box, inp, sug, idx: 0, lista: [] };
    const pintar = () => {
      const l = sugerencias(inp.value); T.editor.lista = l;
      if (T.editor.idx >= l.length) T.editor.idx = 0;
      sug.innerHTML = l.map((x, i) => `<div data-i="${i}" class="${i === T.editor.idx ? 'on' : ''}"><i style="background:${e(x.col)}"></i><b>${e(x.v)}</b><span>${e(x.d)}</span></div>`).join('')
        || '<div class="cert-muted" style="cursor:default">Sin coincidencias: no es un centro de costo ni un código de estado</div>';
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
    const ops = col.mixto ? [['', '— sin condición —']].concat(OPS_TXT, OPS_NUM) : (col.num ? [['', '— sin condición —']].concat(OPS_NUM) : [['', '— sin condición —']].concat(OPS_TXT));
    const colores = col.mixto ? Object.keys(CATS) : [];
    const muestra = (cat) => ({ trabajado: '#cfe3fb', cambio: T.op.color_cambio_turno, descanso: (codigo('D') || {}).color || '#cbd5e1', vacaciones: (codigo('V') || {}).color || '#9be3b0',
      ausencia: (codigo('F') || {}).color || '#fca5a5', otro: (codigo('VJ') || {}).color || '#c4b5fd', vacia: '#ffffff' }[cat]);
    const m = document.createElement('div');
    m.id = 'tj-menu';
    m.className = 'tj-menu';
    const asc = col.num || col.mixto ? 'Ordenar de menor a mayor' : 'Ordenar de A a Z';
    const desc = col.num || col.mixto ? 'Ordenar de mayor a menor' : 'Ordenar de Z a A';
    m.innerHTML = `<div class="sec">${e(col.label)}</div>
      <button type="button" class="it" data-ord="asc">↑ ${asc}${T.orden && T.orden.key === key && T.orden.dir === 'asc' ? ' ✓' : ''}</button>
      <button type="button" class="it" data-ord="desc">↓ ${desc}${T.orden && T.orden.key === key && T.orden.dir === 'desc' ? ' ✓' : ''}</button>
      <hr>
      ${colores.length ? `<div class="sec">Filtrar por color</div><div class="cols">${colores.map((c) => `<label><input type="checkbox" data-col="${c}" ${(f.color || []).includes(c) ? 'checked' : ''}><i style="background:${muestra(c)};border:1px solid #cbd5e1"></i>${CATS[c]}</label>`).join('')}</div><hr>` : ''}
      <div class="sec">${col.mixto ? 'Filtros de texto / número' : (col.num ? 'Filtros de número' : 'Filtros de texto')}</div>
      <div class="fila"><select id="tj-m-op">${col.mixto ? `<option value="">— sin condición —</option><optgroup label="Texto">${opts(OPS_TXT, f.cond && f.cond.op)}</optgroup><optgroup label="Número">${opts(OPS_NUM, f.cond && f.cond.op)}</optgroup>` : opts(ops, f.cond && f.cond.op)}</select></div>
      <div class="fila"><input type="text" id="tj-m-a" placeholder="valor" value="${e(f.cond ? f.cond.a ?? '' : '')}"><input type="text" id="tj-m-b" placeholder="y" value="${e(f.cond ? f.cond.b ?? '' : '')}"></div>
      <hr>
      <input type="text" id="tj-m-q" placeholder="Buscar">
      <div class="vals" id="tj-m-vals">
        <label><input type="checkbox" id="tj-m-todo"> <b>(Seleccionar todo)</b></label>
        ${vacias ? `<label data-v=""><input type="checkbox" value="" ${!elegidos || elegidos.has('') ? 'checked' : ''}> (Vacías)</label>` : ''}
        ${lista.map((v) => { const s = String(v); const d = col.mixto ? descCodigo(s) : ''; return `<label data-v="${e(s.toLowerCase())} ${e(d.toLowerCase())}"><input type="checkbox" value="${e(s)}" ${!elegidos || elegidos.has(s) ? 'checked' : ''}> ${e(s)} ${d ? `<small>${e(d)}</small>` : ''}</label>`; }).join('')}
      </div>
      <div class="pie"><button type="button" class="cert-btn sm danger" id="tj-m-borrar">Borrar filtro</button>
        <span class="cert-actions"><button type="button" class="cert-btn sm" id="tj-m-cancel">Cancelar</button><button type="button" class="cert-btn sm primary" id="tj-m-ok">Aceptar</button></span></div>`;
    document.body.appendChild(m);
    T.menuEn = performance.now();
    const rc = boton.getBoundingClientRect();
    const left = Math.max(8, Math.min(rc.left - 140, window.innerWidth - 310));
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
      return `<li><b>${e(h.accion.replace(/_/g, ' ').toLowerCase())}</b> · ${e(h.usuario)} · ${e(h.creado_en)}<br><span class="cert-muted">${e(txt(a))} → ${e(txt(n))}</span></li>`;
    };
    const cuerpo = `
      <div class="cert-grid" style="grid-template-columns:1fr 1fr">
        <div class="cert-field"><label>Operador</label><div class="cert-val">${e(o.legajo)} · ${e(o.nombre)}</div></div>
        <div class="cert-field"><label>Fecha</label><div class="cert-val">${fFecha(d.fecha)} (${INI[d.dow]})${d.feriado ? ` · Feriado: ${e(d.feriado)}` : ''}</div></div>
        <div class="cert-field" style="grid-column:span 2"><label>Código</label><div class="cert-val">${cel ? `<b>${e(cel.c)}</b> - ${e(descCodigo(cel.c))}` : '<span class="cert-muted">Sin cargar</span>'}
          ${cel && cel.ct ? ' <span class="cert-badge cert-b-PENDIENTE">Cambio de turno</span>' : ''}${cel && cel.m ? ' <span class="cert-badge cert-b-ANULADO">Horas a mano</span>' : ''}</div></div>
        ${cel ? `<div class="cert-field"><label>Horas efectivas</label><input type="number" step="0.5" min="0" max="24" id="tj-d-h" value="${cel.h}" ${editable ? '' : 'disabled'}></div>
        <div class="cert-field"><label>Horas de break</label><input type="number" step="0.5" min="0" max="24" id="tj-d-b" value="${cel.b}" ${editable ? '' : 'disabled'}></div>
        <div class="cert-field" style="grid-column:span 2"><label>Observaciones</label><textarea id="tj-d-o" rows="2" ${editable ? '' : 'disabled'}>${e(cel.o || '')}</textarea></div>` : ''}
      </div>
      ${cel ? '<p class="cert-muted" style="margin:8px 0 0">Al cambiar las horas, el día queda marcado como editado a mano y los recálculos no lo modifican.</p>' : ''}
      <div class="cert-seccion">Historial del día</div>
      <ul class="cert-timeline">${hist.map(fmtH).join('') || '<li class="cert-muted">Sin cambios registrados.</li>'}</ul>`;
    const pie = `${editable && cel.m ? '<button type="button" class="cert-btn" id="tj-d-rest">Restaurar cálculo automático</button>' : ''}
      <button type="button" class="cert-btn" data-tj-cerrar>Cerrar</button>${editable ? '<button type="button" class="cert-btn primary" id="tj-d-ok">Guardar</button>' : ''}`;
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
    const cuerpo = `<div class="cert-grid" style="grid-template-columns:1fr 1fr">
        <div class="cert-field"><label>Legajo</label><div class="cert-val">${e(o.legajo)}</div></div>
        <div class="cert-field"><label>Apellido y Nombre</label><div class="cert-val">${e(o.nombre)}</div></div>
        <div class="cert-field"><label>Obra (hoy)</label><div class="cert-val">${o.obra.pendiente ? '<span class="tj-pend" style="color:var(--c-red);font-weight:800">P</span> Pendiente: hoy sin cargar' : e(o.obra.texto)}</div></div>
        <div class="cert-field"><label>Régimen de turno</label><select id="tj-o-reg" ${editable ? '' : 'disabled'}>${opts([['Rotativo', 'Rotativo (subida y bajada con media jornada)'], ['Diario', 'Diario (5x2, oficina / taller)']], o.regimen)}</select></div>
      </div>
      <div class="cert-seccion">Totales del ${fFecha(T.desde)} al ${fFecha(T.hasta)}</div>
      <div class="cert-stats" style="grid-template-columns:repeat(4,minmax(0,1fr))">${TOTALES.map(([k, , l]) => `<div class="cert-stat"><span class="lbl">${l}</span><b>${nf(o.totales[k])}</b></div>`).join('')}</div>`;
    modal(`Operador ${e(o.legajo)}`, cuerpo, `<button type="button" class="cert-btn" data-tj-cerrar>Cerrar</button>${editable ? '<button type="button" class="cert-btn primary" id="tj-o-ok">Guardar régimen</button>' : ''}`, 'sm');
    if ($('tj-o-ok')) $('tj-o-ok').addEventListener('click', () => ejecutar(async () => {
      await api(`${API}/regimen`, 'PUT', { legajo: o.legajo, regimen: $('tj-o-reg').value });
      cerrarModal(); await cargarMes();
    }, 'Régimen actualizado; horas recalculadas'));
  }

  // ---------------------------------------------------------------- configuración
  function configuracion(tab) {
    const c = T.op.config;
    const tabs = [['jornada', 'Jornada'], ['codigos', 'Códigos de estado'], ['feriados', 'Feriados'], ['importar', 'Importar histórico']];
    let cuerpo = `<div class="cert-toggle" style="margin-bottom:14px">${tabs.map(([k, l]) => `<button type="button" data-tj-tab="${k}" class="${k === tab ? 'on' : ''}">${l}</button>`).join('')}</div>`;
    if (tab === 'jornada') {
      cuerpo += `<div class="cert-grid" style="grid-template-columns:repeat(2,minmax(0,1fr))">
        <div class="cert-field"><label>Jornada normal: horas efectivas</label><input type="number" step="0.5" id="tj-c-1" value="${c.horas_efectivas}"></div>
        <div class="cert-field"><label>Jornada normal: horas de break</label><input type="number" step="0.5" id="tj-c-2" value="${c.horas_break}"></div>
        <div class="cert-field"><label>Cambio de turno (subida / bajada): horas efectivas</label><input type="number" step="0.5" id="tj-c-3" value="${c.horas_efectivas_cambio_turno}"></div>
        <div class="cert-field"><label>Cambio de turno (subida / bajada): horas de break</label><input type="number" step="0.5" id="tj-c-4" value="${c.horas_break_cambio_turno}"></div></div>
        <p class="cert-muted" style="margin-top:10px">Al guardar se recalculan todos los días, salvo los editados a mano. La media jornada solo se aplica al régimen Rotativo.</p>
        <div class="cert-actions" style="justify-content:flex-end"><button type="button" class="cert-btn primary" id="tj-c-ok">Guardar jornada</button></div>`;
    } else if (tab === 'codigos') {
      const fila = (k) => `<tr data-id="${k ? k.id : ''}">
        <td><input type="text" class="tj-k-cod" value="${e(k ? k.codigo : '')}" style="width:70px;text-transform:uppercase" maxlength="10"></td>
        <td><input type="text" class="tj-k-desc" value="${e(k ? k.descripcion : '')}" style="width:220px"></td>
        <td class="c"><input type="color" class="tj-k-color" value="${e(k ? k.color : '#cbd5e1')}"></td>
        <td><select class="tj-k-cat">${opts(T.op.categorias.map((x) => [x, x.charAt(0) + x.slice(1).toLowerCase()]), k ? k.categoria : 'OTRO')}</select></td>
        <td class="c"><input type="checkbox" class="tj-k-trab" ${k && k.cuenta_como_trabajado ? 'checked' : ''}></td>
        <td class="c"><input type="checkbox" class="tj-k-act" ${!k || k.activo ? 'checked' : ''}></td>
        <td class="c"><button type="button" class="cert-btn sm ${k ? '' : 'primary'}" data-tj-kok>${k ? 'Guardar' : 'Agregar'}</button></td></tr>`;
      cuerpo += `<div class="cert-wrap"><table class="cert-t"><thead><tr><th>Código</th><th>Descripción</th><th>Color</th><th>Categoría (totales)</th><th>Cuenta como trabajado</th><th>Activo</th><th></th></tr></thead>
        <tbody>${T.op.codigos.map(fila).join('')}${fila(null)}</tbody></table></div>
        <p class="cert-muted" style="margin-top:8px">Los números son siempre centros de costo, por eso los códigos empiezan con letra. Un código que cuenta como trabajado suma jornada y continúa el turno. Los códigos no se borran: se desactivan.</p>`;
    } else if (tab === 'importar') {
      cuerpo += `<div class="cert-alert info" style="margin-bottom:12px"><span class="ic">i</span><div><b>Histórico desde la planilla «TARJA GENERAL»</b>
        Elija el Excel (Logistica SMG.xlsm). Primero se simula y se muestra qué entra y qué no; recién al confirmar se graba.
        Se cruza por legajo; los centros de costo deben existir en el sistema; los días ya cargados no se pisan. Antes de grabar se respalda la base.</div></div>
        <div class="cert-actions" style="margin-bottom:12px"><input type="file" id="tj-i-arch" accept=".xlsx,.xlsm" style="width:auto">
          <button type="button" class="cert-btn primary" id="tj-i-sim">Simular</button></div>
        <div id="tj-i-res"></div>`;
    } else {
      const fer = T.op.feriados || [];
      cuerpo += `<div class="cert-actions" style="margin-bottom:10px"><input type="date" id="tj-f-fecha"><input type="text" id="tj-f-desc" placeholder="Descripción" style="width:260px">
        <button type="button" class="cert-btn primary" id="tj-f-add">Agregar feriado</button></div>
        <div class="cert-wrap" style="max-height:360px;overflow:auto"><table class="cert-t"><thead><tr><th>Fecha</th><th>Descripción</th><th></th></tr></thead><tbody>
        ${fer.map((f) => `<tr><td>${fFecha(f.fecha)}</td><td>${e(f.descripcion)}</td><td class="c"><button type="button" class="cert-btn sm danger" data-tj-fdel="${f.fecha}">Quitar</button></td></tr>`).join('')
          || '<tr><td colspan="3" class="cert-muted">No hay feriados cargados.</td></tr>'}</tbody></table></div>
        <p class="cert-muted" style="margin-top:8px">El feriado resalta la columna del día. Quien no trabaja ese día se carga con el código FER; el resto con su centro de costo.</p>`;
    }
    modal('Configuración de la Tarja', cuerpo, '<button type="button" class="cert-btn" data-tj-cerrar>Cerrar</button>', 'md');
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
      $('tj-i-res').innerHTML = `<p class="cert-muted">${aplicar ? 'Importando' : 'Leyendo la planilla y simulando'}… puede tardar un minuto.</p>`;
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
      const kv = (k, v) => `<div class="cert-kv"><span>${k}</span><b>${v}</b></div>`;
      const aplicado = r.aplicado;
      $('tj-i-res').innerHTML = `
        <div class="cert-alert ${aplicado ? '' : 'info'}" style="margin-bottom:10px;${aplicado ? 'border-color:#9bd5ae;background:var(--c-green-soft);color:#14532d' : ''}"><span class="ic" style="${aplicado ? 'background:var(--c-green)' : ''}">${aplicado ? '✓' : 'i'}</span>
          <div><b>${aplicado ? 'Importación aplicada' : 'Simulación: todavía no se grabó nada'}</b>${e(nombre)} · días del ${fFecha(r.desde)} al ${fFecha(r.hasta)}${d.respaldo ? ` · respaldo: ${e(d.respaldo)}` : ''}</div></div>
        <div class="cert-trio" style="grid-template-columns:1fr 1fr">
          <div>${kv('Operadores que se importan', nf(r.filas_importables))}${kv(aplicado ? 'Días importados' : 'Días a importar', nf(r.dias_a_importar))}
            ${kv('Días ya cargados (no se pisan)', nf(r.ya_cargados))}${kv('Excepciones (ver reporte)', nf(r.excepciones))}
            ${d.filas_excluidas.length ? `<div class="cert-seccion">Filas excluidas</div>${d.filas_excluidas.map(([m, n]) => kv(e(m), n)).join('')}` : ''}</div>
          <div><div class="cert-seccion" style="margin-top:0">Celdas sin importar</div>
            <div class="cert-wrap" style="max-height:230px;overflow:auto"><table class="cert-t"><thead><tr><th>Valor</th><th>Motivo</th><th>Celdas</th></tr></thead><tbody>
            ${d.sin_importar.map((x) => `<tr><td>${e(x.valor)}</td><td style="white-space:normal">${e(x.motivo)}</td><td class="r">${nf(x.celdas)}</td></tr>`).join('') || '<tr><td colspan="3" class="cert-muted">Ninguna.</td></tr>'}
            </tbody></table></div></div>
        </div>
        <div class="cert-actions" style="justify-content:flex-end;margin-top:12px">
          <button type="button" class="cert-btn" id="tj-i-rep">Descargar reporte de excepciones</button>
          ${!aplicado && r.dias_a_importar > 0 ? `<button type="button" class="cert-btn primary" id="tj-i-apl">Importar ${nf(r.dias_a_importar)} días</button>` : ''}
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
