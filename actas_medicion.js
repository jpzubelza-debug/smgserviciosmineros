/* Operaciones › Actas de Medición.
 * Pantalla del motor configurable de fojas de medición (actas_medicion.py). Todo cálculo lo hace el servidor;
 * esta pantalla solo edita configuración, muestra resultados y navega hasta los partes diarios.
 * Usa los estilos cert-* del panel de Certificados (tema claro) y las funciones globales showToast / opPuede.
 */
(() => {
  'use strict';
  const API = '/api/actas-medicion';
  const AM = {
    tab: 'actas', opciones: null, contratos: [], contratoSel: null, contrato: null, actas: [], acta: null, reportes: [],
    repSel: null, plantillas: [], ed: {}, catalogo: null, campos: [], refs: [], ocupado: false,
  };
  const ACCIONES = [['consultar', 'Consultar'], ['generar', 'Generar'], ['configurar', 'Configurar'], ['aprobar', 'Aprobar'], ['emitir', 'Emitir'], ['anular', 'Anular']];
  const SALIDAS = [['registrado', 'Registrado'], ['minimas', 'Mínimas'], ['extras', 'Extras'], ['certificar', 'A certificar']];
  const REGLAS = [['SIN_MINIMO', 'Sin mínimo (se certifica lo registrado)'], ['TOPE', 'Tope (mínimas hasta el mínimo, extras el exceso)'],
    ['MINIMO_GARANTIZADO', 'Mínimo garantizado'], ['TRAMOS', 'Por tramos / escalas'], ['FORMULA', 'Fórmula libre']];
  const AMBITOS = [['UNIDAD', 'Por unidad'], ['ITEM', 'Por ítem'], ['EQUIPO', 'Por equipo']];
  const MODOS = [['PARTE', 'Por parte diario'], ['FIJO', 'Fijo por período'], ['MIXTO', 'Mixto (fijo + partes)']];
  const TIPOS_MEDIDA = [['DIFERENCIA', 'Diferencia final − inicio'], ['CAMPO', 'Valor cargado'], ['FIJO', 'Valor fijo por parte'], ['FORMULA', 'Fórmula']];
  const FORMATOS = [['texto', 'Texto'], ['entero', 'Entero'], ['decimal2', '2 decimales'], ['porcentaje', '%'], ['moneda', 'Moneda'], ['fecha', 'Fecha']];
  const NIVELES = [['ITEM', 'Por ítem'], ['UNIDAD', 'Por unidad'], ['EQUIPO', 'Por equipo'], ['PARTE', 'Por parte diario']];
  const AGRUPAR = [['', 'Sin agrupar'], ['seccion', 'Sección'], ['item', 'Ítem'], ['unidad', 'Unidad del ítem'], ['equipo', 'Equipo']];
  const OPS_FILTRO = [['IGUAL', 'igual a'], ['DISTINTO', 'distinto de'], ['CONTIENE', 'contiene'], ['EMPIEZA', 'empieza con'], ['NO_VACIO', 'no vacío'], ['VACIO', 'vacío']];
  const VALIDACIONES = ['FINAL_MENOR_INICIO', 'INICIO_DISTINTO_ANTERIOR', 'DUPLICADO_EQUIPO_FECHA_TURNO', 'DIAS_SIN_PARTE', 'NRO_PARTE_REPETIDO',
    'CANTIDAD_MAXIMA', 'EQUIPO_SIN_ITEM', 'EQUIPO_INACTIVO', 'FECHA_INVALIDA', 'SUPERA_CONTRATO'];
  const FILAS_NUEVAS = {
    conceptos: () => ({ codigo: 'NUEVO', titulo: 'Nuevo concepto', base: 'litros', unidad: '', signo: -1, precio: null, activo: true, aplica_secciones: [], aplica_items: [] }),
    indicadores: () => ({ codigo: 'nuevo', titulo: 'Nuevo indicador', formula: 'registrado / dias', decimales: 2 }),
    validaciones: () => ({ codigo: 'CANTIDAD_MAXIMA', activa: true, severidad: 'AVISO', param: {} }),
    estados: () => ({ codigo: 'NUEVO', titulo: 'Nuevo estado', permiso: 'generar', congela: false }),
    campos: () => ({ etiqueta: 'Campo', valor: '' }),
    firmas: () => ({ titulo: 'Firma', cargo: '', empresa: '' }),
    columnas: () => ({ valor: 'codigo', titulo: 'Columna', grupo: '', formato: 'texto', total: false }),
    reportes: () => ({ codigo: `reporte_${Date.now() % 100000}`, titulo: 'Nuevo reporte', activo: true, nivel: 'ITEM', agrupar: '', una_tabla: true,
      encabezado: true, firmas: false, mostrar_conceptos: false, columnas: [{ valor: 'codigo', titulo: 'Ítem', formato: 'texto' }] }),
    medidas: () => ({ codigo: 'nueva', titulo: 'Nueva medida', unidad: '', tipo: 'CAMPO', campo: 'viajes' }),
    factores: () => ({ codigo: 'nuevo_factor', titulo: 'Nuevo factor', unidad: '' }),
    turnos: () => ({ codigo: 'TURNO', alias: '' }),
    filtros: () => ({ campo: 'cliente', op: 'CONTIENE', valor: '' }),
    tramos: () => ({ hasta: null, salida: 'extras', factor: 1 }),
  };

  // ---------------------------------------------------------------- utilidades
  const $ = (id) => document.getElementById(id);
  const e = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const puede = (accion) => (typeof opPuede === 'function' ? opPuede('actas_medicion', accion) : true);
  const toast = (msg, tipo = 'info') => (typeof showToast === 'function' ? showToast(e(msg), tipo) : alert(msg));
  const clon = (o) => JSON.parse(JSON.stringify(o ?? null));
  const fFecha = (iso) => (iso && /^\d{4}-\d{2}-\d{2}/.test(iso) ? `${iso.slice(8, 10)}/${iso.slice(5, 7)}/${iso.slice(0, 4)}` : (iso ?? ''));
  const nf = (v, d = 0) => Number(v).toLocaleString('es-AR', { minimumFractionDigits: d, maximumFractionDigits: d });
  function fmt(v, formato) {
    if (v === null || v === undefined || v === '') return '';
    if (formato === 'fecha') return fFecha(String(v));
    const n = Number(v);
    if (formato === 'texto' || Number.isNaN(n) || typeof v === 'boolean') return String(v);
    if (formato === 'entero') return nf(n, Number.isInteger(n) ? 0 : 2);
    if (formato === 'porcentaje') return `${nf(n * 100, 2)} %`;
    if (formato === 'moneda' || formato === 'decimal2') return nf(n, 2);
    return nf(n, Number.isInteger(n) ? 0 : 2);
  }
  async function api(url, metodo = 'GET', cuerpo) {
    const op = { method: metodo, headers: {} };
    if (cuerpo !== undefined) { op.headers['Content-Type'] = 'application/json'; op.body = JSON.stringify(cuerpo); }
    let res;
    try { res = await fetch(url, op); } catch (err) { throw new Error('No se pudo conectar con el servidor.'); }
    let data = null;
    try { data = await res.json(); } catch (err) { data = null; }
    if (!res.ok) {
      const d = data && data.detail;
      throw new Error(typeof d === 'string' ? d : (Array.isArray(d) ? d.map((x) => x.msg).join('; ') : `Error ${res.status} del servidor.`));
    }
    return data;
  }
  async function ejecutar(fn, okMsg) {
    if (AM.ocupado) return false;
    AM.ocupado = true;
    document.body.style.cursor = 'progress';
    try { await fn(); if (okMsg) toast(okMsg, 'success'); return true; } catch (err) { toast(err.message, 'error'); return false; } finally {
      AM.ocupado = false; document.body.style.cursor = '';
    }
  }
  const getP = (o, p) => p.split('.').reduce((x, k) => (x == null ? undefined : x[k]), o);
  function setP(o, p, v) {
    const ks = p.split('.');
    let x = o;
    for (let i = 0; i < ks.length - 1; i += 1) {
      if (x[ks[i]] == null) x[ks[i]] = /^\d+$/.test(ks[i + 1]) ? [] : {};
      x = x[ks[i]];
    }
    x[ks[ks.length - 1]] = v;
  }
  const opts = (lista, sel) => lista.map(([v, t]) => `<option value="${e(v)}" ${String(v) === String(sel ?? '') ? 'selected' : ''}>${e(t)}</option>`).join('');

  // Controles enlazados: data-b="raíz:ruta" data-t="tipo". Las raíces viven en AM.ed.
  const fIn = (b, v, t = 'txt', at = '') => `<input data-b="${b}" data-t="${t}" value="${e(t === 'lista' && Array.isArray(v) ? v.join(', ') : (v ?? ''))}" ${at}>`;
  const fNum = (b, v, at = '') => fIn(b, v, 'num', `type="number" step="any" ${at}`);
  const fSel = (b, v, lista, t = 'txt', at = '') => `<select data-b="${b}" data-t="${t}" ${at}>${opts(lista, v)}</select>`;
  const fChk = (b, v) => `<input type="checkbox" data-b="${b}" data-t="chk" ${v ? 'checked' : ''}>`;
  const fJson = (b, v, filas = 4) => `<textarea data-b="${b}" data-t="json" rows="${filas}" spellcheck="false" style="font-family:Consolas,monospace;font-size:12px;width:100%">${e(JSON.stringify(v ?? {}, null, 2))}</textarea>`;
  const campo = (etq, html, ayuda = '') => `<div class="cert-field"><label>${e(etq)}${ayuda ? ` <span class="cert-muted" title="${e(ayuda)}">ⓘ</span>` : ''}</label>${html}</div>`;
  function leerControl(el) {
    const t = el.dataset.t;
    if (t === 'chk') return el.checked;
    if (t === 'num') return el.value === '' ? null : Number(el.value);
    if (t === 'lista') return el.value.split(',').map((x) => x.trim()).filter((x) => x !== '');
    if (t === 'json') {
      try { const v = JSON.parse(el.value || '{}'); el.style.outline = ''; return v; } catch (err) { el.style.outline = '2px solid #b91c1c'; throw err; }
    }
    return el.value;
  }
  function enlazar(raiz) {
    const manejar = (ev) => {
      const el = ev.target;
      if (!el.dataset || !el.dataset.b) return;
      const [r, p] = el.dataset.b.split(':');
      try { setP(AM.ed, `${r}.${p}`, leerControl(el)); } catch (err) { /* JSON incompleto: se reintenta al seguir escribiendo */ }
      if (el.dataset.rerender) AM[el.dataset.rerender]();
    };
    raiz.addEventListener('input', manejar);
    raiz.addEventListener('change', manejar);
    raiz.addEventListener('click', (ev) => {
      const b = ev.target.closest('[data-arr]');
      if (!b) return;
      const [r, p] = b.dataset.arr.split(':');
      const ruta = `${r}.${p}`;
      let arr = getP(AM.ed, ruta);
      if (!Array.isArray(arr)) { arr = []; setP(AM.ed, ruta, arr); }
      const i = Number(b.dataset.i);
      if (b.dataset.op === 'add') arr.push((FILAS_NUEVAS[b.dataset.tpl || p.split('.').pop()] || (() => ({})))());
      else if (b.dataset.op === 'del') arr.splice(i, 1);
      else if (b.dataset.op === 'up' && i > 0) [arr[i - 1], arr[i]] = [arr[i], arr[i - 1]];
      else if (b.dataset.op === 'down' && i < arr.length - 1) [arr[i + 1], arr[i]] = [arr[i], arr[i + 1]];
      AM[b.dataset.rerender || 'renderEditor']();
    });
  }
  // Tabla editable de una lista: cols = [{k, t, tipo: txt|num|sel|chk|lista|json, op: [[v,t]]}]
  function tablaLista(raiz, ruta, filas, cols, tpl, rerender = 'renderEditor') {
    const cuerpo = (filas || []).map((f, i) => `<tr>${cols.map((c) => {
      const b = `${raiz}:${ruta}.${i}.${c.k}`;
      const v = f[c.k];
      let h;
      if (c.tipo === 'sel') h = fSel(b, v, c.op, c.vt || 'txt');
      else if (c.tipo === 'num') h = fNum(b, v, 'style="width:90px"');
      else if (c.tipo === 'chk') h = fChk(b, v);
      else if (c.tipo === 'lista') h = fIn(b, v, 'lista');
      else if (c.tipo === 'json') h = fIn(b, JSON.stringify(v ?? {}), 'json', 'style="font-family:Consolas,monospace;width:220px"');
      else h = fIn(b, v, 'txt', c.w ? `style="width:${c.w}"` : '');
      return `<td>${h}</td>`;
    }).join('')}<td class="c" style="white-space:nowrap">
      <button type="button" class="cert-btn sm" data-arr="${raiz}:${ruta}" data-op="up" data-i="${i}" data-rerender="${rerender}" title="Subir">↑</button>
      <button type="button" class="cert-btn sm" data-arr="${raiz}:${ruta}" data-op="down" data-i="${i}" data-rerender="${rerender}" title="Bajar">↓</button>
      <button type="button" class="cert-btn sm danger" data-arr="${raiz}:${ruta}" data-op="del" data-i="${i}" data-rerender="${rerender}" title="Quitar">×</button></td></tr>`).join('');
    return `<div class="cert-wrap"><table class="cert-t ajustar"><thead><tr>${cols.map((c) => `<th>${e(c.t)}</th>`).join('')}<th></th></tr></thead>
      <tbody>${cuerpo || `<tr><td colspan="${cols.length + 1}" class="cert-muted">Sin filas.</td></tr>`}</tbody></table></div>
      <button type="button" class="cert-btn sm" style="margin-top:6px" data-arr="${raiz}:${ruta}" data-op="add" data-tpl="${tpl || ''}" data-rerender="${rerender}">+ Agregar</button>`;
  }
  const tarjeta = (titulo, cuerpo, acciones = '', id = '') => `<div class="cert-card" style="margin-bottom:12px" ${id ? `id="${id}"` : ''}>
    <div class="cert-card-h"><h3>${titulo}</h3><span class="cert-actions">${acciones}</span></div><div class="cert-card-b">${cuerpo}</div></div>`;
  const badge = (estado) => {
    const cls = estado === 'ANULADA' ? 'ANULADO' : (/EMITIDA|APROBADA/.test(estado) ? 'EMITIDO' : 'BORRADOR');
    return `<span class="cert-badge cert-b-${cls}">${e(estado)}</span>`;
  };

  // ---------------------------------------------------------------- modal propio
  function modal(titulo, cuerpo, pie = '', ancho = 'md') {
    let bg = $('am-modal');
    if (!bg) {
      bg = document.createElement('div');
      bg.id = 'am-modal';
      bg.className = 'cert-modal-bg encima';
      document.body.appendChild(bg);
      bg.addEventListener('click', (ev) => { if (ev.target === bg || ev.target.closest('[data-am-cerrar]')) cerrarModal(); });
    }
    bg.innerHTML = `<div class="cert-modal ${ancho}"><div class="cert-modal-h"><h3>${titulo}</h3><button class="cert-close" type="button" data-am-cerrar>×</button></div>
      <div class="cert-modal-b" id="am-modal-b">${cuerpo}</div>${pie ? `<div class="cert-modal-f">${pie}</div>` : ''}</div>`;
    bg.classList.add('open');
    return bg;
  }
  function cerrarModal() { const bg = $('am-modal'); if (bg) bg.classList.remove('open'); }

  // ---------------------------------------------------------------- estructura
  function root() { return $('am-root'); }
  function renderShell() {
    const tabs = [['actas', 'Actas'], ['contratos', 'Contratos'], ['plantillas', 'Plantillas'], ['campos', 'Campos del parte']];
    root().innerHTML = `<div class="seguimiento-header"><div><h2>Actas de Medición</h2>
        <p>Fojas de medición calculadas desde los partes diarios. La forma de medir, calcular y presentar se define en Plantillas y Contratos.</p></div>
        <div class="seguimiento-actions"><span class="cert-toggle">${tabs.map(([k, t]) => `<button type="button" data-am-tab="${k}" class="${AM.tab === k ? 'on' : ''}">${t}</button>`).join('')}</span></div></div>
      <div class="seguimiento-main"><div id="am-body"></div></div>`;
    root().querySelectorAll('[data-am-tab]').forEach((b) => b.addEventListener('click', () => { AM.tab = b.dataset.amTab; AM.acta = null; abrirTab(); }));
  }
  async function abrirTab() {
    renderShell();
    const body = $('am-body');
    body.innerHTML = '<p class="cert-muted">Cargando…</p>';
    try {
      if (!AM.opciones) AM.opciones = await api(`${API}/opciones`);
      if (AM.tab === 'actas') await vistaActas();
      else if (AM.tab === 'contratos') await vistaContratos();
      else if (AM.tab === 'plantillas') await vistaPlantillas();
      else await vistaCampos();
    } catch (err) { body.innerHTML = `<div class="cert-alert err"><span class="ic">!</span><div>${e(err.message)}</div></div>`; }
  }

  // ================================================================= ACTAS
  async function vistaActas() {
    AM.contratos = await api(`${API}/contratos`);
    const body = $('am-body');
    if (!AM.contratos.length) {
      body.innerHTML = tarjeta('Sin contratos', `<p>Primero cree un contrato (pestaña <b>Contratos</b>) desde una plantilla, elija su seguimiento y OC y asigne los equipos (CI) a cada ítem.</p>`);
      return;
    }
    if (!AM.contratoSel || !AM.contratos.some((c) => c.id === AM.contratoSel)) AM.contratoSel = (AM.contratos.find((c) => c.activo) || AM.contratos[0]).id;
    if (AM.acta) { await vistaActa(AM.acta.id); return; }
    AM.actas = await api(`${API}/contratos/${AM.contratoSel}/actas`);
    body.innerHTML = tarjeta('Actas del contrato', `
      <div class="cert-actions" style="margin-bottom:10px">${campo('Contrato', `<select id="am-sel-contrato" style="min-width:320px">${opts(AM.contratos.map((c) => [c.id, `${c.nombre}${c.numero_contrato ? ` · ${c.numero_contrato}` : ''}${c.activo ? '' : ' (inactivo)'}`]), AM.contratoSel)}</select>`)}</div>
      <div class="cert-wrap"><table class="cert-t"><thead><tr><th>N°</th><th>Rev.</th><th>Período</th><th>Fecha</th><th>Estado</th><th>Certificado</th><th></th></tr></thead><tbody>
      ${AM.actas.map((a) => `<tr><td class="c">${a.numero}</td><td class="c">${a.revision}</td><td>${fFecha(a.periodo_desde)} al ${fFecha(a.periodo_hasta)}</td><td>${fFecha(a.fecha)}</td>
        <td>${badge(a.estado)}${a.congelada ? ' <span class="cert-muted" title="Congelada: no cambia aunque cambien partes o configuración">🔒</span>' : ''}</td>
        <td>${a.certificados.map((c) => `${c.numero ? `N° ${c.numero}` : 'borrador'} (${e(c.estado)})`).join(', ') || '—'}</td>
        <td class="c"><button type="button" class="cert-btn sm" data-am-abrir="${a.id}">Abrir</button></td></tr>`).join('') || '<tr><td colspan="7" class="cert-muted">Sin actas.</td></tr>'}
      </tbody></table></div>`, puede('generar') ? '<button type="button" class="cert-btn primary" id="am-nueva-acta">+ Nueva acta</button>' : '');
    $('am-sel-contrato').addEventListener('change', (ev) => { AM.contratoSel = Number(ev.target.value); vistaActas(); });
    body.querySelectorAll('[data-am-abrir]').forEach((b) => b.addEventListener('click', () => vistaActa(Number(b.dataset.amAbrir))));
    if ($('am-nueva-acta')) $('am-nueva-acta').addEventListener('click', nuevaActa);
  }

  async function nuevaActa() {
    const s = await api(`${API}/contratos/${AM.contratoSel}/sugerencia`);
    modal('Nueva acta de medición', `<div class="cert-grid" style="grid-template-columns:repeat(4,1fr)">
      ${campo('N°', `<input id="am-na-num" type="number" value="${s.numero}">`)}${campo('Desde', `<input id="am-na-desde" type="date" value="${s.periodo_desde}">`)}
      ${campo('Hasta', `<input id="am-na-hasta" type="date" value="${s.periodo_hasta}">`)}${campo('Fecha', `<input id="am-na-fecha" type="date" value="${s.fecha}">`)}</div>
      <p class="cert-muted">El período y el número se sugieren según el día de corte y la última acta del contrato. Las cantidades salen de los partes diarios.</p>`,
    '<button class="cert-btn" type="button" data-am-cerrar>Cancelar</button><button class="cert-btn primary" type="button" id="am-na-ok">Crear borrador</button>', 'sm');
    $('am-na-ok').addEventListener('click', () => ejecutar(async () => {
      const a = await api(`${API}/contratos/${AM.contratoSel}/actas`, 'POST', {
        numero: $('am-na-num').value, periodo_desde: $('am-na-desde').value, periodo_hasta: $('am-na-hasta').value, fecha: $('am-na-fecha').value,
      });
      cerrarModal();
      await vistaActa(a.id);
    }, 'Acta creada.'));
  }

  async function vistaActa(id) {
    const body = $('am-body');
    body.innerHTML = '<p class="cert-muted">Calculando desde los partes diarios…</p>';
    const [acta, reportes] = await Promise.all([api(`${API}/actas/${id}`), api(`${API}/actas/${id}/reportes`)]);
    AM.acta = acta;
    AM.reportes = reportes;
    if (!AM.repSel || !reportes.some((r) => r.codigo === AM.repSel)) AM.repSel = reportes[0] && reportes[0].codigo;
    AM.refs = [];
    const res = acta.resultado;
    const estados = acta.estados;
    const iAct = estados.findIndex((x) => x.codigo === acta.estado);
    const sig = estados[iAct + 1], ant = estados[iAct - 1];
    const anulada = acta.estado === 'ANULADA';
    const stepper = estados.map((x, i) => `<span class="cert-pill" style="${i === iAct ? '' : 'background:#eef2f7;color:#64748b'}">${i + 1}. ${e(x.titulo || x.codigo)}${x.congela ? ' 🔒' : ''}</span>`).join(' → ');
    const acciones = [
      '<button type="button" class="cert-btn" id="am-volver">← Actas</button>',
      !acta.congelada && !anulada && puede('generar') ? '<button type="button" class="cert-btn" id="am-editar">Período y ajustes</button>' : '',
      !acta.congelada && !anulada ? '<button type="button" class="cert-btn" id="am-recalc">Recalcular</button>' : '',
      ant && !acta.congelada && !anulada ? `<button type="button" class="cert-btn" data-am-estado="${e(ant.codigo)}">← Volver a ${e(ant.titulo || ant.codigo)}</button>` : '',
      sig && !anulada ? `<button type="button" class="cert-btn primary" data-am-estado="${e(sig.codigo)}" ${puede(sig.permiso || 'generar') ? '' : 'disabled title="Sin permiso"'}>Pasar a ${e(sig.titulo || sig.codigo)}${sig.congela && !acta.congelada ? ' (congela)' : ''}</button>` : '',
      acta.congelada && !anulada && puede('generar') ? '<button type="button" class="cert-btn" id="am-revision">Emitir revisión</button>' : '',
      acta.congelada && !anulada && puede('anular') ? '<button type="button" class="cert-btn danger" id="am-anular">Anular</button>' : '',
      !acta.congelada && puede('generar') ? '<button type="button" class="cert-btn danger" id="am-eliminar">Eliminar borrador</button>' : '',
      `<a class="cert-btn" href="${API}/actas/${id}/excel">Excel</a>`,
      '<button type="button" class="cert-btn" id="am-imprimir">Imprimir</button>',
    ].join('');
    const vals = res.validaciones || [];
    const bloq = vals.filter((v) => v.severidad === 'BLOQUEO').length;
    const valHtml = vals.length ? vals.map((v) => `<details ${v.severidad === 'BLOQUEO' ? 'open' : ''} style="margin-bottom:6px"><summary>
        <span class="cert-badge cert-b-${v.severidad === 'BLOQUEO' ? 'ANULADO' : 'BORRADOR'}">${e(v.severidad)}</span> <b>${e(v.titulo)}</b> (${v.mensajes.length})</summary>
        <ul style="margin:6px 0 0 18px">${v.mensajes.slice(0, 200).map((m) => `<li>${e(m.texto)}${m.partes && m.partes.length ? ` <a href="#" data-am-ref="${ref(m.partes)}">ver partes</a>` : ''}</li>`).join('')}
        ${v.mensajes.length > 200 ? `<li class="cert-muted">… y ${v.mensajes.length - 200} más</li>` : ''}</ul></details>`).join('')
      : '<p class="cert-muted">Sin observaciones en las validaciones activas.</p>';
    body.innerHTML = `
      ${tarjeta(`Acta N° ${acta.numero}${acta.revision ? ` · Rev. ${acta.revision}` : ''} — ${e(acta.contrato.nombre)}`, `
        <div class="cert-actions" style="margin-bottom:8px">${badge(acta.estado)} <span class="cert-muted">Período ${fFecha(acta.periodo_desde)} al ${fFecha(acta.periodo_hasta)} · ${res.totales.partes} partes ·
          ${acta.congelada ? `congelada por ${e(acta.congelada_por)} el ${e(acta.congelada_en)} (no cambia aunque cambien partes o configuración)` : 'cálculo en vivo desde los partes diarios'}</span></div>
        <div style="margin-bottom:8px">${stepper}</div>
        ${acta.certificados.length ? `<div class="cert-alert info"><span class="ic">i</span><div>Certificados: ${acta.certificados.map((c) => `${c.numero ? `N° ${c.numero}` : 'borrador'} (${e(c.estado)})`).join(', ')}</div></div>` : ''}
        ${anulada ? `<div class="cert-alert err"><span class="ic">!</span><div>Anulada por ${e(acta.anulada_por)}: ${e(acta.motivo_anulacion)}</div></div>` : ''}
        <div class="cert-actions">${acciones}</div>`)}
      ${tarjeta(`Validaciones ${bloq ? `<span class="cert-badge cert-b-ANULADO">${bloq} bloquean</span>` : ''}`, valHtml)}
      ${importesHtml(res)}
      ${tarjeta('Reportes', `<div class="cert-toggle" style="margin-bottom:10px">${reportes.map((r) => `<button type="button" data-am-rep="${e(r.codigo)}" class="${r.codigo === AM.repSel ? 'on' : ''}">${e(r.titulo)}</button>`).join('')}</div>
        <div id="am-reporte">${reporteHtml(reportes.find((r) => r.codigo === AM.repSel))}</div>
        <p class="cert-muted" style="margin-top:8px">Clic en una fila para ver los partes diarios que la componen.</p>`)}`;
    $('am-volver').addEventListener('click', () => { AM.acta = null; vistaActas(); });
    if ($('am-recalc')) $('am-recalc').addEventListener('click', () => ejecutar(() => vistaActa(id), 'Recalculado con los partes y la configuración actuales.'));
    if ($('am-editar')) $('am-editar').addEventListener('click', editarActa);
    if ($('am-revision')) $('am-revision').addEventListener('click', () => {
      const motivo = prompt('Motivo de la revisión (se recalcula con los partes y la configuración actuales):');
      if (motivo === null) return;
      ejecutar(async () => { const n = await api(`${API}/actas/${id}/revision`, 'POST', { motivo }); await vistaActa(n.id); }, 'Revisión creada en borrador.');
    });
    if ($('am-anular')) $('am-anular').addEventListener('click', () => {
      const motivo = prompt('Motivo de la anulación (mínimo 10 caracteres):');
      if (!motivo) return;
      ejecutar(async () => { await api(`${API}/actas/${id}/anular`, 'POST', { motivo }); await vistaActa(id); }, 'Acta anulada.');
    });
    if ($('am-eliminar')) $('am-eliminar').addEventListener('click', () => {
      if (!confirm('¿Eliminar este borrador?')) return;
      ejecutar(async () => { await api(`${API}/actas/${id}`, 'DELETE'); AM.acta = null; await vistaActas(); }, 'Borrador eliminado.');
    });
    body.querySelectorAll('[data-am-estado]').forEach((b) => b.addEventListener('click', () => {
      const est = estados.find((x) => x.codigo === b.dataset.amEstado);
      if (est.congela && !acta.congelada && !confirm(`Al pasar a «${est.titulo || est.codigo}» el acta queda congelada: guarda sus resultados, la configuración usada y los partes. ¿Continuar?`)) return;
      ejecutar(async () => { await api(`${API}/actas/${id}/estado`, 'POST', { destino: est.codigo }); await vistaActa(id); }, `Acta en ${est.titulo || est.codigo}.`);
    }));
    body.querySelectorAll('[data-am-rep]').forEach((b) => b.addEventListener('click', () => {
      AM.repSel = b.dataset.amRep;
      body.querySelectorAll('[data-am-rep]').forEach((x) => x.classList.toggle('on', x === b));
      $('am-reporte').innerHTML = reporteHtml(AM.reportes.find((r) => r.codigo === AM.repSel));
    }));
    body.onclick = (ev) => {
      const t = ev.target.closest('[data-am-ref]');
      if (!t) return;
      ev.preventDefault();
      verPartes(AM.refs[Number(t.dataset.amRef)]).catch((err) => toast(err.message, 'error'));
    };
    $('am-imprimir').addEventListener('click', imprimir);
  }

  function ref(ids) { AM.refs.push(ids); return AM.refs.length - 1; }

  function importesHtml(res) {
    const filas = [];
    res.items.forEach((it) => Object.entries(it.importes || {}).forEach(([s, imp]) => {
      if (!(it.facturar || []).includes(s)) return;
      const p = it.precios[s];
      filas.push(`<tr><td>${e(it.codigo)}</td><td>${e(it.descripcion)}</td><td>${e(s)}</td><td class="r">${fmt(it.salidas[s], 'entero')}</td>
        <td class="r">${fmt(p.precio, 'moneda')}</td><td>${p.origen === 'OC' ? `OC · ${e(p.oc_item)}` : 'Manual'}</td><td class="r">${fmt(imp, 'moneda')}</td></tr>`);
    }));
    const conc = (res.conceptos || []).map((c) => `<tr class="sub"><td colspan="3">${e(c.titulo)}</td><td class="r">${fmt(c.base, 'entero')} ${e(c.unidad || '')}</td>
      <td class="r">${c.precio == null ? '<span class="cert-muted">sin precio</span>' : fmt(c.precio, 'moneda')}</td><td></td><td class="r">${c.importe == null ? '—' : fmt(c.importe, 'moneda')}</td></tr>`).join('');
    if (!filas.length && !conc) return '';
    const t = res.totales;
    return tarjeta('Importes (precios de la OC o manuales)', `<div class="cert-wrap"><table class="cert-t"><thead><tr><th>Ítem</th><th>Descripción</th><th>Salida</th><th>Cantidad</th><th>Precio</th><th>Origen</th><th>Importe</th></tr></thead>
      <tbody>${filas.join('') || '<tr><td colspan="7" class="cert-muted">Ningún ítem tiene precio (vincule ítems de la OC o cargue precios manuales en el contrato). El acta se emite en cantidades.</td></tr>'}${conc}</tbody>
      ${t.importe_items != null ? `<tfoot><tr class="tot"><td colspan="6">Neto (ítems ${fmt(t.importe_items, 'moneda')} + conceptos ${fmt(t.importe_conceptos || 0, 'moneda')})</td><td class="r">${fmt(t.importe_neto, 'moneda')}</td></tr></tfoot>` : ''}</table></div>`);
  }

  function reporteHtml(rep) {
    if (!rep) return '<p class="cert-muted">No hay reportes activos en la configuración.</p>';
    const cols = rep.columnas;
    const conGrupos = cols.some((c) => c.grupo);
    let thead = '';
    if (conGrupos) {
      let fila1 = '';
      for (let i = 0; i < cols.length;) {
        const g = cols[i].grupo;
        if (!g) { fila1 += `<th rowspan="2">${e(cols[i].titulo)}</th>`; i += 1; continue; }
        let j = i;
        while (j < cols.length && cols[j].grupo === g) j += 1;
        fila1 += `<th colspan="${j - i}">${e(g)}</th>`;
        i = j;
      }
      thead = `<tr class="g">${fila1}</tr><tr>${cols.filter((c) => c.grupo).map((c) => `<th>${e(c.titulo)}</th>`).join('')}</tr>`;
    } else thead = `<tr>${cols.map((c) => `<th>${e(c.titulo)}</th>`).join('')}</tr>`;
    const celdas = (vals, cls = '') => vals.map((v, i) => `<td class="${cols[i].formato === 'texto' || cols[i].formato === 'fecha' ? '' : 'r'} ${cls}">${e(fmt(v, cols[i].formato))}</td>`).join('');
    const filasSec = (s) => s.filas.map((f) => `<tr style="cursor:pointer" data-am-ref="${ref(f.partes)}">${celdas(f.celdas)}</tr>`).join('')
      + (s.totales ? `<tr class="tot" data-am-ref="${ref(s.partes)}" style="cursor:pointer">${s.totales.map((v, i) => `<td class="r">${i === 0 && v == null ? 'TOTAL' : e(fmt(v, cols[i].formato))}</td>`).join('')}</tr>` : '');
    const enc = rep.encabezado ? `<div style="display:flex;gap:16px;align-items:center;margin-bottom:10px">
        ${rep.encabezado.logo ? `<img src="${e(rep.encabezado.logo)}" alt="" style="height:46px">` : ''}
        <div><div style="font-weight:800;font-size:16px;color:var(--c-navy)">${e(rep.encabezado.titulo)}</div>
        <div class="cert-actions" style="gap:4px 16px">${rep.encabezado.campos.map((c) => `<span><b>${e(c.etiqueta)}:</b> ${e(c.valor)}</span>`).join('')}</div></div></div>` : '';
    let tablas;
    if (rep.una_tabla) {
      tablas = `<div class="cert-wrap"><table class="cert-t am-rep"><thead>${thead}</thead><tbody>${rep.secciones.map((s) => `${s.titulo ? `<tr class="sub"><td colspan="${cols.length}">${e(s.titulo)}</td></tr>` : ''}${filasSec(s)}`).join('')}
        ${rep.total_general ? `<tr class="tot">${rep.total_general.map((v, i) => `<td class="r">${i === 0 && v == null ? 'TOTAL GENERAL' : e(fmt(v, cols[i].formato))}</td>`).join('')}</tr>` : ''}</tbody></table></div>`;
    } else {
      tablas = rep.secciones.map((s) => `<div class="cert-seccion">${e(s.titulo)}</div><div class="cert-wrap"><table class="cert-t am-rep"><thead>${thead}</thead><tbody>${filasSec(s)}</tbody></table></div>`).join('')
        || '<p class="cert-muted">Sin partes en el período.</p>';
    }
    const conceptos = rep.conceptos && rep.conceptos.length ? `<table class="cert-t" style="width:auto;margin-top:10px">${rep.conceptos.map((c) => `<tr><td><b>${e(c.titulo)}</b></td>
      <td class="r">${fmt(c.base, 'entero')} ${e(c.unidad || '')}</td><td>Precio</td><td class="r">${c.precio == null ? '—' : fmt(c.precio, 'moneda')}</td><td>Total $</td>
      <td class="r">${c.importe == null ? '—' : fmt(Math.abs(c.importe), 'moneda')}</td></tr>`).join('')}</table>` : '';
    const firmas = rep.firmas && rep.firmas.length ? `<div style="display:flex;justify-content:space-around;gap:20px;margin-top:40px;text-align:center">${rep.firmas.map((f) => `<div style="min-width:200px;border-top:1px solid #64748b;padding-top:6px">
      <b>${e(f.titulo)}</b><br>${e(f.cargo)}<br>${e(f.empresa)}</div>`).join('')}</div>` : '';
    return `<div class="am-print">${enc}${rep.subtitulo ? `<div class="cert-seccion">${e(rep.subtitulo)}</div>` : ''}${tablas}${conceptos}${firmas}</div>`;
  }

  function imprimir() {
    const rep = AM.reportes.find((r) => r.codigo === AM.repSel);
    const w = window.open('', '_blank');
    if (!w) { toast('El navegador bloqueó la ventana de impresión.', 'error'); return; }
    w.document.write(`<!doctype html><html><head><meta charset="utf-8"><title>${e(rep.titulo)}</title><style>
      body{font-family:Arial,sans-serif;font-size:11px;color:#111;margin:18px} table{border-collapse:collapse;width:100%;margin-bottom:10px}
      th,td{border:1px solid #888;padding:3px 5px} th{background:#dce8f7} td.r{text-align:right} tr.sub td{background:#f1f5fb;font-weight:bold}
      tr.tot td{background:#eef4fc;font-weight:bold} .cert-seccion{font-weight:bold;margin:12px 0 4px} .cert-actions span{margin-right:14px}
      @page{size:landscape;margin:12mm} .am-salto{page-break-before:always}</style></head><body>${reporteHtml(rep).replace(/data-am-ref="\d+"/g, '')}</body></html>`);
    w.document.close();
    w.focus();
    setTimeout(() => w.print(), 300);
  }

  async function verPartes(ids) {
    if (!ids || !ids.length) { toast('No hay partes asociados a esta fila (ítem fijo o sin partes).'); return; }
    const partes = await api(`${API}/actas/${AM.acta.id}/partes`, 'POST', { ids });
    const meds = Object.keys((partes[0] || {}).medidas || {}).filter((k) => partes.some((p) => p.medidas[k] !== null && p.medidas[k] !== 0));
    modal(`Partes diarios (${partes.length})`, `<div class="cert-wrap"><table class="cert-t ajustar"><thead><tr><th>Fecha</th><th>N° parte</th><th>Equipo</th><th>Turno</th><th>Ítem / unidad</th>
      <th>Maquinista</th><th>Descripción</th><th>Medida</th><th>Cantidad</th>${meds.map((m) => `<th>${e(m)}</th>`).join('')}<th>Factores</th></tr></thead><tbody>
      ${partes.map((p) => `<tr><td>${fFecha(p.fecha)}</td><td>${e(p.datos.nro_parte)}</td><td>${e(p.recurso)}</td><td>${e(p.turno_norm)}</td><td>${e(p.item_codigo)} · u${p.unidad}</td>
        <td>${e(p.datos.maquinista)}</td><td style="min-width:240px">${e(p.datos.descripcion_trabajos)}</td><td>${e(p.medida)}</td><td class="r">${fmt(p.cantidad, 'decimal2')}</td>
        ${meds.map((m) => `<td class="r">${fmt(p.medidas[m], 'decimal2')}</td>`).join('')}<td>${Object.entries(p.factores || {}).map(([k, v]) => `${e(k)}=${e(v)}`).join(', ')}</td></tr>`).join('')}
      </tbody><tfoot><tr class="tot"><td colspan="8">Total</td><td class="r">${fmt(partes.reduce((s, p) => s + (p.cantidad || 0), 0), 'decimal2')}</td>
      ${meds.map((m) => `<td class="r">${fmt(partes.reduce((s, p) => s + (p.medidas[m] || 0), 0), 'decimal2')}</td>`).join('')}<td></td></tr></tfoot></table></div>`, '', '');
  }

  function editarActa() {
    const a = AM.acta;
    AM.ed.acta = { periodo_desde: a.periodo_desde, periodo_hasta: a.periodo_hasta, fecha: a.fecha, observaciones: a.observaciones || '', overrides: clon(a.overrides) };
    const bg = modal(`Acta N° ${a.numero}: período y ajustes`, `<div class="cert-grid" style="grid-template-columns:repeat(3,1fr)">
      ${campo('Desde', fIn('acta:periodo_desde', a.periodo_desde, 'txt', 'type="date"'))}${campo('Hasta', fIn('acta:periodo_hasta', a.periodo_hasta, 'txt', 'type="date"'))}
      ${campo('Fecha', fIn('acta:fecha', a.fecha, 'txt', 'type="date"'))}</div>
      ${campo('Observaciones', `<textarea data-b="acta:observaciones" data-t="txt" rows="2">${e(a.observaciones || '')}</textarea>`)}
      ${campo('Ajustes solo para esta acta (pisan contrato y plantilla)', fJson('acta:overrides', a.overrides, 8),
    'Mismo formato que la configuración. Ej.: {"items": {"I.1": {"regla": {"tipo": "MINIMO_GARANTIZADO"}}}} o {"emitir_importes": true}')}`,
    '<button class="cert-btn" type="button" data-am-cerrar>Cancelar</button><button class="cert-btn primary" type="button" id="am-ea-ok">Guardar y recalcular</button>', 'md');
    enlazar(bg.querySelector('.cert-modal'));
    $('am-ea-ok').addEventListener('click', () => ejecutar(async () => {
      await api(`${API}/actas/${a.id}`, 'PUT', AM.ed.acta);
      cerrarModal();
      await vistaActa(a.id);
    }, 'Acta actualizada.'));
  }

  // ================================================================= CONTRATOS
  async function vistaContratos(id) {
    AM.contratos = await api(`${API}/contratos`);
    const body = $('am-body');
    if (id === undefined) {
      body.innerHTML = tarjeta('Contratos de medición', `<div class="cert-wrap"><table class="cert-t"><thead><tr><th>Nombre</th><th>N° contrato</th><th>Plantilla</th><th>OC</th><th>Actas</th><th>Estado</th><th></th></tr></thead><tbody>
        ${AM.contratos.map((c) => `<tr><td>${e(c.nombre)}</td><td>${e(c.numero_contrato || '')}</td><td>${e(c.plantilla)}</td><td>${e(c.oc_texto || '—')}</td><td class="c">${c.actas}</td>
          <td>${c.activo ? 'Activo' : 'Inactivo'}</td><td class="c"><button type="button" class="cert-btn sm" data-am-c="${c.id}">Configurar</button></td></tr>`).join('') || '<tr><td colspan="7" class="cert-muted">Sin contratos.</td></tr>'}
        </tbody></table></div>`, puede('configurar') ? '<button type="button" class="cert-btn primary" id="am-c-nuevo">+ Nuevo contrato</button>' : '');
      body.querySelectorAll('[data-am-c]').forEach((b) => b.addEventListener('click', () => vistaContratos(Number(b.dataset.amC))));
      if ($('am-c-nuevo')) $('am-c-nuevo').addEventListener('click', () => vistaContratos(null));
      return;
    }
    AM.contrato = id ? await api(`${API}/contratos/${id}`) : null;
    const c = AM.contrato || { nombre: '', plantilla_id: (AM.opciones.plantillas[0] || {}).id, config: {}, activo: 1 };
    AM.ed.c = {
      nombre: c.nombre, numero_contrato: c.numero_contrato || '', proyecto: c.proyecto || '', contratista: c.contratista || '', cuit: c.cuit || '',
      comitente: c.comitente || '', plantilla_id: c.plantilla_id, seguimiento_cc_id: c.seguimiento_cc_id || '', orden_compra_cliente_id: c.orden_compra_cliente_id || '',
      fecha_inicio: c.fecha_inicio || '', duracion_meses: c.duracion_meses ?? '', saldo_hasta_numero: c.saldo_hasta_numero ?? 0, activo: !!c.activo, config: clon(c.config) || {},
    };
    AM.renderContrato = () => renderContrato();
    renderContrato();
  }

  function renderContrato() {
    const c = AM.contrato;
    const d = AM.ed.c;
    const o = AM.opciones;
    const ocs = o.ordenes.filter((x) => !d.seguimiento_cc_id || x.seguimiento_cc_id === Number(d.seguimiento_cc_id));
    const cfgC = d.config;
    const efectiva = c ? c.config_efectiva : {};
    const per = (cfgC.periodo || {});
    const body = $('am-body');
    const datos = `<div class="cert-grid" style="grid-template-columns:repeat(4,minmax(0,1fr))">
      ${campo('Nombre', fIn('c:nombre', d.nombre))}${campo('N° de contrato', fIn('c:numero_contrato', d.numero_contrato))}
      ${campo('Proyecto', fIn('c:proyecto', d.proyecto))}${campo('Plantilla', fSel('c:plantilla_id', d.plantilla_id, o.plantillas.map((p) => [p.id, p.nombre]), 'num'))}
      ${campo('Contratista', fIn('c:contratista', d.contratista))}${campo('CUIT', fIn('c:cuit', d.cuit))}${campo('Comitente', fIn('c:comitente', d.comitente))}
      ${campo('Seguimiento de cliente', fSel('c:seguimiento_cc_id', d.seguimiento_cc_id, [['', '—']].concat(o.seguimientos.map((s) => [s.id, `#${s.id} ${s.cliente || s.cliente_codigo || ''} · ${s.centro_costo || ''}`])), 'num', 'data-rerender="renderContrato"'))}
      ${campo('Orden de compra (precios)', fSel('c:orden_compra_cliente_id', d.orden_compra_cliente_id, [['', '— sin OC —']].concat(ocs.map((x) => [x.id, `${x.orden_compra || x.numero} ${x.revision || ''} · ${x.moneda || ''}`])), 'num'))}
      ${campo('Inicio', fIn('c:fecha_inicio', d.fecha_inicio, 'txt', 'type="date"'))}${campo('Duración (meses)', fNum('c:duracion_meses', d.duracion_meses))}
      ${campo('Saldo inicial hasta acta N°', fNum('c:saldo_hasta_numero', d.saldo_hasta_numero), 'Actas emitidas fuera del sistema; la próxima acta toma el número siguiente y el acumulado anterior parte del saldo inicial de cada ítem.')}
      ${campo('Activo', fChk('c:activo', d.activo))}</div>
      <div class="cert-seccion">Ajustes del contrato (pisan la plantilla; vacío = hereda)</div>
      <div class="cert-grid" style="grid-template-columns:repeat(4,minmax(0,1fr))">
        ${campo('Día de corte', fNum('c:config.periodo.dia_corte', per.dia_corte, `placeholder="${e((efectiva.periodo || {}).dia_corte ?? '')}"`))}
        ${campo('Duración del período', fSel('c:config.periodo.duracion', per.duracion || '', [['', '(hereda)'], ['MENSUAL', 'Mensual'], ['QUINCENAL', 'Quincenal'], ['LIBRE', 'Libre (días)']]))}
        ${campo('Emitir con importes', fSel('c:config.emitir_importes', cfgC.emitir_importes === undefined ? '' : String(cfgC.emitir_importes), [['', '(hereda)'], ['true', 'Sí'], ['false', 'No (solo cantidades)']], 'bool'))}
        ${campo('% pueden superar 100%', fSel('c:config.acumulados.permite_superar_100', ((cfgC.acumulados || {}).permite_superar_100 === undefined ? '' : String(cfgC.acumulados.permite_superar_100)), [['', '(hereda)'], ['true', 'Sí'], ['false', 'No']], 'bool'))}
      </div>
      <div class="cert-seccion">Filtros adicionales de partes (además de los equipos asignados)</div>
      ${tablaLista('c', 'config.fuente.filtros', ((cfgC.fuente || {}).filtros) || [], [{ k: 'campo', t: 'Campo del parte', tipo: 'sel', op: AM.campos.map((x) => [x.campo, x.etiqueta]) },
    { k: 'op', t: 'Condición', tipo: 'sel', op: OPS_FILTRO }, { k: 'valor', t: 'Valor', w: '220px' }], 'filtros', 'renderContrato')}
      <details style="margin-top:10px"><summary class="cert-muted">Ajustes avanzados (JSON que pisa cualquier clave de la plantilla)</summary>${fJson('c:config', cfgC, 10)}</details>`;
    body.innerHTML = `${tarjeta(c ? `Contrato: ${e(c.nombre)}` : 'Nuevo contrato', datos, `<button type="button" class="cert-btn" id="am-c-volver">← Contratos</button>
      ${puede('configurar') ? '<button type="button" class="cert-btn primary" id="am-c-guardar">Guardar contrato</button>' : ''}`, 'am-c-datos')}
      ${c ? estructuraHtml(c) : '<p class="cert-muted">Al crear el contrato se copian las secciones e ítems de la estructura de la plantilla; después se asignan los equipos (CI) a cada ítem.</p>'}`;
    enlazar($('am-c-datos'));
    $('am-c-volver').addEventListener('click', () => vistaContratos());
    if ($('am-c-guardar')) $('am-c-guardar').addEventListener('click', guardarContrato);
    if (c) eventosEstructura(c);
  }

  // bool: '' => elimina, 'true'/'false' => booleano (se normaliza al guardar)
  function limpiarBool(cfg) {
    const recorrer = (o) => { Object.keys(o || {}).forEach((k) => { if (o[k] === 'true') o[k] = true; else if (o[k] === 'false') o[k] = false; else if (o[k] === '' || o[k] === null) delete o[k]; else if (typeof o[k] === 'object' && !Array.isArray(o[k])) { recorrer(o[k]); if (!Object.keys(o[k]).length) delete o[k]; } }); };
    recorrer(cfg);
    return cfg;
  }

  async function guardarContrato() {
    const d = clon(AM.ed.c);
    d.config = limpiarBool(d.config || {});
    await ejecutar(async () => {
      const r = AM.contrato ? await api(`${API}/contratos/${AM.contrato.id}`, 'PUT', d) : await api(`${API}/contratos`, 'POST', d);
      AM.opciones = await api(`${API}/opciones`);
      await vistaContratos(r.id);
    }, 'Contrato guardado.');
  }

  function estructuraHtml(c) {
    const cfg = c.config_efectiva;
    const medidas = Object.entries(cfg.medidas || {});
    const secc = Object.fromEntries(c.secciones.map((s) => [s.id, s]));
    const items = c.items;
    const regTxt = (it) => {
      const r = Object.assign({}, (cfg.item_defecto || {}).regla, it.config.regla);
      const modo = it.config.modo || (cfg.item_defecto || {}).modo || 'PARTE';
      if (modo === 'FIJO') return `Fijo ${(it.config.fijo || {}).valor ?? ''} ${(it.config.fijo || {}).por === 'ITEM' ? 'por ítem' : 'por unidad'}`;
      return `${r.tipo || 'SIN_MINIMO'}${r.minimo ? ` ${r.minimo}` : ''} · ${(r.ambito || 'ITEM').toLowerCase()}`;
    };
    const filasItems = items.map((it) => `<tr><td>${e((secc[it.seccion_id] || {}).codigo || '')}</td><td>${e(it.codigo)}</td><td>${e(it.descripcion)}</td><td class="c">${e(it.unidad || '')}</td>
      <td class="r">${fmt(it.unidades, 'entero')}</td><td class="r">${it.cantidad_contratada != null ? fmt(it.cantidad_contratada, 'entero') : `<span class="cert-muted">${fmt((it.cantidad_mensual || 0) * it.unidades * (c.duracion_meses || 0), 'entero')}</span>`}</td>
      <td>${e(it.config.medida || (cfg.item_defecto || {}).medida || '')}</td><td>${e(regTxt(it))}</td>
      <td>${it.oc.map((l) => `${e(l.salida)} → ${e(l.numero)}`).join('<br>') || '—'}</td><td>${it.asignaciones.map((a) => e(a.recurso_codigo)).join(', ') || '—'}</td>
      <td class="c">${it.activo ? '' : 'inactivo '}${puede('configurar') ? `<button type="button" class="cert-btn sm" data-am-item="${it.id}">Editar</button>` : ''}</td></tr>`).join('');
    const asign = items.flatMap((it) => it.asignaciones.map((a) => ({ ...a, item: it })));
    const filasAsig = asign.map((a) => {
      const eq = AM.opciones.equipos.find((x) => x.codigo.toUpperCase() === a.recurso_codigo.toUpperCase());
      return `<tr><td>${e(a.item.codigo)}</td><td>${e(a.recurso_tipo)}</td><td><b>${e(a.recurso_codigo)}</b> <span class="cert-muted">${e(eq ? `${eq.tipo || ''} ${eq.marca || ''}` : '')}</span></td>
        <td class="c">${a.unidad}</td><td>${e(a.turno || 'todos')}</td><td>${fFecha(a.desde)}</td><td>${a.hasta ? fFecha(a.hasta) : 'vigente'}</td>
        <td>${e(Object.entries(a.config || {}).map(([k, v]) => `${k}: ${typeof v === 'object' ? JSON.stringify(v) : v}`).join('; '))}</td>
        <td class="c" style="white-space:nowrap">${puede('configurar') ? `<button type="button" class="cert-btn sm" data-am-asig="${a.id}">Editar</button>
        <button type="button" class="cert-btn sm" data-am-mover="${a.id}" title="Mover a otro ítem desde una fecha">Mover</button>
        <button type="button" class="cert-btn sm danger" data-am-asig-del="${a.id}">×</button>` : ''}</td></tr>`;
    }).join('');
    const titFactor = Object.fromEntries((cfg.factores || []).map((f) => [f.codigo, `${f.titulo} (${f.unidad || ''})`]));
    const filasFact = c.factores.map((f) => `<tr><td><b>${e(f.recurso_codigo)}</b></td><td>${e(titFactor[f.codigo] || f.codigo)}</td><td class="r">${fmt(f.valor, 'decimal2')}</td>
      <td>${fFecha(f.desde)}</td><td>${f.hasta ? fFecha(f.hasta) : 'vigente'}</td><td class="c">${puede('configurar') ? `<button type="button" class="cert-btn sm" data-am-fac="${f.id}">Editar</button>
      <button type="button" class="cert-btn sm danger" data-am-fac-del="${f.id}">×</button>` : ''}</td></tr>`).join('');
    const itemsTxt = Object.fromEntries(items.map((it) => [it.id, it.codigo]));
    const filasPre = c.precios.map((p) => `<tr><td>${p.item_id ? `${e(itemsTxt[p.item_id])} · ${e(p.salida)}` : `Concepto ${e(p.concepto)}`}</td><td class="r">${fmt(p.precio, 'moneda')}</td><td>${e(p.moneda || '')}</td>
      <td>${fFecha(p.desde)}</td><td>${p.hasta ? fFecha(p.hasta) : 'vigente'}</td><td class="c">${puede('configurar') ? `<button type="button" class="cert-btn sm danger" data-am-pre-del="${p.id}">×</button>` : ''}</td></tr>`).join('');
    const btn = (id, t) => (puede('configurar') ? `<button type="button" class="cert-btn sm" id="${id}">${t}</button>` : '');
    return `
      ${tarjeta('Secciones', `<div class="cert-wrap"><table class="cert-t"><thead><tr><th>Orden</th><th>Código</th><th>Título</th><th></th></tr></thead><tbody>
        ${c.secciones.map((s) => `<tr><td class="c">${s.orden}</td><td>${e(s.codigo)}</td><td>${e(s.titulo)}</td><td class="c">${puede('configurar') ? `<button type="button" class="cert-btn sm" data-am-sec="${s.id}">Editar</button>
        <button type="button" class="cert-btn sm danger" data-am-sec-del="${s.id}">×</button>` : ''}</td></tr>`).join('') || '<tr><td colspan="4" class="cert-muted">Sin secciones.</td></tr>'}</tbody></table></div>`, btn('am-sec-nueva', '+ Sección'))}
      ${tarjeta('Ítems del contrato', `<div class="cert-wrap"><table class="cert-t ajustar"><thead><tr><th>Sec.</th><th>Ítem</th><th>Descripción</th><th>Unidad</th><th>Unidades</th><th>Contratado</th>
        <th>Medida</th><th>Regla</th><th>Ítems de OC</th><th>Equipos</th><th></th></tr></thead><tbody>${filasItems || '<tr><td colspan="11" class="cert-muted">Sin ítems.</td></tr>'}</tbody></table></div>
        <p class="cert-muted">Medidas disponibles: ${medidas.map(([k, m]) => `<b>${e(k)}</b> (${e(m.titulo || '')})`).join(', ')}. Se definen en la plantilla.</p>`, btn('am-item-nuevo', '+ Ítem'))}
      ${tarjeta('Equipos y operadores asignados (con vigencia y turno)', `<div class="cert-wrap"><table class="cert-t"><thead><tr><th>Ítem</th><th>Tipo</th><th>CI / legajo</th><th>Unidad</th><th>Turno</th><th>Desde</th><th>Hasta</th><th>Ajustes</th><th></th></tr></thead>
        <tbody>${filasAsig || '<tr><td colspan="9" class="cert-muted">Sin asignaciones: los ítems medidos por parte necesitan al menos un equipo.</td></tr>'}</tbody></table></div>`, btn('am-asig-nueva', '+ Asignar equipo'))}
      ${tarjeta('Factores por equipo (índice de carga, capacidad de riego…)', `<div class="cert-wrap"><table class="cert-t"><thead><tr><th>Equipo</th><th>Factor</th><th>Valor</th><th>Desde</th><th>Hasta</th><th></th></tr></thead>
        <tbody>${filasFact || '<tr><td colspan="6" class="cert-muted">Sin factores. Ej.: volquete C-34 · m³ por viaje = 20; regador C-16 · litros por viaje = 10.000.</td></tr>'}</tbody></table></div>
        <p class="cert-muted">Los factores se usan en las fórmulas de las medidas como <code>f.codigo</code> (ej.: <code>viajes * f.carga_m3</code>).</p>`, btn('am-fac-nuevo', '+ Factor'))}
      ${tarjeta('Precios manuales (cuando el ítem no se vincula a la OC)', `<div class="cert-wrap"><table class="cert-t"><thead><tr><th>Ítem / concepto</th><th>Precio</th><th>Moneda</th><th>Desde</th><th>Hasta</th><th></th></tr></thead>
        <tbody>${filasPre || '<tr><td colspan="6" class="cert-muted">Sin precios manuales.</td></tr>'}</tbody></table></div>`, btn('am-pre-nuevo', '+ Precio'))}`;
  }

  function eventosEstructura(c) {
    const body = $('am-body');
    const on = (sel, fn) => body.querySelectorAll(sel).forEach((b) => b.addEventListener('click', () => fn(b)));
    const borrar = (tipo, id, msg) => { if (!confirm(msg)) return; ejecutar(async () => { await api(`${API}/contratos/${c.id}/${tipo}/${id}`, 'DELETE'); await vistaContratos(c.id); }, 'Eliminado.'); };
    on('[data-am-sec]', (b) => editarSeccion(c, c.secciones.find((s) => s.id === Number(b.dataset.amSec))));
    on('[data-am-sec-del]', (b) => borrar('secciones', b.dataset.amSecDel, '¿Eliminar la sección? Sus ítems quedan sin sección.'));
    on('[data-am-item]', (b) => editarItem(c, c.items.find((i) => i.id === Number(b.dataset.amItem))));
    const asigs = c.items.flatMap((it) => it.asignaciones);
    on('[data-am-asig]', (b) => editarAsignacion(c, asigs.find((a) => a.id === Number(b.dataset.amAsig))));
    on('[data-am-asig-del]', (b) => borrar('asignaciones', b.dataset.amAsigDel, '¿Quitar el equipo del ítem? (para cambiarlo de ítem use «Mover»)'));
    on('[data-am-mover]', (b) => moverAsignacion(c, asigs.find((a) => a.id === Number(b.dataset.amMover))));
    on('[data-am-fac]', (b) => editarFactor(c, c.factores.find((f) => f.id === Number(b.dataset.amFac))));
    on('[data-am-fac-del]', (b) => borrar('factores', b.dataset.amFacDel, '¿Eliminar el factor?'));
    on('[data-am-pre-del]', (b) => borrar('precios', b.dataset.amPreDel, '¿Eliminar el precio?'));
    const clic = (id, fn) => { if ($(id)) $(id).addEventListener('click', fn); };
    clic('am-sec-nueva', () => editarSeccion(c, null));
    clic('am-item-nuevo', () => editarItem(c, null));
    clic('am-asig-nueva', () => editarAsignacion(c, null));
    clic('am-fac-nuevo', () => editarFactor(c, null));
    clic('am-pre-nuevo', () => editarPrecio(c));
  }

  function formModal(titulo, raiz, html, guardar, ancho = 'sm') {
    const bg = modal(titulo, html, '<button class="cert-btn" type="button" data-am-cerrar>Cancelar</button><button class="cert-btn primary" type="button" id="am-fm-ok">Guardar</button>', ancho);
    enlazar(bg.querySelector('.cert-modal'));
    $('am-fm-ok').addEventListener('click', () => ejecutar(async () => { await guardar(AM.ed[raiz]); cerrarModal(); }, 'Guardado.'));
  }

  function editarSeccion(c, s) {
    AM.ed.sec = s ? { codigo: s.codigo, titulo: s.titulo, orden: s.orden } : { codigo: '', titulo: '', orden: c.secciones.length };
    formModal(s ? 'Editar sección' : 'Nueva sección', 'sec', `<div class="cert-grid">${campo('Código', fIn('sec:codigo', AM.ed.sec.codigo))}${campo('Título', fIn('sec:titulo', AM.ed.sec.titulo))}
      ${campo('Orden', fNum('sec:orden', AM.ed.sec.orden))}</div>`, async (d) => {
      await api(`${API}/contratos/${c.id}/secciones${s ? `/${s.id}` : ''}`, s ? 'PUT' : 'POST', d);
      await vistaContratos(c.id);
    });
  }

  function editarItem(c, it) {
    const cfg = c.config_efectiva;
    const def = cfg.item_defecto || {};
    const base = it || { codigo: '', descripcion: '', unidad: 'hs', unidades: 1, cantidad_mensual: null, cantidad_contratada: null, saldo_inicial: 0, meta: null, meta_medida: '', orden: (c.items.length + 1) * 10, activo: 1, config: {}, oc: [] };
    AM.ed.it = {
      seccion_id: base.seccion_id || '', codigo: base.codigo, descripcion: base.descripcion, unidad: base.unidad || '', unidades: base.unidades, cantidad_mensual: base.cantidad_mensual,
      cantidad_contratada: base.cantidad_contratada, saldo_inicial: base.saldo_inicial, meta: base.meta, meta_medida: base.meta_medida || '', orden: base.orden, activo: !!base.activo,
      config: Object.assign({ regla: {}, fijo: {} }, clon(base.config)), oc: Object.fromEntries((base.oc || []).map((l) => [l.salida, l.item_oc_cliente_id])),
    };
    const itemsOc = AM.opciones.items_oc.filter((x) => (!c.seguimiento_cc_id || x.seguimiento_cc_id === c.seguimiento_cc_id)
      && (!c.orden_compra_cliente_id || x.orden_compra_cliente_id === c.orden_compra_cliente_id));
    const medidas = [['', `(plantilla: ${def.medida || '—'})`]].concat(Object.entries(cfg.medidas || {}).map(([k, m]) => [k, `${k} · ${m.titulo || ''}`]));
    AM.renderItemModal = () => {
      const d = AM.ed.it;
      const r = d.config.regla || {};
      const tipo = r.tipo || (def.regla || {}).tipo || 'SIN_MINIMO';
      $('am-modal-b').innerHTML = `
        <div class="cert-grid" style="grid-template-columns:repeat(4,minmax(0,1fr))">
          ${campo('Sección', fSel('it:seccion_id', d.seccion_id, [['', '—']].concat(c.secciones.map((s) => [s.id, `${s.codigo} · ${s.titulo}`])), 'num'))}
          ${campo('Código', fIn('it:codigo', d.codigo))}${campo('Descripción', fIn('it:descripcion', d.descripcion))}${campo('Unidad', fIn('it:unidad', d.unidad))}
          ${campo('Cantidad de unidades', fNum('it:unidades', d.unidades))}${campo('Cantidad mensual por unidad', fNum('it:cantidad_mensual', d.cantidad_mensual))}
          ${campo('Cantidad contratada', fNum('it:cantidad_contratada', d.cantidad_contratada, `placeholder="mensual × unidades × ${c.duracion_meses || 0} meses"`), 'Vacío = cantidad mensual × unidades × duración del contrato')}
          ${campo('Saldo inicial (acumulado previo)', fNum('it:saldo_inicial', d.saldo_inicial))}
          ${campo('Meta de avance', fNum('it:meta', d.meta))}${campo('Medida de la meta', fSel('it:meta_medida', d.meta_medida, [['', '—']].concat(Object.keys(cfg.medidas || {}).map((k) => [k, k]))))}
          ${campo('Orden', fNum('it:orden', d.orden))}${campo('Activo', fChk('it:activo', d.activo))}</div>
        <div class="cert-seccion">Qué se mide y cómo</div>
        <div class="cert-grid" style="grid-template-columns:repeat(4,minmax(0,1fr))">
          ${campo('Medida', fSel('it:config.medida', d.config.medida || '', medidas))}
          ${campo('Modo', fSel('it:config.modo', d.config.modo || '', [['', `(plantilla: ${def.modo || 'PARTE'})`]].concat(MODOS), 'txt', 'data-rerender="renderItemModal"'))}
          ${campo('Fijo: valor', fNum('it:config.fijo.valor', (d.config.fijo || {}).valor), 'Para modo fijo o mixto (ej.: 300 hs por persona)')}
          ${campo('Fijo: por', fSel('it:config.fijo.por', (d.config.fijo || {}).por || 'UNIDAD', [['UNIDAD', 'Por unidad'], ['ITEM', 'Por ítem']]))}</div>
        <div class="cert-seccion">Regla de cálculo</div>
        <div class="cert-grid" style="grid-template-columns:repeat(4,minmax(0,1fr))">
          ${campo('Regla', fSel('it:config.regla.tipo', tipo, REGLAS, 'txt', 'data-rerender="renderItemModal"'))}
          ${campo('Mínimo', fNum('it:config.regla.minimo', r.minimo))}
          ${campo('Se aplica', fSel('it:config.regla.ambito', r.ambito || (def.regla || {}).ambito || 'ITEM', AMBITOS))}
          ${campo('Prorrateo del mínimo', fSel('it:config.regla.prorrateo', r.prorrateo || '', [['', 'No'], ['DIAS', 'Por días del período']]))}
          ${campo('Certifica extras', fSel('it:config.regla.certifica_extras', r.certifica_extras === false ? 'false' : 'true', [['true', 'Sí'], ['false', 'No (solo mínimas)']], 'bool'))}</div>
        ${tipo === 'TRAMOS' ? `<div class="cert-seccion">Tramos (hasta: cantidad acumulada; vacío = sin límite)</div>${tablaLista('it', 'config.regla.tramos', r.tramos || [], [{ k: 'hasta', t: 'Hasta', tipo: 'num' },
    { k: 'salida', t: 'Va a', tipo: 'sel', op: [['minimas', 'Mínimas'], ['extras', 'Extras']] }, { k: 'factor', t: 'Factor', tipo: 'num' }], 'tramos', 'renderItemModal')}` : ''}
        ${tipo === 'FORMULA' ? `<div class="cert-grid" style="grid-template-columns:repeat(3,1fr)">${['minimas', 'extras', 'certificar'].map((s) => campo(`Fórmula ${s}`, fIn(`it:config.regla.formulas.${s}`, ((r.formulas || {})[s]) || '', 'txt', 'style="font-family:Consolas,monospace"'))).join('')}</div>
          <p class="cert-muted">Variables: R (registrado), M (mínimo), unidades, dias_periodo; funciones: min, max, si(cond, a, b), redondear…</p>` : ''}
        <div class="cert-seccion">Facturación y vínculo con la OC (${itemsOc.length ? `${itemsOc.length} ítems de OC disponibles` : 'el contrato no tiene seguimiento/OC con ítems'})</div>
        <div class="cert-grid" style="grid-template-columns:repeat(4,minmax(0,1fr))">
          ${campo('Salidas que se facturan', fIn('it:config.facturar', d.config.facturar || def.facturar || ['certificar'], 'lista'), 'Lista separada por coma: registrado, minimas, extras, certificar')}
          ${SALIDAS.map(([s, t]) => campo(`${t} → ítem de OC`, fSel(`it:oc.${s}`, d.oc[s] || '', [['', '—']].concat(itemsOc.map((x) => [x.id, `${x.orden_compra} · ${x.numero} · ${x.descripcion} · ${nf(x.precio, 2)}`])), 'num'))).join('')}</div>
        <details style="margin-top:10px"><summary class="cert-muted">Configuración del ítem (JSON)</summary>${fJson('it:config', d.config, 8)}</details>`;
    };
    const bg = modal(it ? `Ítem ${e(it.codigo)}` : 'Nuevo ítem', '', `${it && puede('configurar') ? '<button class="cert-btn danger" type="button" id="am-it-del" style="margin-right:auto">Eliminar ítem</button>' : ''}
      <button class="cert-btn" type="button" data-am-cerrar>Cancelar</button><button class="cert-btn primary" type="button" id="am-it-ok">Guardar</button>`, 'md');
    enlazar(bg.querySelector('.cert-modal'));
    AM.renderItemModal();
    $('am-it-ok').addEventListener('click', () => ejecutar(async () => {
      const d = clon(AM.ed.it);
      d.config = limpiarBool(d.config);
      await api(`${API}/contratos/${c.id}/items${it ? `/${it.id}` : ''}`, it ? 'PUT' : 'POST', d);
      cerrarModal();
      await vistaContratos(c.id);
    }, 'Ítem guardado.'));
    if ($('am-it-del')) $('am-it-del').addEventListener('click', () => {
      if (!confirm('¿Eliminar el ítem y sus asignaciones?')) return;
      ejecutar(async () => { await api(`${API}/contratos/${c.id}/items/${it.id}`, 'DELETE'); cerrarModal(); await vistaContratos(c.id); }, 'Ítem eliminado.');
    });
  }

  function datalists() {
    return `<datalist id="am-dl-eq">${AM.opciones.equipos.map((x) => `<option value="${e(x.codigo)}">${e(`${x.tipo || ''} ${x.marca || ''} ${x.modelo || ''}`)}</option>`).join('')}</datalist>
      <datalist id="am-dl-op">${AM.opciones.operadores.map((x) => `<option value="${e(x.legajo)}">${e(x.nombre)}</option>`).join('')}</datalist>`;
  }

  function editarAsignacion(c, a) {
    const cfg = c.config_efectiva;
    AM.ed.as = a ? { item_id: a.item_id, recurso_tipo: a.recurso_tipo, recurso_codigo: a.recurso_codigo, unidad: a.unidad, turno: a.turno || '', desde: a.desde, hasta: a.hasta || '', config: clon(a.config) || {} }
      : { item_id: (c.items[0] || {}).id, recurso_tipo: 'EQUIPO', recurso_codigo: '', unidad: 1, turno: '', desde: c.fecha_inicio || new Date().toISOString().slice(0, 10), hasta: '', config: {} };
    const turnos = [['', 'Todos los turnos']].concat(Object.keys(cfg.turnos || {}).map((t) => [t, t]));
    const d = AM.ed.as;
    formModal(a ? 'Editar asignación' : 'Asignar equipo u operador a un ítem', 'as', `${datalists()}<div class="cert-grid" style="grid-template-columns:repeat(2,1fr)">
      ${campo('Ítem', fSel('as:item_id', d.item_id, c.items.map((i) => [i.id, `${i.codigo} · ${i.descripcion}`]), 'num'))}
      ${campo('Tipo', fSel('as:recurso_tipo', d.recurso_tipo, [['EQUIPO', 'Equipo (CI)'], ['OPERADOR', 'Operador (legajo)']]))}
      ${campo('CI del equipo / legajo', fIn('as:recurso_codigo', d.recurso_codigo, 'txt', `list="${d.recurso_tipo === 'OPERADOR' ? 'am-dl-op' : 'am-dl-eq'}" autocomplete="off"`))}
      ${campo('N° de unidad dentro del ítem', fNum('as:unidad', d.unidad), 'Ej.: ítem de 2 camionetas: unidad 1 y 2. Un reemplazo va en la misma unidad.')}
      ${campo('Turno', fSel('as:turno', d.turno, turnos))}${campo('Medida para este equipo', fSel('as:config.medida', (d.config || {}).medida || '', [['', '(la del ítem)']].concat(Object.keys(cfg.medidas || {}).map((k) => [k, k]))))}
      ${campo('Desde', fIn('as:desde', d.desde, 'txt', 'type="date"'))}${campo('Hasta', fIn('as:hasta', d.hasta, 'txt', 'type="date"'))}</div>
      <details style="margin-top:10px"><summary class="cert-muted">Regla propia de este equipo (JSON, opcional)</summary>${fJson('as:config', d.config, 5)}
      <p class="cert-muted">Ej.: {"regla": {"tipo": "MINIMO_GARANTIZADO", "minimo": 200}}</p></details>`, async (x) => {
      const datos = clon(x);
      datos.config = limpiarBool(datos.config || {});
      await api(`${API}/contratos/${c.id}/asignaciones${a ? `/${a.id}` : ''}`, a ? 'PUT' : 'POST', datos);
      await vistaContratos(c.id);
    });
  }

  function moverAsignacion(c, a) {
    AM.ed.mv = { item_id: (c.items.find((i) => i.id !== a.item_id) || {}).id, fecha: new Date().toISOString().slice(0, 10) };
    formModal(`Mover ${e(a.recurso_codigo)} a otro ítem`, 'mv', `<div class="cert-grid">${campo('Ítem destino', fSel('mv:item_id', AM.ed.mv.item_id, c.items.map((i) => [i.id, `${i.codigo} · ${i.descripcion}`]), 'num'))}
      ${campo('Desde la fecha', fIn('mv:fecha', AM.ed.mv.fecha, 'txt', 'type="date"'))}</div>
      <p class="cert-muted">La asignación actual se cierra el día anterior y se crea una nueva en el ítem destino. Las actas congeladas no cambian.</p>`, async (x) => {
      await api(`${API}/contratos/${c.id}/asignaciones/${a.id}/mover`, 'POST', x);
      await vistaContratos(c.id);
    });
  }

  function editarFactor(c, f) {
    const cfg = c.config_efectiva;
    AM.ed.fa = f ? { recurso_codigo: f.recurso_codigo, codigo: f.codigo, valor: f.valor, desde: f.desde, hasta: f.hasta || '' }
      : { recurso_codigo: '', codigo: ((cfg.factores || [])[0] || {}).codigo || '', valor: null, desde: c.fecha_inicio || new Date().toISOString().slice(0, 10), hasta: '' };
    const d = AM.ed.fa;
    formModal(f ? 'Editar factor' : 'Nuevo factor de equipo', 'fa', `${datalists()}<div class="cert-grid" style="grid-template-columns:repeat(2,1fr)">
      ${campo('Equipo (CI)', fIn('fa:recurso_codigo', d.recurso_codigo, 'txt', 'list="am-dl-eq" autocomplete="off"'))}
      ${campo('Factor', fSel('fa:codigo', d.codigo, (cfg.factores || []).map((x) => [x.codigo, `${x.titulo} (${x.unidad || ''})`])))}
      ${campo('Valor', fNum('fa:valor', d.valor))}<div></div>${campo('Desde', fIn('fa:desde', d.desde, 'txt', 'type="date"'))}${campo('Hasta', fIn('fa:hasta', d.hasta, 'txt', 'type="date"'))}</div>
      <p class="cert-muted">Los tipos de factor se definen en la plantilla (Factores). Un cambio de valor desde una fecha se carga como un factor nuevo con su vigencia.</p>`, async (x) => {
      await api(`${API}/contratos/${c.id}/factores${f ? `/${f.id}` : ''}`, f ? 'PUT' : 'POST', x);
      await vistaContratos(c.id);
    });
  }

  function editarPrecio(c) {
    const cfg = c.config_efectiva;
    AM.ed.pr = { item_id: (c.items[0] || {}).id, salida: 'certificar', concepto: '', precio: null, moneda: '', desde: c.fecha_inicio || new Date().toISOString().slice(0, 10), hasta: '' };
    const d = AM.ed.pr;
    formModal('Nuevo precio manual', 'pr', `<div class="cert-grid" style="grid-template-columns:repeat(2,1fr)">
      ${campo('Ítem (vacío si es concepto)', fSel('pr:item_id', d.item_id, [['', '—']].concat(c.items.map((i) => [i.id, `${i.codigo} · ${i.descripcion}`])), 'num'))}
      ${campo('Salida', fSel('pr:salida', d.salida, SALIDAS))}
      ${campo('o Concepto', fSel('pr:concepto', '', [['', '—']].concat((cfg.conceptos || []).map((x) => [x.codigo, x.titulo]))))}
      ${campo('Precio', fNum('pr:precio', d.precio))}${campo('Moneda', fIn('pr:moneda', d.moneda))}<div></div>
      ${campo('Desde', fIn('pr:desde', d.desde, 'txt', 'type="date"'))}${campo('Hasta', fIn('pr:hasta', d.hasta, 'txt', 'type="date"'))}</div>`, async (x) => {
      const datos = clon(x);
      if (datos.concepto) datos.item_id = null;
      await api(`${API}/contratos/${c.id}/precios`, 'POST', datos);
      await vistaContratos(c.id);
    });
  }

  // ================================================================= PLANTILLAS
  async function vistaPlantillas(id) {
    const body = $('am-body');
    if (id === undefined) {
      AM.plantillas = await api(`${API}/plantillas`);
      body.innerHTML = tarjeta('Plantillas de acta', `<div class="cert-wrap"><table class="cert-t"><thead><tr><th>Nombre</th><th>Descripción</th><th>Contratos</th><th>Estado</th><th></th></tr></thead><tbody>
        ${AM.plantillas.map((p) => `<tr><td><b>${e(p.nombre)}</b></td><td style="white-space:normal">${e(p.descripcion || '')}</td><td class="c">${p.contratos}</td><td>${p.activa ? 'Activa' : 'Inactiva'}</td>
          <td class="c" style="white-space:nowrap"><button type="button" class="cert-btn sm" data-am-p="${p.id}">Editar</button>
          ${puede('configurar') ? `<button type="button" class="cert-btn sm" data-am-p-copiar="${p.id}">Copiar</button>` : ''}</td></tr>`).join('')}</tbody></table></div>
        <p class="cert-muted">Una plantilla define período, medidas, factores, reglas por defecto, conceptos, indicadores, validaciones, flujo, encabezado, firmas, reportes y la estructura inicial de ítems.
        Los contratos pueden pisar cualquier parte; el acta también.</p>`);
      body.querySelectorAll('[data-am-p]').forEach((b) => b.addEventListener('click', () => vistaPlantillas(Number(b.dataset.amP))));
      body.querySelectorAll('[data-am-p-copiar]').forEach((b) => b.addEventListener('click', () => {
        const nombre = prompt('Nombre de la nueva plantilla:');
        if (!nombre) return;
        ejecutar(async () => { const p = await api(`${API}/plantillas/${b.dataset.amPCopiar}/copiar`, 'POST', { nombre }); AM.opciones = null; AM.opciones = await api(`${API}/opciones`); await vistaPlantillas(p.id); }, 'Plantilla copiada.');
      }));
      return;
    }
    const [p, catalogo] = await Promise.all([api(`${API}/plantillas/${id}`), api(`${API}/catalogo?plantilla_id=${id}`)]);
    AM.catalogo = catalogo;
    const cfg = clon(p.config);
    AM.ed.p = {
      id: p.id, nombre: p.nombre, descripcion: p.descripcion || '', activa: !!p.activa, cfg,
      medidas: Object.entries(cfg.medidas || {}).map(([codigo, m]) => ({ codigo, ...m })),
      turnos: Object.entries(cfg.turnos || {}).map(([codigo, alias]) => ({ codigo, alias: (alias || []).join(', ') })),
      repSel: 0,
    };
    AM.renderEditor = renderPlantilla;
    renderPlantilla();
  }

  function renderPlantilla() {
    const d = AM.ed.p;
    const cfg = d.cfg;
    const camposNum = AM.campos.filter((x) => x.numerico).map((x) => [x.campo, x.etiqueta]);
    const camposTodos = AM.campos.map((x) => [x.campo, x.etiqueta]);
    const medidasCod = d.medidas.map((m) => [m.codigo, m.codigo]);
    const rep = (cfg.reportes || [])[d.repSel];
    const catalogoNivel = rep ? (AM.catalogo[rep.nivel] || []).map((x) => [x.valor, `${x.titulo} [${x.valor}]`]) : [];
    const seccion = (titulo, html, abierto = false) => `<details class="cert-card" style="margin-bottom:10px" ${abierto ? 'open' : ''}><summary class="cert-card-h" style="cursor:pointer"><h3>${titulo}</h3></summary><div class="cert-card-b">${html}</div></details>`;
    const per = cfg.periodo || {};
    const def = cfg.item_defecto || {};
    const fuente = cfg.fuente || {};
    $('am-body').innerHTML = `<div id="am-p-ed">
      ${tarjeta(`Plantilla: ${e(d.nombre)}`, `<div class="cert-grid" style="grid-template-columns:2fr 3fr 1fr">${campo('Nombre', fIn('p:nombre', d.nombre))}${campo('Descripción', fIn('p:descripcion', d.descripcion))}${campo('Activa', fChk('p:activa', d.activa))}</div>`,
    `<button type="button" class="cert-btn" id="am-p-volver">← Plantillas</button>${puede('configurar') ? '<button type="button" class="cert-btn primary" id="am-p-guardar">Guardar plantilla</button>' : ''}`)}
      ${seccion('1. Período y numeración', `<div class="cert-grid" style="grid-template-columns:repeat(4,1fr)">${campo('Día de corte', fNum('p:cfg.periodo.dia_corte', per.dia_corte), 'El período termina ese día (ej.: 20 → del 21 al 20)')}
        ${campo('Duración', fSel('p:cfg.periodo.duracion', per.duracion || 'MENSUAL', [['MENSUAL', 'Mensual'], ['QUINCENAL', 'Quincenal'], ['LIBRE', 'Libre']]))}
        ${campo('Días (libre)', fNum('p:cfg.periodo.dias', per.dias))}${campo('Duración del contrato por defecto (meses)', fNum('p:cfg.contrato_defecto.duracion_meses', (cfg.contrato_defecto || {}).duracion_meses))}</div>`, true)}
      ${seccion('2. Fuente: campos del parte diario', `<div class="cert-grid" style="grid-template-columns:repeat(5,1fr)">
        ${[['campo_fecha', 'Fecha'], ['campo_equipo', 'Equipo (CI)'], ['campo_operador', 'Operador'], ['campo_turno', 'Turno'], ['campo_nro_parte', 'N° de parte']].map(([k, t]) => campo(t, fSel(`p:cfg.fuente.${k}`, fuente[k], camposTodos))).join('')}</div>
        <div class="cert-seccion">Filtros de partes por defecto</div>${tablaLista('p', 'cfg.fuente.filtros', fuente.filtros || [], [{ k: 'campo', t: 'Campo', tipo: 'sel', op: camposTodos }, { k: 'op', t: 'Condición', tipo: 'sel', op: OPS_FILTRO }, { k: 'valor', t: 'Valor' }], 'filtros')}
        <div class="cert-seccion">Turnos (equivalencias de lo que se escribe en el parte)</div>${tablaLista('p', 'turnos', d.turnos, [{ k: 'codigo', t: 'Turno' }, { k: 'alias', t: 'Se escribe como (separado por coma; vacío = sin turno)', w: '420px' }], 'turnos')}`)}
      ${seccion('3. Medidas (qué se puede medir de cada parte)', tablaLista('p', 'medidas', d.medidas, [{ k: 'codigo', t: 'Código', w: '110px' }, { k: 'titulo', t: 'Título' }, { k: 'unidad', t: 'Unidad', w: '60px' },
    { k: 'tipo', t: 'Cómo se obtiene', tipo: 'sel', op: TIPOS_MEDIDA }, { k: 'inicio', t: 'Inicio', tipo: 'sel', op: [['', '—']].concat(camposNum) }, { k: 'fin', t: 'Final', tipo: 'sel', op: [['', '—']].concat(camposNum) },
    { k: 'campo', t: 'Campo', tipo: 'sel', op: [['', '—']].concat(camposNum) }, { k: 'valor', t: 'Valor fijo', tipo: 'num' }, { k: 'formula', t: 'Fórmula', w: '200px' },
    { k: 'alternativa', t: 'Si no hay dato usar', tipo: 'sel', op: [['', '—']].concat(medidasCod) }], 'medidas')
      + '<p class="cert-muted">Las fórmulas usan cualquier campo del parte por su nombre, medidas anteriores y factores del equipo como f.codigo. Ej.: viajes * f.carga_m3</p>')}
      ${seccion('4. Factores por equipo (se cargan valores en cada contrato)', tablaLista('p', 'cfg.factores', cfg.factores || [], [{ k: 'codigo', t: 'Código' }, { k: 'titulo', t: 'Título', w: '300px' }, { k: 'unidad', t: 'Unidad' }], 'factores'))}
      ${seccion('5. Ítem por defecto y acumulados', `<div class="cert-grid" style="grid-template-columns:repeat(4,1fr)">
        ${campo('Medida', fSel('p:cfg.item_defecto.medida', def.medida, medidasCod))}${campo('Modo', fSel('p:cfg.item_defecto.modo', def.modo || 'PARTE', MODOS))}
        ${campo('Regla', fSel('p:cfg.item_defecto.regla.tipo', (def.regla || {}).tipo || 'SIN_MINIMO', REGLAS))}${campo('Mínimo', fNum('p:cfg.item_defecto.regla.minimo', (def.regla || {}).minimo))}
        ${campo('Ámbito', fSel('p:cfg.item_defecto.regla.ambito', (def.regla || {}).ambito || 'ITEM', AMBITOS))}${campo('Salidas que se facturan', fIn('p:cfg.item_defecto.facturar', def.facturar || ['certificar'], 'lista'))}
        ${campo('% pueden superar 100%', fChk('p:cfg.acumulados.permite_superar_100', (cfg.acumulados || {}).permite_superar_100 !== false))}${campo('Emitir con importes', fChk('p:cfg.emitir_importes', !!cfg.emitir_importes))}</div>`)}
      ${seccion('6. Conceptos adicionales (descuentos / adicionales)', tablaLista('p', 'cfg.conceptos', cfg.conceptos || [], [{ k: 'codigo', t: 'Código', w: '90px' }, { k: 'titulo', t: 'Título' }, { k: 'base', t: 'Base (fórmula sobre medidas)', w: '170px' },
    { k: 'unidad', t: 'Unidad', w: '50px' }, { k: 'signo', t: 'Signo', tipo: 'sel', vt: 'num', op: [['-1', 'Descuento (−)'], ['1', 'Adicional (+)']] }, { k: 'precio', t: 'Precio', tipo: 'num' },
    { k: 'aplica_secciones', t: 'Solo secciones', tipo: 'lista' }, { k: 'aplica_items', t: 'Solo ítems', tipo: 'lista' }, { k: 'activo', t: 'Activo', tipo: 'chk' }], 'conceptos'))}
      ${seccion('7. Indicadores (columnas calculadas)', tablaLista('p', 'cfg.indicadores', cfg.indicadores || [], [{ k: 'codigo', t: 'Código' }, { k: 'titulo', t: 'Título' }, { k: 'formula', t: 'Fórmula', w: '260px' }, { k: 'decimales', t: 'Decimales', tipo: 'num' }], 'indicadores')
      + '<p class="cert-muted">Variables: las medidas sumadas (litros, viajes, m3…), registrado, minimas, extras, certificar, partes, dias, unidades, contratado.</p>')}
      ${seccion('8. Validaciones', tablaLista('p', 'cfg.validaciones', cfg.validaciones || [], [{ k: 'codigo', t: 'Validación', tipo: 'sel', op: VALIDACIONES.map((v) => [v, v]) }, { k: 'activa', t: 'Activa', tipo: 'chk' },
    { k: 'severidad', t: 'Severidad', tipo: 'sel', op: [['AVISO', 'Aviso'], ['BLOQUEO', 'Bloqueo']] }, { k: 'param', t: 'Parámetros (JSON)', tipo: 'json' }], 'validaciones'))}
      ${seccion('9. Flujo de estados', tablaLista('p', 'cfg.flujo.estados', (cfg.flujo || {}).estados || [], [{ k: 'codigo', t: 'Código' }, { k: 'titulo', t: 'Título', w: '260px' },
    { k: 'permiso', t: 'Acción requerida', tipo: 'sel', op: ACCIONES }, { k: 'congela', t: 'Congela', tipo: 'chk' }], 'estados')
      + campo('Estados desde los que se puede precargar un certificado', fIn('p:cfg.flujo.precarga_desde', (cfg.flujo || {}).precarga_desde || [], 'lista')))}
      ${seccion('10. Encabezado y firmas', `<div class="cert-grid" style="grid-template-columns:2fr 1fr">${campo('Título', fIn('p:cfg.encabezado.titulo', (cfg.encabezado || {}).titulo))}${campo('Logo', fIn('p:cfg.encabezado.logo', (cfg.encabezado || {}).logo))}</div>
        <div class="cert-seccion">Datos del encabezado</div>${tablaLista('p', 'cfg.encabezado.campos', (cfg.encabezado || {}).campos || [], [{ k: 'etiqueta', t: 'Etiqueta' }, { k: 'valor', t: 'Valor', w: '300px' }], 'campos')}
        <div class="cert-seccion">Firmas</div>${tablaLista('p', 'cfg.firmas', cfg.firmas || [], [{ k: 'titulo', t: 'Título' }, { k: 'cargo', t: 'Cargo' }, { k: 'empresa', t: 'Empresa' }], 'firmas')}
        <p class="cert-muted">Marcadores: {numero} {revision} {fecha} {periodo} {contrato.nombre} {contrato.numero_contrato} {contrato.proyecto} {contrato.contratista} {contrato.cuit} {contrato.comitente}</p>`)}
      ${seccion('11. Reportes', `${tablaLista('p', 'cfg.reportes', cfg.reportes || [], [{ k: 'codigo', t: 'Código' }, { k: 'titulo', t: 'Título' }, { k: 'nivel', t: 'Nivel', tipo: 'sel', op: NIVELES }, { k: 'activo', t: 'Activo', tipo: 'chk' }], 'reportes')}
        ${(cfg.reportes || []).length ? `<div class="cert-actions" style="margin:12px 0 6px"><b>Editar reporte:</b> <select id="am-p-rep">${opts((cfg.reportes || []).map((r, i) => [i, r.titulo]), d.repSel)}</select></div>` : ''}
        ${rep ? `<div class="cert-grid" style="grid-template-columns:repeat(4,1fr)">${campo('Subtítulo', fIn(`p:cfg.reportes.${d.repSel}.subtitulo`, rep.subtitulo))}
          ${campo('Agrupar por', fSel(`p:cfg.reportes.${d.repSel}.agrupar`, rep.agrupar || '', AGRUPAR))}${campo('Título de cada grupo', fIn(`p:cfg.reportes.${d.repSel}.titulo_seccion`, rep.titulo_seccion))}
          ${campo('Una sola tabla', fChk(`p:cfg.reportes.${d.repSel}.una_tabla`, rep.una_tabla))}${campo('Hoja de Excel por grupo', fChk(`p:cfg.reportes.${d.repSel}.hoja_por_seccion`, rep.hoja_por_seccion))}
          ${campo('Encabezado', fChk(`p:cfg.reportes.${d.repSel}.encabezado`, rep.encabezado))}${campo('Firmas', fChk(`p:cfg.reportes.${d.repSel}.firmas`, rep.firmas))}${campo('Mostrar conceptos', fChk(`p:cfg.reportes.${d.repSel}.mostrar_conceptos`, rep.mostrar_conceptos))}</div>
          <div class="cert-seccion">Columnas (en orden) — el valor puede ser cualquier dato del nivel o una fórmula que empiece con =</div>
          ${tablaLista('p', `cfg.reportes.${d.repSel}.columnas`, rep.columnas || [], [{ k: 'valor', t: 'Valor', tipo: 'sel', op: catalogoNivel.concat((rep.columnas || []).filter((c) => !catalogoNivel.some(([v]) => v === c.valor)).map((c) => [c.valor, c.valor])) },
    { k: 'titulo', t: 'Título' }, { k: 'grupo', t: 'Grupo (encabezado superior)' }, { k: 'formato', t: 'Formato', tipo: 'sel', op: FORMATOS }, { k: 'total', t: 'Total', tipo: 'chk' }], 'columnas')}
          <div class="cert-actions" style="margin-top:6px">Columna con fórmula: <input id="am-p-colf" placeholder="= med_litros / registrado" style="width:260px;font-family:Consolas,monospace">
          <button type="button" class="cert-btn sm" id="am-p-colf-ok">Agregar</button></div>
          <p class="cert-muted">Nivel «Por parte diario»: están todas las columnas del parte (${AM.campos.length}), incluidas las que se agreguen en el futuro.</p>` : ''}`)}
      ${seccion('12. Estructura inicial de ítems (se copia al crear un contrato)', fJson('p:cfg.estructura', cfg.estructura || [], 14))}
      ${seccion('JSON completo (avanzado)', `<p class="cert-muted">Se aplica al guardar. Útil para copiar configuraciones entre plantillas.</p><textarea id="am-p-json" rows="16" spellcheck="false" style="font-family:Consolas,monospace;font-size:12px;width:100%">${e(JSON.stringify(armarConfig(), null, 2))}</textarea>
        <button type="button" class="cert-btn sm" id="am-p-json-ok" style="margin-top:6px">Usar este JSON</button>`)}
    </div>`;
    const ed = $('am-p-ed');
    enlazar(ed);
    $('am-p-volver').addEventListener('click', () => vistaPlantillas());
    if ($('am-p-guardar')) $('am-p-guardar').addEventListener('click', guardarPlantilla);
    if ($('am-p-rep')) $('am-p-rep').addEventListener('change', (ev) => { d.repSel = Number(ev.target.value); renderPlantilla(); });
    if ($('am-p-colf-ok')) $('am-p-colf-ok').addEventListener('click', () => {
      const f = $('am-p-colf').value.trim();
      if (!f) return;
      rep.columnas.push({ valor: f.startsWith('=') ? f : `=${f}`, titulo: 'Calculada', formato: 'decimal2' });
      renderPlantilla();
    });
    $('am-p-json-ok').addEventListener('click', () => {
      try {
        const nuevo = JSON.parse($('am-p-json').value);
        d.cfg = nuevo;
        d.medidas = Object.entries(nuevo.medidas || {}).map(([codigo, m]) => ({ codigo, ...m }));
        d.turnos = Object.entries(nuevo.turnos || {}).map(([codigo, alias]) => ({ codigo, alias: (alias || []).join(', ') }));
        renderPlantilla();
        toast('JSON aplicado (falta guardar).');
      } catch (err) { toast(`JSON inválido: ${err.message}`, 'error'); }
    });
  }

  function armarConfig() {
    const d = AM.ed.p;
    const cfg = clon(d.cfg);
    cfg.medidas = {};
    d.medidas.forEach((m) => {
      const { codigo, ...resto } = m;
      Object.keys(resto).forEach((k) => { if (resto[k] === '' || resto[k] === null) delete resto[k]; });
      if (codigo) cfg.medidas[codigo] = resto;
    });
    cfg.turnos = {};
    d.turnos.forEach((t) => { if (t.codigo) cfg.turnos[t.codigo.toUpperCase()] = String(t.alias || '').split(',').map((x) => x.trim().toUpperCase()); });
    (cfg.conceptos || []).forEach((c) => { c.signo = Number(c.signo) < 0 ? -1 : 1; });
    return cfg;
  }

  async function guardarPlantilla() {
    const d = AM.ed.p;
    await ejecutar(async () => {
      await api(`${API}/plantillas/${d.id}`, 'PUT', { nombre: d.nombre, descripcion: d.descripcion, activa: d.activa, config: armarConfig() });
      AM.opciones = await api(`${API}/opciones`);
      AM.catalogo = await api(`${API}/catalogo?plantilla_id=${d.id}`);
      renderPlantilla();
    }, 'Plantilla guardada. Las actas congeladas no cambian; los borradores se recalculan con la nueva configuración.');
  }

  // ================================================================= CAMPOS DEL PARTE
  async function vistaCampos() {
    AM.campos = await api(`${API}/campos`);
    $('am-body').innerHTML = tarjeta('Campos del parte diario', `<p class="cert-muted">Se leen automáticamente de la tabla de partes: un campo nuevo aparece aquí y queda disponible para medidas, filtros, fórmulas y columnas.
      Puede cambiar la etiqueta con que se muestra.</p><div class="cert-wrap"><table class="cert-t"><thead><tr><th>Campo</th><th>Tipo</th><th>Numérico</th><th>Etiqueta</th><th>Formato</th><th></th></tr></thead><tbody>
      ${AM.campos.map((c, i) => `<tr><td><code>${e(c.campo)}</code></td><td>${e(c.tipo_sql)}</td><td class="c">${c.numerico ? 'sí' : ''}</td>
        <td><input id="am-cp-e${i}" value="${e(c.etiqueta)}"></td><td><select id="am-cp-f${i}">${opts(FORMATOS, c.formato === 'decimal' ? 'decimal2' : c.formato)}</select></td>
        <td class="c">${puede('configurar') ? `<button type="button" class="cert-btn sm" data-am-cp="${i}">Guardar</button>` : ''}</td></tr>`).join('')}</tbody></table></div>`);
    $('am-body').querySelectorAll('[data-am-cp]').forEach((b) => b.addEventListener('click', () => {
      const i = Number(b.dataset.amCp);
      ejecutar(() => api(`${API}/campos/${encodeURIComponent(AM.campos[i].campo)}`, 'PUT', { etiqueta: $(`am-cp-e${i}`).value, formato: $(`am-cp-f${i}`).value }), 'Etiqueta guardada.');
    }));
  }

  // ================================================================= Entrada y precarga del certificado
  window.amAbrir = async () => {
    if (!root()) return;
    try { if (!AM.campos.length) AM.campos = await api(`${API}/campos`); } catch (err) { /* sin permiso o sin conexión: se informa en la pestaña */ }
    abrirTab();
  };

  // Llamada desde el editor de certificados (Seguimiento de Cliente › Certificados › Nuevo).
  window.amPrecargarCertificado = async () => {
    const ed = typeof certState !== 'undefined' ? certState.ed : null;
    if (!ed) return;
    let lista;
    try { lista = await api(`/operaciones/seguimiento-cliente/${certState.sid}/actas-medicion?oc=${ed.alcance === 'OC' ? (ed.oc || 0) : 0}`); } catch (err) { toast(err.message, 'error'); return; }
    if (!lista.length) { toast('No hay actas de medición aprobadas para este seguimiento (vincule el contrato de medición al seguimiento y apruebe el acta).'); return; }
    modal('Precargar desde acta de medición', `<p class="cert-muted">Se cargan las cantidades del período en los ítems de la OC vinculados a cada ítem del acta y los conceptos con precio como descuentos. Todo queda editable.</p>
      <div class="cert-wrap"><table class="cert-t"><thead><tr><th>Contrato</th><th>Acta</th><th>Período</th><th>Estado</th><th>Certificado vigente</th><th></th></tr></thead><tbody>
      ${lista.map((a) => `<tr><td>${e(a.contrato)}</td><td class="c">N° ${a.numero}${a.revision ? ` rev. ${a.revision}` : ''}</td><td>${fFecha(a.periodo_desde)} al ${fFecha(a.periodo_hasta)}</td><td>${badge(a.estado)}</td>
        <td>${a.certificado_vigente ? `${a.certificado_vigente.numero ? `N° ${a.certificado_vigente.numero}` : 'borrador'} (${e(a.certificado_vigente.estado)})` : '—'}${a.coincide_oc ? '' : ' <span class="cert-muted">(otra OC)</span>'}</td>
        <td class="c"><button type="button" class="cert-btn sm primary" data-am-pc="${a.acta_id}" ${a.certificado_vigente && a.certificado_vigente.id !== ed.id ? 'disabled title="Ya tiene un certificado vigente"' : ''}>Precargar</button></td></tr>`).join('')}
      </tbody></table></div>`, '<button class="cert-btn" type="button" data-am-cerrar>Cerrar</button>', 'md');
    $('am-modal-b').querySelectorAll('[data-am-pc]').forEach((b) => b.addEventListener('click', async () => {
      try {
        const p = await api(`/operaciones/seguimiento-cliente/${certState.sid}/actas-medicion/${b.dataset.amPc}/precarga`);
        if (ed.modalidad !== 'ITEMS' && !certState.resumen.modalidad_fija) ed.modalidad = 'ITEMS';
        if (ed.modalidad !== 'ITEMS') { toast('El seguimiento certifica en modalidad global: la precarga por ítems no aplica.', 'error'); return; }
        if (p.ordenes.length === 1) {
          const o = certState.resumen.ordenes.find((x) => x.orden_compra_cliente_id === p.ordenes[0]);
          if (o) { ed.alcance = 'OC'; ed.oc = o.orden_compra_cliente_id; ed.moneda = o.moneda; }
        } else if (p.ordenes.length > 1) ed.alcance = 'SEG';
        ed.carga = {};
        p.lineas.forEach((l) => { ed.carga[`i${l.item_oc_cliente_id}`] = { valor: String(l.cantidad_periodo), obs: l.observacion.slice(0, 500) }; });
        ed.desde = p.periodo_desde;
        ed.hasta = p.periodo_hasta;
        ed.descuentos = (ed.descuentos || []).filter((x) => !x.amPrecarga).concat(p.descuentos.map((x) => ({ ...x, porcentaje: '', amPrecarga: true })));
        ed.observaciones = ed.observaciones && !ed.observaciones.includes(p.observaciones) ? `${ed.observaciones}\n${p.observaciones}` : (ed.observaciones || p.observaciones);
        ed.amActaId = p.acta_id;
        cerrarModal();
        certRenderEditor();
        certCalcular();
        const avisos = [];
        if (!p.lineas.length) avisos.push('ningún ítem del acta está vinculado a ítems de la OC');
        if (p.items_sin_vinculo.length) avisos.push(`ítems sin vínculo con la OC: ${p.items_sin_vinculo.join(', ')}`);
        if (p.conceptos_sin_precio.length) avisos.push(`conceptos sin precio (no se descuentan): ${p.conceptos_sin_precio.join(', ')}`);
        toast(`Precargado desde el acta N° ${p.numero}.${avisos.length ? ` Atención: ${avisos.join('; ')}.` : ''}`, avisos.length ? 'warning' : 'success');
      } catch (err) { toast(err.message, 'error'); }
    }));
  };

  window.amVincularCertificado = async (actaId, certificadoId) => {
    try { await api(`/operaciones/seguimiento-cliente/${certState.sid}/actas-medicion/${actaId}/vincular`, 'POST', { certificado_id: certificadoId }); } catch (err) { toast(`El certificado se guardó pero no se pudo vincular al acta: ${err.message}`, 'error'); }
  };

  AM.renderEditor = () => {};
  AM.renderContrato = () => {};
  AM.renderItemModal = () => {};
})();
