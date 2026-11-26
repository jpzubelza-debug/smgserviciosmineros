/* Operaciones › Actas de Medición.
 * Un acta = OC del cliente + período + equipos (CI) + columnas del parte diario. La cantidad de cada equipo es la suma de
 * una columna de sus partes del período y va al ítem de la OC elegido; desde un acta cerrada se precarga el certificado.
 * Todo cálculo lo hace el servidor (actas_medicion.py). Usa los componentes del design system (/ui/app.css) y showToast / opPuede.
 */
(() => {
  'use strict';
  const API = '/api/actas-medicion';
  const AM = { opciones: null, filtroSeg: '', ed: null, res: null, sucio: false, ocupado: false, timer: null, acomodar: false, timerG: null };

  // ---------------------------------------------------------------- utilidades
  const $ = (id) => document.getElementById(id);
  const e = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const puede = (accion) => (typeof opPuede === 'function' ? opPuede('actas_medicion', accion) : true);
  const toast = (msg, tipo = 'info') => (typeof showToast === 'function' ? showToast(e(msg), tipo) : alert(msg));
  const fFecha = (iso) => (iso && /^\d{4}-\d{2}-\d{2}/.test(iso) ? `${iso.slice(8, 10)}/${iso.slice(5, 7)}/${iso.slice(0, 4)}` : (iso ?? ''));
  const nf = (v, d = 2) => (v === null || v === undefined || v === '' || Number.isNaN(Number(v)) ? '' : Number(v).toLocaleString('es-AR', { minimumFractionDigits: 0, maximumFractionDigits: d }));
  const money = (v) => (v === null || v === undefined ? '—' : Number(v).toLocaleString('es-AR', { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
  const opts = (lista, sel) => lista.map(([v, t]) => `<option value="${e(v)}" ${String(v) === String(sel ?? '') ? 'selected' : ''}>${e(t)}</option>`).join('');
  const ic = (n, c = '') => `<svg class="i ${c}" aria-hidden="true"><use href="#i-${n}"/></svg>`;
  const campo = (etq, html, ancho = '') => `<div class="field ${Number(ancho) >= 4 ? 'span-all' : ''}"><label>${e(etq)}</label>${html}</div>`;
  const tarjeta = (titulo, cuerpo, acciones = '', id = '') => `<section class="card" ${id ? `id="${id}"` : ''}>
    <div class="card-head"><h2 class="card-title">${titulo}</h2><span class="head-actions">${acciones}</span></div><div class="card-body">${cuerpo}</div></section>`;
  // Estados en sentence case: Borrador (advertencia), Cerrada (acento), Certificada (éxito), Anulada.
  const ESTADO_ACTA = { BORRADOR: ['chip-warn', 'Borrador'], CERRADA: ['chip-accent', 'Cerrada'], ANULADA: ['chip-bad', 'Anulada'], CERTIFICADA: ['chip-ok', 'Certificada'] };
  const badge = (estado) => { const x = ESTADO_ACTA[estado] || ['chip-muted', estado]; return `<span class="chip ${x[0]}">${e(x[1])}</span>`; };
  const certTxt = (c) => (c ? `${c.numero ? `N° ${c.numero}` : 'borrador'} (${e(c.estado)})` : '—');

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

  // ---------------------------------------------------------------- modal propio
  function modal(titulo, cuerpo, pie = '', ancho = 'md') {
    let bg = $('am-modal');
    if (!bg) {
      bg = document.createElement('div');
      bg.id = 'am-modal';
      bg.className = 'modal is-toggle encima';
      document.body.appendChild(bg);
      bg.addEventListener('click', (ev) => { if (ev.target === bg || ev.target.closest('[data-am-cerrar]')) cerrarModal(); });
    }
    bg.innerHTML = `<div class="modal-panel ${ancho}" role="dialog" aria-modal="true"><div class="modal-head"><h3>${titulo}</h3><span class="spacer"></span><button class="btn btn-ghost btn-icon" type="button" data-am-cerrar aria-label="Cerrar">${ic('x')}</button></div>
      <div class="modal-body ctl-auto" id="am-modal-b">${cuerpo}</div>${pie ? `<div class="modal-foot">${pie}</div>` : ''}</div>`;
    bg.classList.add('open');
    return bg;
  }
  function cerrarModal() { const bg = $('am-modal'); if (bg) bg.classList.remove('open'); }

  // ---------------------------------------------------------------- datos auxiliares
  const root = () => $('am-root');
  const campos = () => (AM.opciones ? AM.opciones.campos : []);
  const etiqueta = (c) => (campos().find((x) => x.campo === c) || { etiqueta: c }).etiqueta;
  const numericos = () => campos().filter((x) => x.numerico);
  const ordenesDe = (sid) => AM.opciones.ordenes.filter((o) => !sid || o.seguimiento_cc_id === Number(sid));
  const itemsDe = (oc) => AM.opciones.items_oc.filter((i) => i.orden_compra_cliente_id === Number(oc));
  const segTxt = (s) => `#${s.id} ${s.cliente || s.cliente_codigo || ''}${s.centro_costo ? ` · ${s.centro_costo}` : ''}`;
  const ocTxt = (o) => `${o.orden_compra || `OC ${o.numero}`}${o.revision ? ` rev. ${o.revision}` : ''}${o.moneda ? ` · ${o.moneda}` : ''}`;
  const descEquipo = (ci) => ((AM.opciones.equipos.find((x) => x.codigo === String(ci).toUpperCase()) || {}).descripcion || '');
  // Centro de costos de los partes (Obra/Proyecto «209 - …»). null = todos los partes del equipo.
  const centros = () => AM.opciones.centros_costos || [];
  const ccTxt = (id) => { const c = centros().find((x) => x.id === Number(id)); return c ? `${c.id} - ${c.nombre}` : (id ? String(id) : ''); };
  const ccDelSeguimiento = () => { const s = AM.opciones.seguimientos.find((x) => x.id === Number(AM.ed.seguimiento_cc_id)); return (s && s.centro_costo_id) || null; };
  const opCentros = () => [['', 'Todos']].concat(centros().map((c) => [c.id, `${c.id} - ${c.nombre}`]));

  // Encabezado de la página (.page-head) + cuerpo; título, subtítulo y acciones los completa cada vista.
  function encabezado(titulo = 'Actas de medición', sub = 'Partes diarios de los equipos de un período, vinculados a los ítems de una orden de compra para certificar.') {
    root().classList.add('ctl-auto');
    root().innerHTML = `<div class="page-head"><div><div id="am-volver-wrap"></div><h1 id="am-titulo">${titulo}</h1><p class="page-sub" id="am-sub">${sub}</p></div>
        <div class="head-actions" id="am-acciones"></div></div>
      <div class="stack" id="am-body"></div>`;
  }

  // ================================================================= LISTA
  const AM_LISTA = { estado: '', q: '', filas: [] };
  async function vistaLista() {
    AM.ed = null;
    AM.res = null;
    encabezado();
    const body = $('am-body');
    body.innerHTML = '<section class="card"><div class="empty-state">Cargando…</div></section>';
    const actas = await api(`${API}/actas${AM.filtroSeg ? `?seguimiento=${AM.filtroSeg}` : ''}`);
    AM_LISTA.filas = actas;
    if (puede('generar')) $('am-acciones').innerHTML = `<button type="button" class="btn btn-primary" id="am-nueva">${ic('plus')}Nueva acta</button>`;
    body.innerHTML = `<section class="card" aria-label="Listado de actas">
      <div class="toolbar">
        <div class="search">${ic('search')}<input type="search" class="input" id="am-f-q" placeholder="N.º, cliente u OC" aria-label="Buscar actas" value="${e(AM_LISTA.q)}" autocomplete="off"></div>
        <select class="chip-select ${AM.filtroSeg ? 'on' : ''}" id="am-f-seg" aria-label="Seguimiento de cliente">${opts([['', 'Todos los seguimientos']].concat(AM.opciones.seguimientos.map((s) => [s.id, segTxt(s)])), AM.filtroSeg)}</select>
        <select class="chip-select ${AM_LISTA.estado ? 'on' : ''}" id="am-f-estado" aria-label="Estado">${opts([['', 'Todos los estados'], ['BORRADOR', 'Borrador'], ['CERRADA', 'Cerrada'], ['CERTIFICADA', 'Certificada'], ['ANULADA', 'Anulada']], AM_LISTA.estado)}</select>
        <span class="spacer"></span><span class="muted num" id="am-cuenta"></span>
      </div>
      <div class="table-wrap is-auto"><table class="tbl" id="am-tabla"><colgroup></colgroup><thead><tr><th>N.º</th><th>Cliente</th><th>OC</th><th>Período</th><th class="num">Equipos</th><th>Estado</th><th>Certificado</th><th class="center"><span class="sr-only">Acciones</span></th></tr></thead><tbody id="am-lista"></tbody></table></div>
    </section>`;
    SMG.columnasTabla($('am-tabla'), $('am-tabla').querySelector('colgroup'), [{ w: 72 }, { w: 220, flex: true }, { w: 160 }, { w: 200 }, { w: 90 }, { w: 120 }, { w: 170 }, { w: 52 }]);
    pintarLista();
    $('am-f-seg').addEventListener('change', (ev) => { AM.filtroSeg = ev.target.value; vistaLista().catch((err) => toast(err.message, 'error')); });
    $('am-f-estado').addEventListener('change', (ev) => { AM_LISTA.estado = ev.target.value; ev.target.classList.toggle('on', !!ev.target.value); pintarLista(); });
    $('am-f-q').addEventListener('input', (ev) => { AM_LISTA.q = ev.target.value; pintarLista(); });
    $('am-lista').addEventListener('click', (ev) => {
      const tr = ev.target.closest('tr[data-am-fila]');
      if (!tr) return;
      const id = Number(tr.dataset.amFila);
      const menu = ev.target.closest('[data-menu-fila]');
      if (menu && typeof opMenuFila === 'function') {
        opMenuFila(menu, [
          { t: 'Abrir', i: 'eye', fn: () => abrirActa(id).catch((err) => toast(err.message, 'error')) },
          { t: 'Descargar Excel', i: 'download', fn: () => { location.href = `${API}/actas/${id}/excel`; } },
          { t: 'Imprimir / PDF', i: 'printer', fn: () => abrirActa(id).then(imprimir).catch((err) => toast(err.message, 'error')) },
        ]);
        return;
      }
      abrirActa(id).catch((err) => toast(err.message, 'error'));
    });
    if ($('am-nueva')) $('am-nueva').addEventListener('click', nuevaActa);
  }

  const estadoLista = (a) => (a.estado === 'CERRADA' && a.certificado && a.certificado.estado !== 'ANULADO' ? 'CERTIFICADA' : a.estado);
  function pintarLista() {
    const q = SMG.fold(AM_LISTA.q.trim());
    const filas = AM_LISTA.filas.filter((a) => (!AM_LISTA.estado || estadoLista(a) === AM_LISTA.estado)
      && (!q || SMG.fold(`${a.numero} ${a.cliente || ''} ${a.orden_compra || ''} ${a.oc_numero || ''}`).includes(q)));
    $('am-cuenta').textContent = `${filas.length} de ${AM_LISTA.filas.length} actas`;
    $('am-lista').innerHTML = filas.map((a) => `<tr data-am-fila="${a.id}" tabindex="0">
        <td class="mono">${a.numero}</td><td title="${e(a.cliente || '')}">${e(a.cliente || '')}</td><td class="mono">${e(a.orden_compra || `OC ${a.oc_numero}`)}</td>
        <td>${fFecha(a.periodo_desde)} al ${fFecha(a.periodo_hasta)}</td><td class="num">${a.equipos}</td><td>${badge(estadoLista(a))}</td><td>${certTxt(a.certificado)}</td>
        <td class="center"><button type="button" class="btn btn-ghost btn-icon btn-sm" data-menu-fila data-am-abrir="${a.id}" aria-haspopup="menu" aria-label="Acciones del acta ${a.numero}">${ic('more')}</button></td></tr>`).join('')
      || `<tr><td colspan="8" class="empty-cell">${AM_LISTA.filas.length ? 'Ninguna acta coincide con los filtros.' : 'Todavía no hay actas. Use «Nueva acta».'}</td></tr>`;
  }

  // ================================================================= ACTA
  function nuevaActa() {
    const sid = AM.filtroSeg || '';
    AM.ed = { id: null, estado: 'BORRADOR', seguimiento_cc_id: sid, orden_compra_cliente_id: '', numero: '', periodo_desde: '', periodo_hasta: '',
      fecha: new Date().toISOString().slice(0, 10), campo_cantidad: 'total_hs', columnas: [], observaciones: '', equipos: [], certificados: [],
      metas: [], graficos: null };
    AM.res = null;
    AM.acomodar = false;
    AM.sucio = false;
    renderActa();
  }

  async function abrirActa(id) {
    const a = await api(`${API}/actas/${id}`);
    AM.ed = {
      id: a.id, estado: a.estado, seguimiento_cc_id: a.seguimiento_cc_id, orden_compra_cliente_id: a.orden_compra_cliente_id, numero: a.numero,
      periodo_desde: a.periodo_desde, periodo_hasta: a.periodo_hasta, fecha: a.fecha, campo_cantidad: a.campo_cantidad, columnas: a.columnas,
      observaciones: a.observaciones || '', equipos: a.equipos.map((x) => ({ ...x })), certificados: a.certificados, cerrada_por: a.cerrada_por,
      cerrada_en: a.cerrada_en, anulada_por: a.anulada_por, motivo_anulacion: a.motivo_anulacion,
      metas: (a.metas || []).map((x) => ({ ...x })), graficos: a.graficos || null,
    };
    if (a.graficos_defecto) GRAFICOS_DEFECTO = a.graficos_defecto;
    AM.res = a.resultado;
    AM.sucio = false;
    renderActa();
  }

  const editable = () => AM.ed.estado === 'BORRADOR' && puede('generar');

  function renderActa() {
    encabezado();
    const d = AM.ed;
    const ro = editable() ? '' : 'disabled';
    const ocs = ordenesDe(d.seguimiento_cc_id);
    const nuevo = !d.id;
    const vigente = (d.certificados || []).find((c) => c.estado !== 'ANULADO');
    const visibles = [
      d.id && d.estado === 'BORRADOR' && puede('aprobar') ? '<button type="button" class="btn" id="am-cerrar" title="Guarda el resultado y habilita la precarga del certificado">Cerrar acta</button>' : '',
      d.id && d.estado === 'CERRADA' && puede('aprobar') && !vigente ? '<button type="button" class="btn" id="am-reabrir">Reabrir</button>' : '',
      editable() ? `<button type="button" class="btn btn-primary" id="am-guardar">${nuevo ? 'Crear acta' : 'Guardar'}</button>` : '',
    ].filter(Boolean);
    const mas = [
      d.id ? `<button type="button" class="menu-item" role="menuitem" id="am-imprimir" data-cerrar-menu>${ic('printer')}Imprimir / PDF</button>` : '',
      d.id ? `<a class="menu-item" role="menuitem" href="${API}/actas/${d.id}/excel" data-cerrar-menu>${ic('download')}Descargar Excel</a>` : '',
      d.id && d.estado !== 'ANULADA' && puede('anular') && !vigente ? `<button type="button" class="menu-item danger" role="menuitem" id="am-anular" data-cerrar-menu>${ic('ban')}Anular</button>` : '',
      d.id && d.estado === 'BORRADOR' && puede('generar') ? `<button type="button" class="menu-item danger" role="menuitem" id="am-eliminar" data-cerrar-menu>${ic('trash')}Eliminar</button>` : '',
    ].filter(Boolean);
    const acciones = visibles.join('') + (mas.length ? `<button type="button" class="btn btn-icon" id="am-mas" aria-haspopup="menu" aria-label="Más acciones">${ic('more')}</button>
      <div class="menu" id="am-mas-menu" role="menu" hidden>${mas.join('')}</div>` : '');
    let estadoTxt = '';
    if (d.estado === 'CERRADA') estadoTxt = `Cerrada por ${e(d.cerrada_por)} el ${e(d.cerrada_en)}: el resultado quedó guardado y no cambia aunque cambien los partes.`;
    else if (d.estado === 'ANULADA') estadoTxt = `Anulada por ${e(d.anulada_por)}: ${e(d.motivo_anulacion)}`;
    else estadoTxt = 'Borrador: se calcula en vivo desde los partes diarios.';

    const datos = `<div class="form-grid">
        ${campo('Seguimiento de cliente', `<select id="am-seg" ${ro || (d.id ? 'disabled' : '')}>${opts([['', 'Elegir seguimiento']].concat(AM.opciones.seguimientos.map((s) => [s.id, segTxt(s)])), d.seguimiento_cc_id)}</select>`, 2)}
        ${campo('Orden de compra', `<select id="am-oc" ${ro || (d.id ? 'disabled' : '')}>${opts([['', ocs.length ? 'Elegir orden de compra' : 'Sin OC en el seguimiento']].concat(ocs.map((o) => [o.id, ocTxt(o)])), d.orden_compra_cliente_id)}</select>`, 2)}
        ${campo('N.º de acta', `<input id="am-num" type="number" min="1" value="${e(d.numero)}" ${ro}>`)}
        ${campo('Fecha', `<input id="am-fecha" type="date" value="${e(d.fecha)}" ${ro}>`)}
        ${campo('Período desde', `<input id="am-desde" type="date" value="${e(d.periodo_desde)}" ${ro}>`)}
        ${campo('Período hasta', `<input id="am-hasta" type="date" value="${e(d.periodo_hasta)}" ${ro}>`)}
        ${campo('Observaciones', `<input id="am-obs" value="${e(d.observaciones)}" ${ro}>`, 4)}</div>`;

    const listo = d.orden_compra_cliente_id && d.periodo_desde && d.periodo_hasta;
    $('am-titulo').innerHTML = `${nuevo ? 'Nueva acta de medición' : `Acta N.º ${e(d.numero)}`} ${badge(d.estado)}`;
    $('am-sub').innerHTML = `${estadoTxt}${(d.certificados || []).length ? ` · Certificado: ${(d.certificados || []).map(certTxt).join(', ')}` : ''}`;
    $('am-acciones').innerHTML = acciones;
    $('am-volver-wrap').innerHTML = `<button type="button" class="back-link btn-link" id="am-volver">${ic('left', 'i-sm')}Actas de medición</button>`;
    if ($('am-mas')) $('am-mas').addEventListener('click', (ev) => SMG.abrirMenu($('am-mas-menu'), ev.currentTarget));
    $('am-body').innerHTML = `
      ${tarjeta('Datos del acta', datos)}
      ${listo ? tarjeta('Equipos', '<div id="am-equipos"></div>', editable() ? `<button type="button" class="btn btn-sm" id="am-eq-partes">${ic('plus', 'i-sm')}Equipos con partes en el período</button>` : '') : ''}
      ${listo ? tarjeta('Mínimo y objetivo por ítem', '<div id="am-metas"></div>', '<span class="muted">Referencias de gestión: no cambian lo que se certifica</span>') : ''}
      ${listo ? tarjeta('Columnas del parte diario', '<div id="am-columnas"></div>') : ''}
      ${listo ? '<div id="am-res" class="stack"></div>' : '<section class="card"><div class="empty-state">Elija el seguimiento, la orden de compra y el período para continuar.</div></section>'}`;

    $('am-volver').addEventListener('click', () => {
      if (AM.sucio && !confirm('Hay cambios sin guardar. ¿Salir igual?')) return;
      vistaLista().catch((err) => toast(err.message, 'error'));
    });
    $('am-seg').addEventListener('change', (ev) => { d.seguimiento_cc_id = ev.target.value; d.orden_compra_cliente_id = ''; renderActa(); });
    $('am-oc').addEventListener('change', (ev) => elegirOc(ev.target.value));
    [['am-num', 'numero'], ['am-fecha', 'fecha'], ['am-obs', 'observaciones']].forEach(([id, k]) => $(id).addEventListener('input', (ev) => { d[k] = ev.target.value; AM.sucio = true; }));
    [['am-desde', 'periodo_desde'], ['am-hasta', 'periodo_hasta']].forEach(([id, k]) => $(id).addEventListener('change', (ev) => {
      d[k] = ev.target.value; AM.sucio = true;
      if ($('am-res')) recalcular(); else renderActa();
    }));
    const clic = (id, fn) => { if ($(id)) $(id).addEventListener('click', fn); };
    clic('am-guardar', guardar);
    clic('am-cerrar', cerrarActa);
    clic('am-reabrir', () => {
      if (!confirm('El acta vuelve a borrador y se recalcula con los partes actuales. ¿Continuar?')) return;
      ejecutar(async () => { await api(`${API}/actas/${d.id}/reabrir`, 'POST'); await abrirActa(d.id); }, 'Acta reabierta.');
    });
    clic('am-anular', () => {
      const motivo = prompt('Motivo de la anulación (mínimo 10 caracteres):');
      if (!motivo) return;
      ejecutar(async () => { await api(`${API}/actas/${d.id}/anular`, 'POST', { motivo }); await abrirActa(d.id); }, 'Acta anulada.');
    });
    clic('am-eliminar', () => {
      if (!confirm('¿Eliminar este borrador?')) return;
      ejecutar(async () => { await api(`${API}/actas/${d.id}`, 'DELETE'); await vistaLista(); }, 'Acta eliminada.');
    });
    clic('am-imprimir', imprimir);
    clic('am-eq-partes', elegirEquiposConPartes);
    if (listo) { renderEquipos(); renderMetas(); renderColumnas(); renderResultado(); }
  }

  async function elegirOc(oc) {
    const d = AM.ed;
    d.orden_compra_cliente_id = oc ? Number(oc) : '';
    AM.sucio = true;
    if (!oc) { renderActa(); return; }
    const o = AM.opciones.ordenes.find((x) => x.id === Number(oc));
    if (o && !d.seguimiento_cc_id) d.seguimiento_cc_id = o.seguimiento_cc_id;
    if (!d.id) {
      try {
        const s = await api(`${API}/sugerencia?oc=${oc}`);
        Object.assign(d, { numero: s.numero, periodo_desde: s.periodo_desde, periodo_hasta: s.periodo_hasta, campo_cantidad: s.campo_cantidad, columnas: s.columnas, equipos: s.equipos,
          metas: s.metas || [], graficos: s.graficos || null });
        if (s.desde_acta) toast(`Se copiaron los equipos, ítems, mínimos/objetivos, columnas y gráficos del acta N° ${s.desde_acta}. Puede cambiarlos.`);
      } catch (err) { toast(err.message, 'error'); }
    } else {
      const items = new Set(itemsDe(oc).map((i) => i.id));
      d.equipos.forEach((x) => { if (!items.has(x.item_oc_cliente_id)) x.item_oc_cliente_id = null; });
      d.metas = (d.metas || []).filter((m) => items.has(m.item_oc_cliente_id));
    }
    renderActa();
    recalcular();
  }

  // ---------------------------------------------------------------- equipos
  function renderEquipos() {
    const d = AM.ed;
    const ro = editable() ? '' : 'disabled';
    const items = itemsDe(d.orden_compra_cliente_id);
    const porCi = Object.fromEntries(((AM.res && AM.res.equipos) || []).map((x) => [x.ci, x]));
    const opItems = [['', 'Sin ítem (no se certifica)']].concat(items.map((i) => [i.id, `${i.numero} · ${i.descripcion || ''}${i.unidad ? ` (${i.unidad})` : ''}`]));
    const opCant = [['', `(${etiqueta(d.campo_cantidad)})`]].concat(numericos().map((c) => [c.campo, c.etiqueta]));
    const filas = d.equipos.map((x, i) => {
      const r = porCi[String(x.ci).toUpperCase()];
      return `<tr><td class="mono"><b>${e(x.ci)}</b></td><td>${e(descEquipo(x.ci))}</td>
        <td><select data-am-eq-cc="${i}" ${ro} style="max-width:220px" title="Solo cuentan los partes de este equipo cargados en este centro de costos (Obra/Proyecto)">${opts(opCentros(), x.centro_costo_id || '')}</select></td>
        <td><select data-am-eq-item="${i}" ${ro} style="min-width:260px">${opts(opItems, x.item_oc_cliente_id)}</select></td>
        <td><select data-am-eq-campo="${i}" ${ro}>${opts(opCant, x.campo_cantidad || '')}</select></td>
        <td >${r ? r.partes.length : ''}</td><td class="num"><b>${r ? nf(r.total) : ''}</b></td>
        <td class="center">${ro ? '' : `<button type="button" class="btn btn-ghost btn-icon btn-sm" data-am-eq-del="${i}" title="Quitar" aria-label="Quitar ${e(x.ci)}">${ic('trash', 'i-sm')}</button>`}</td></tr>`;
    }).join('');
    $('am-equipos').innerHTML = `${items.length ? '' : `<div class="callout warn">${ic('alert')}<div>La OC no tiene ítems cargados: cárguelos en Seguimiento de cliente para poder certificar.</div></div>`}
      <div class="table-scroll"><table class="tbl tbl-static tbl-auto"><thead><tr><th>CI</th><th>Equipo</th><th>Centro de costos</th><th>Ítem de la OC donde se certifica</th><th>Cantidad = suma de</th><th class="num">Partes</th><th class="num">Cantidad</th><th></th></tr></thead>
      <tbody>${filas || '<tr><td colspan="8" class="muted">Sin equipos. Agréguelos con «+ Equipos con partes en el período» o escribiendo el CI.</td></tr>'}</tbody></table></div>
      ${ro ? '' : `<div class="head-actions" style="margin-top:8px"><input id="am-eq-ci" list="am-dl-eq" placeholder="CI del equipo (ej. M-25)" autocomplete="off" style="width:220px">
        <button type="button" class="btn btn-sm" id="am-eq-add">Agregar</button>
        <datalist id="am-dl-eq">${AM.opciones.equipos.map((x) => `<option value="${e(x.codigo)}">${e(x.descripcion)}</option>`).join('')}</datalist></div>`}`;
    const box = $('am-equipos');
    box.querySelectorAll('[data-am-eq-cc]').forEach((s) => s.addEventListener('change', () => { d.equipos[Number(s.dataset.amEqCc)].centro_costo_id = s.value ? Number(s.value) : null; AM.sucio = true; recalcular(); }));
    box.querySelectorAll('[data-am-eq-item]').forEach((s) => s.addEventListener('change', () => { d.equipos[Number(s.dataset.amEqItem)].item_oc_cliente_id = s.value ? Number(s.value) : null; AM.sucio = true; recalcular(); }));
    box.querySelectorAll('[data-am-eq-campo]').forEach((s) => s.addEventListener('change', () => { d.equipos[Number(s.dataset.amEqCampo)].campo_cantidad = s.value || null; AM.sucio = true; recalcular(); }));
    box.querySelectorAll('[data-am-eq-del]').forEach((b) => b.addEventListener('click', () => { d.equipos.splice(Number(b.dataset.amEqDel), 1); AM.sucio = true; renderEquipos(); recalcular(); }));
    const agregar = () => {
      const ci = $('am-eq-ci').value.trim().toUpperCase();
      if (!ci) return;
      if (d.equipos.some((x) => String(x.ci).toUpperCase() === ci)) { toast(`${ci} ya está en el acta.`); return; }
      d.equipos.push({ ci, item_oc_cliente_id: null, campo_cantidad: null, centro_costo_id: ccDelSeguimiento() });
      AM.sucio = true;
      renderEquipos();
      recalcular();
    };
    if ($('am-eq-add')) {
      $('am-eq-add').addEventListener('click', agregar);
      $('am-eq-ci').addEventListener('keydown', (ev) => { if (ev.key === 'Enter') { ev.preventDefault(); agregar(); } });
    }
  }

  async function elegirEquiposConPartes() {
    const d = AM.ed;
    let lista;
    try { lista = await api(`${API}/equipos-con-partes?desde=${d.periodo_desde}&hasta=${d.periodo_hasta}`); } catch (err) { toast(err.message, 'error'); return; }
    const ya = new Set(d.equipos.map((x) => String(x.ci).toUpperCase()));
    const ccLista = [...new Set(lista.map((x) => x.centro_costo_id).filter((x) => x != null))].sort((a, b) => a - b);
    const ccSeg = ccDelSeguimiento();
    const ccIni = ccSeg && ccLista.includes(ccSeg) ? String(ccSeg) : '';
    const opFiltro = [['', 'Todos'], ['sin', 'Sin centro de costos']].concat(ccLista.map((c) => [c, ccTxt(c)]));
    modal(`Equipos con partes del ${fFecha(d.periodo_desde)} al ${fFecha(d.periodo_hasta)}`, lista.length ? `
      <div class="head-actions" style="margin-bottom:8px"><label class="range">Centro de costos (obra / proyecto)
        <select id="am-eq-cc" style="max-width:320px">${opts(opFiltro, ccIni)}</select></label>
        <input id="am-eq-buscar" type="search" placeholder="Buscar CI, equipo o cliente" style="width:260px">
        <span class="muted" id="am-eq-cuenta"></span></div>
      <div class="table-scroll" style="max-height:55vh;overflow:auto"><table class="tbl tbl-static tbl-auto"><thead><tr><th></th><th>CI</th><th>Equipo</th><th>Centro de costos</th><th>Cliente en los partes</th><th>Partes</th><th>Total hs</th></tr></thead><tbody>
      ${lista.map((x) => `<tr data-am-cc="${x.centro_costo_id == null ? 'sin' : x.centro_costo_id}" data-am-buscar="${e(`${x.ci} ${x.descripcion} ${x.clientes.join(' ')}`.toLowerCase())}"><td ><input type="checkbox" data-am-ci="${e(x.ci)}" data-am-ci-cc="${x.centro_costo_id ?? ''}" ${ya.has(x.ci) ? 'checked disabled' : ''}></td>
        <td><b>${e(x.ci)}</b></td><td>${e(x.descripcion)}</td><td>${e(x.centro_costo || 'Sin centro de costos')}</td><td>${e(x.clientes.join(', '))}</td><td >${x.partes}</td><td class="num">${nf(x.total_hs)}</td></tr>`).join('')}
      </tbody></table></div>` : '<p class="muted">No hay partes cargados en ese período.</p>',
    `<button class="btn" type="button" data-am-cerrar>Cancelar</button>${lista.length ? '<button class="btn btn-primary" type="button" id="am-eq-ok">Agregar seleccionados</button>' : ''}`, 'md');
    if (!lista.length) return;
    const filtrar = () => {
      const q = $('am-eq-buscar').value.trim().toLowerCase();
      const cc = $('am-eq-cc').value;
      let n = 0;
      $('am-modal-b').querySelectorAll('[data-am-buscar]').forEach((tr) => {
        const ver = (!q || tr.dataset.amBuscar.includes(q)) && (!cc || tr.dataset.amCc === cc);
        tr.style.display = ver ? '' : 'none';
        if (ver) n += 1;
      });
      $('am-eq-cuenta').textContent = `${n} de ${lista.length}`;
    };
    $('am-eq-buscar').addEventListener('input', filtrar);
    $('am-eq-cc').addEventListener('change', filtrar);
    filtrar();
    $('am-eq-ok').addEventListener('click', () => {
      // Solo las filas visibles (las del centro de costos filtrado); un CI entra una sola vez al acta.
      const marcados = [...$('am-modal-b').querySelectorAll('[data-am-ci]:checked:not(:disabled)')].filter((c) => c.closest('tr').style.display !== 'none');
      const nuevos = [];
      const repetidos = [];
      marcados.forEach((c) => {
        if (nuevos.some((x) => x.ci === c.dataset.amCi)) { repetidos.push(c.dataset.amCi); return; }
        nuevos.push({ ci: c.dataset.amCi, item_oc_cliente_id: null, campo_cantidad: null, centro_costo_id: c.dataset.amCiCc ? Number(c.dataset.amCiCc) : null });
      });
      if (repetidos.length) toast(`${[...new Set(repetidos)].join(', ')}: se marcó en más de un centro de costos; se tomó el primero. Cámbielo en la tabla si hace falta.`);
      nuevos.forEach((x) => d.equipos.push(x));
      cerrarModal();
      if (nuevos.length) { AM.sucio = true; renderEquipos(); recalcular(); toast(`${nuevos.length} equipo(s) agregados. Elija el ítem de la OC de cada uno.`); }
    });
  }

  // ---------------------------------------------------------------- columnas
  function renderColumnas() {
    const d = AM.ed;
    const ro = editable() ? '' : 'disabled';
    const elegidas = new Set(d.columnas);
    $('am-columnas').innerHTML = `<div class="form-grid" style="margin-bottom:12px">
        ${campo('Cantidad a certificar = suma de la columna', `<select id="am-campo" ${ro}>${opts(numericos().map((c) => [c.campo, c.etiqueta]), d.campo_cantidad)}</select>`)}
        <p class="help" style="align-self:end">Cada equipo puede usar otra columna (columna «Cantidad = suma de» en Equipos).</p></div>
      <div class="field"><label>Columnas que se muestran en el detalle de partes</label>
      <div class="chip-row" style="gap:8px 16px;margin-top:4px">${campos().map((c) => `<label class="check">
        <input type="checkbox" data-am-col="${e(c.campo)}" ${elegidas.has(c.campo) ? 'checked' : ''} ${ro}>${e(c.etiqueta)}</label>`).join('')}</div></div>`;
    if (ro) return;
    $('am-campo').addEventListener('change', (ev) => { d.campo_cantidad = ev.target.value; AM.sucio = true; renderEquipos(); recalcular(); });
    $('am-columnas').querySelectorAll('[data-am-col]').forEach((c) => c.addEventListener('change', () => {
      const orden = campos().map((x) => x.campo);
      const set = new Set(d.columnas);
      if (c.checked) set.add(c.dataset.amCol); else set.delete(c.dataset.amCol);
      d.columnas = orden.filter((x) => set.has(x));
      AM.sucio = true;
      recalcular();
    }));
  }

  // ---------------------------------------------------------------- resultado
  function payload() {
    const d = AM.ed;
    return {
      id: d.id, orden_compra_cliente_id: d.orden_compra_cliente_id, numero: d.numero, periodo_desde: d.periodo_desde, periodo_hasta: d.periodo_hasta,
      fecha: d.fecha, campo_cantidad: d.campo_cantidad, columnas: d.columnas, observaciones: d.observaciones,
      equipos: d.equipos.map((x) => ({ ci: x.ci, item_oc_cliente_id: x.item_oc_cliente_id || null, campo_cantidad: x.campo_cantidad || null, centro_costo_id: x.centro_costo_id || null })),
      metas: (d.metas || []).map((m) => ({ item_oc_cliente_id: m.item_oc_cliente_id, minimo: m.minimo ?? null, objetivo: m.objetivo ?? null })),
      graficos: d.graficos,
    };
  }

  function recalcular() {
    if (!editable() || !$('am-res')) return;
    clearTimeout(AM.timer);
    AM.timer = setTimeout(async () => {
      const d = AM.ed;
      if (!d.orden_compra_cliente_id || !d.periodo_desde || !d.periodo_hasta) return;
      $('am-res').style.opacity = '0.5';
      try {
        AM.res = await api(`${API}/vista-previa`, 'POST', payload());
        renderEquipos();
        actualizarMetas();
        renderResultado();
      } catch (err) { toast(err.message, 'error'); } finally { if ($('am-res')) $('am-res').style.opacity = ''; }
    }, 350);
  }

  function tablaResumen(res) {
    const conMeta = res.items_oc.some((i) => i.estado_meta);
    const n = conMeta ? 9 : 7;
    return `<div class="table-scroll"><table class="tbl tbl-static tbl-auto"><thead><tr><th>Ítem OC</th><th>Descripción</th><th>Unidad</th><th>Equipos</th><th>Cantidad</th><th>Precio</th><th>Importe</th>${conMeta ? '<th>Mín. / Obj.</th><th>Cumplimiento</th>' : ''}</tr></thead><tbody>
      ${res.items_oc.map((i) => `<tr><td >${e(i.numero)}</td><td>${e(i.descripcion)}</td><td >${e(i.unidad || '')}</td><td>${e(i.equipos.join(', '))}</td>
        <td class="num"><b>${nf(i.cantidad_periodo)}</b></td><td class="num">${money(i.precio)}</td><td class="num">${money(i.importe)}</td>
        ${conMeta ? `<td class="num">${i.minimo == null ? '—' : nf(i.minimo)} / ${i.objetivo == null ? '—' : nf(i.objetivo)}</td><td>${chipMeta(i)}</td>` : ''}</tr>`).join('')
      || `<tr><td colspan="${n}" class="muted">Ningún equipo tiene ítem de la OC asignado.</td></tr>`}</tbody>
      ${res.items_oc.length ? `<tfoot><tr class="tot"><td colspan="6">Total</td><td class="num">${money(res.total_importe)}</td>${conMeta ? '<td colspan="2"></td>' : ''}</tr></tfoot>` : ''}</table></div>`;
  }

  function tablaIndicadores(res) {
    const ind = res.indicadores;
    if (!ind || !ind.equipos.length) return '';
    const cols = ind.columnas;
    const celdas = (f, general) => `<td class="num">${general ? '' : f.dias}</td><td class="num">${general ? '' : f.partes}</td>${cols.map((c) => `<td class="num">${e(nf(f.valores[c.campo]))}</td>`).join('')}<td class="num">${f.consumo_lh == null ? '—' : e(nf(f.consumo_lh))}</td>`;
    return `<div class="table-scroll"><table class="tbl tbl-static tbl-auto"><thead><tr><th>Equipo</th><th>Días</th><th>Partes</th>${cols.map((c) => `<th>${e(c.etiqueta)}</th>`).join('')}<th>Consumo (L/h)</th></tr></thead><tbody>
      ${ind.equipos.map((f) => `<tr><td><b>${e(f.ci)}</b> <span class="muted">${e(f.descripcion)}</span></td>${celdas(f)}</tr>`).join('')}</tbody>
      <tfoot><tr class="tot"><td>General</td>${celdas(ind.general, true)}</tr></tfoot></table></div>`;
  }

  function tablaEquipo(eq, cols) {
    const celda = (c, v) => {
      if (c.campo.startsWith('fecha')) return `<td>${e(fFecha(String(v ?? '').slice(0, 10)))}</td>`;
      return c.numerico ? `<td class="num">${e(nf(v))}</td>` : `<td>${e(v ?? '')}</td>`;
    };
    return `<div class="table-scroll"><table class="tbl tbl-static tbl-auto"><thead><tr>${cols.map((c) => `<th>${e(c.etiqueta)}</th>`).join('')}<th>Cantidad<br><span class="muted">${e(eq.etiqueta_cantidad)}</span></th></tr></thead><tbody>
      ${eq.partes.map((p) => `<tr>${cols.map((c) => celda(c, p.valores[c.campo])).join('')}<td class="num"><b>${nf(p.cantidad)}</b></td></tr>`).join('')
      || `<tr><td colspan="${cols.length + 1}" class="muted">Sin partes en el período.</td></tr>`}</tbody>
      ${eq.partes.length ? `<tfoot><tr class="tot">${cols.map((c, i) => `<td class="num">${i === 0 ? 'TOTAL' : (c.campo in eq.totales ? e(nf(eq.totales[c.campo])) : '')}</td>`).join('')}<td class="num">${nf(eq.total)}</td></tr></tfoot>` : ''}
      </table></div>`;
  }

  function renderResultado() {
    const res = AM.res;
    if (!$('am-res')) return;
    if (!res) { $('am-res').innerHTML = ''; return; }
    const avisos = res.avisos.length ? `<div class="callout warn">${ic('alert')}<div>${res.avisos.map(e).join('<br>')}</div></div>` : '';
    const detalle = res.equipos.map((eq) => `<details class="card card-fold" style="margin-bottom:8px" ${res.equipos.length <= 2 ? 'open' : ''}>
        <summary class="card-head" style="padding-bottom:12px"><span><b class="mono">${e(eq.ci)}</b> ${e(eq.descripcion)}${eq.centro_costo ? ` · CC ${e(eq.centro_costo)}` : ''} · <span class="muted">${e(eq.item_oc ? `ítem ${eq.item_oc}` : 'sin ítem de OC')}</span>
        · ${eq.partes.length} partes · <b>${nf(eq.total)}</b> ${e(eq.etiqueta_cantidad)}</span>${ic('chevron-down', 'i-sm fold-ico')}</summary>${tablaEquipo(eq, res.columnas)}</details>`).join('');
    const puedeAcomodar = AM.ed.estado !== 'ANULADA' && puede('generar');
    const accKpi = AM.acomodar
      ? `<select id="am-g-tipo" class="select" style="width:auto;height:28px;font-size:12px" aria-label="Tipo de gráfico">${opts(Object.entries(TIPOS_G), 'items_meta')}</select><button type="button" class="btn btn-sm" id="am-g-add">${ic('plus', 'i-sm')}Agregar</button>
         <button type="button" class="btn btn-sm" id="am-g-reset" title="Vuelve a los gráficos de siempre">Restablecer</button><button type="button" class="btn btn-sm btn-primary" id="am-g-listo">Listo</button>`
      : (puedeAcomodar ? '<button type="button" class="btn btn-sm" id="am-g-acomodar">Acomodar gráficos</button>' : '');
    $('am-res').innerHTML = `${tarjeta('A certificar por ítem de la OC', avisos + tablaResumen(res), `<span class="muted">${res.total_partes} partes${AM.sucio ? ' · cambios sin guardar' : ''}</span>`)}
      ${tarjeta('Gráficos KPI del acta', '<div id="am-kpi"></div>', accKpi)}
      ${res.indicadores && res.indicadores.equipos.length ? tarjeta('Indicadores por equipo', tablaIndicadores(res), '<span class="muted">Consumo = combustible / horas efectivas</span>') : ''}
      ${tarjeta('Detalle de partes por equipo', detalle || '<p class="muted">Agregue equipos para ver sus partes.</p>')}`;
    renderKpi();
  }


  // ================================================================= MÍNIMO Y OBJETIVO POR ÍTEM
  const ESTADOS_META = {
    BAJO_MINIMO: ['chip-bad', '▼', 'Bajo el mínimo'], BAJO_OBJETIVO: ['chip-warn', '●', 'Bajo el objetivo'],
    CUMPLE_MINIMO: ['chip-accent', '✓', 'Cumple el mínimo'], OBJETIVO: ['chip-ok', '★', 'Alcanzó el objetivo'],
  };
  const chipMeta = (i) => {
    const x = i && ESTADOS_META[i.estado_meta];
    return x ? `<span class="chip ${x[0]}" title="${e(x[2])}">${x[1]} ${e(x[2])}${i.pct_objetivo != null ? ` · ${nf(i.pct_objetivo, 1)}%` : ''}</span>` : '<span class="muted">sin meta</span>';
  };
  const metaDe = (itemId) => (AM.ed.metas || []).find((m) => m.item_oc_cliente_id === itemId);
  const lineaRes = (itemId) => ((AM.res && AM.res.items_oc) || []).find((x) => x.item_oc_cliente_id === itemId);

  function renderMetas() {
    const d = AM.ed;
    const box = $('am-metas');
    if (!box) return;
    const ro = editable() ? '' : 'disabled';
    const items = itemsDe(d.orden_compra_cliente_id);
    if (!items.length) { box.innerHTML = '<p class="muted">La OC no tiene ítems cargados.</p>'; return; }
    // En un acta cerrada se muestran los valores con que se cerró (quedaron en el resultado).
    const valor = (it, k) => {
      const r = d.estado !== 'BORRADOR' ? lineaRes(it.id) : null;
      const v = r && r[k] !== undefined ? r[k] : (metaDe(it.id) || {})[k];
      return v == null ? '' : v;
    };
    box.innerHTML = `<div class="table-scroll"><table class="tbl tbl-static tbl-auto"><thead><tr><th>Ítem OC</th><th>Descripción</th><th>Unidad</th><th class="num">Cantidad del período</th>
      <th>Mínimo</th><th>Objetivo</th><th style="min-width:180px">Avance</th><th>Cumplimiento</th></tr></thead><tbody>
      ${items.map((it) => `<tr><td >${e(it.numero)}</td><td>${e(it.descripcion || '')}</td><td >${e(it.unidad || '')}</td>
        <td class="num"><b data-am-mt-cant="${it.id}"></b></td>
        <td><input type="number" min="0" step="any" style="width:100px" data-am-mt="${it.id}" data-k="minimo" value="${e(valor(it, 'minimo'))}" ${ro}></td>
        <td><input type="number" min="0" step="any" style="width:100px" data-am-mt="${it.id}" data-k="objetivo" value="${e(valor(it, 'objetivo'))}" ${ro}></td>
        <td data-am-mt-barra="${it.id}"></td><td data-am-mt-chip="${it.id}"></td></tr>`).join('')}
      </tbody></table></div>
      <p class="muted" style="margin:6px 0 0">Deje vacío lo que no aplique. El estado compara la cantidad del período del ítem (suma de sus equipos) contra el mínimo y el objetivo.</p>`;
    box.querySelectorAll('[data-am-mt]').forEach((inp) => inp.addEventListener('change', () => {
      const id = Number(inp.dataset.amMt);
      const k = inp.dataset.k;
      const v = inp.value.trim() === '' ? null : Number(inp.value);
      if (v !== null && (Number.isNaN(v) || v < 0)) { toast('Ingrese un número mayor o igual a cero.', 'error'); inp.value = ''; return; }
      let m = metaDe(id);
      if (!m) { m = { item_oc_cliente_id: id, minimo: null, objetivo: null }; d.metas.push(m); }
      m[k] = v;
      if (m.minimo != null && m.objetivo != null && m.minimo > m.objetivo) toast('El mínimo no puede superar al objetivo.', 'error');
      d.metas = d.metas.filter((x) => x.minimo != null || x.objetivo != null);
      AM.sucio = true;
      recalcular();
    }));
    actualizarMetas();
  }

  // Solo refresca cantidades y estados (no vuelve a dibujar los campos para no perder el foco mientras se escribe).
  function actualizarMetas() {
    const box = $('am-metas');
    if (!box) return;
    itemsDe(AM.ed.orden_compra_cliente_id).forEach((it) => {
      const r = lineaRes(it.id);
      const cant = box.querySelector(`[data-am-mt-cant="${it.id}"]`);
      if (cant) cant.textContent = r ? nf(r.cantidad_periodo) : '—';
      const chip = box.querySelector(`[data-am-mt-chip="${it.id}"]`);
      if (chip) chip.innerHTML = r ? chipMeta(r) : '<span class="muted">sin equipos</span>';
      const barra = box.querySelector(`[data-am-mt-barra="${it.id}"]`);
      if (barra) barra.innerHTML = r && (r.objetivo || r.minimo) ? barraMeta(r) : '';
    });
  }

  function barraMeta(r) {
    const tope = Math.max(r.cantidad_periodo || 0, r.objetivo || 0, r.minimo || 0) * 1.05 || 1;
    const pct = (v) => `${Math.min(100, (v / tope) * 100).toFixed(1)}%`;
    return `<div style="position:relative;height:10px;background:var(--surface-2, #e6edf5);border-radius:999px" title="Cantidad ${nf(r.cantidad_periodo)} · mínimo ${r.minimo == null ? '—' : nf(r.minimo)} · objetivo ${r.objetivo == null ? '—' : nf(r.objetivo)}">
      <i style="position:absolute;left:0;top:0;bottom:0;width:${pct(r.cantidad_periodo || 0)};background:${COL.barra};border-radius:3px"></i>
      ${r.minimo != null ? `<i style="position:absolute;top:-3px;bottom:-3px;left:${pct(r.minimo)};border-left:2px dashed ${COL.min}"></i>` : ''}
      ${r.objetivo != null ? `<i style="position:absolute;top:-3px;bottom:-3px;left:${pct(r.objetivo)};border-left:2px solid ${COL.obj}"></i>` : ''}</div>`;
  }

  // ================================================================= GRÁFICOS KPI
  // Cada acta guarda qué gráficos ve y en qué orden (null = los de siempre). Se dibujan en SVG, sin librerías.
  // Colores de los gráficos: los del tema en pantalla; los de papel (claros) mientras se arma la impresión.
  const tema = (v, papel) => (AM.imprimiendo || typeof SMG === 'undefined' ? papel : (SMG.css(v) || papel));
  const COL = {
    get barra() { return tema('--accent', '#1565c0'); }, get min() { return tema('--danger', '#b91c1c'); }, get obj() { return tema('--success', '#15803d'); },
    get ritmo() { return tema('--text-faint', '#8a99ad'); }, get grid() { return tema('--border', '#e3e9f1'); }, get eje() { return tema('--text-muted', '#5b6b80'); },
    get texto() { return tema('--text', '#0f2440'); },
  };
  const TIPOS_G = { tarjetas: 'Tarjetas de resumen', items_meta: 'Cantidad por ítem vs. mínimo y objetivo', avance_item: 'Avance acumulado del período',
    equipos: 'Indicador por equipo', diario: 'Cantidad diaria' };
  const IND_G = [['cantidad', 'Cantidad a certificar'], ['total_hs', 'Horas efectivas'], ['gasoil', 'Combustible (L)'], ['consumo_lh', 'Consumo (L/h)'],
    ['dias', 'Días con partes'], ['partes', 'Partes'], ['aceite', 'Aceite (L)'], ['km_total', 'Km recorridos'], ['viajes', 'Viajes'],
    ['hs_disposicion', 'Hs a disposición'], ['hs_mantenimiento', 'Hs mantenimiento'], ['hs_reparaciones', 'Hs reparaciones']];
  let GRAFICOS_DEFECTO = [{ tipo: 'tarjetas', ancho: 'completo' }, { tipo: 'items_meta', ancho: 'medio' },
    { tipo: 'equipos', indicador: 'total_hs', ancho: 'medio' }, { tipo: 'diario', ancho: 'completo' }];
  // Solo para la ventana de impresión (documento aparte, sin app.css): mismas clases que en pantalla.
  const KPI_CSS = `.chart-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
    .chart-card{border:1px solid #c3d0e0;border-radius:8px;padding:10px 12px;min-width:0}.chart-card.is-full{grid-column:1/-1}
    .chart-card h4{margin:0 0 8px;font-size:13px}.chart-card svg{display:block;width:100%;height:auto;overflow:visible}
    .legend{display:flex;gap:14px;flex-wrap:wrap;font-size:11px;color:#555;margin-top:6px}.legend span{display:inline-flex;align-items:center;gap:5px}
    .legend i.line{display:inline-block;width:14px;height:0;border-top:3px solid}
    .kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px}.kpi{border:1px solid #c3d0e0;border-radius:8px;padding:8px 10px}
    .kpi-value{font-size:20px;font-weight:800}.kpi-label,.kpi-sub{font-size:11px;color:#555;display:block}`;

  const listaGraficos = () => (AM.ed && AM.ed.graficos) || GRAFICOS_DEFECTO;
  const nombreItem = (id) => {
    const it = AM.opciones.items_oc.find((x) => x.id === Number(id));
    return it ? `ítem ${it.numero}${it.descripcion ? ` · ${it.descripcion}` : ''}` : 'todos los ítems';
  };
  const nombreInd = (k) => (IND_G.find((x) => x[0] === k) || [k, k])[1];
  function tituloG(g) {
    if (g.titulo) return g.titulo;
    if (g.tipo === 'equipos') return `${nombreInd(g.indicador || 'total_hs')} por equipo`;
    if (g.tipo === 'avance_item') return `Avance acumulado · ${nombreItem(g.item_oc_cliente_id)}`;
    if (g.tipo === 'diario') return `Cantidad diaria · ${nombreItem(g.item_oc_cliente_id)}`;
    return TIPOS_G[g.tipo] || g.tipo;
  }
  const corto = (t, n) => (String(t).length > n ? `${String(t).slice(0, n - 1)}…` : String(t));
  const vacioG = (t) => `<div class="empty-state">${e(t)}</div>`;
  const ley = (lista) => `<div class="legend">${lista.map(([c, t, dash]) => `<span><i class="line" style="border-top-color:${c};${dash ? 'border-top-style:dashed' : ''}"></i>${e(t)}</span>`).join('')}</div>`;
  const txt = (x, y, t, { anchor = 'start', size = 11, color = COL.eje, peso = 400 } = {}) => `<text x="${x}" y="${y}" text-anchor="${anchor}" font-size="${size}" font-weight="${peso}" fill="${color}" font-family="inherit">${e(t)}</text>`;
  function pasoEje(max) {
    const bruto = max / 4;
    const p = 10 ** Math.floor(Math.log10(bruto || 1));
    return [1, 2, 2.5, 5, 10].map((m) => m * p).find((m) => m >= bruto) || p * 10;
  }

  // Días del período (YYYY-MM-DD).
  function diasPeriodo() {
    const out = [];
    const d0 = new Date(`${AM.ed.periodo_desde}T00:00:00Z`);
    const d1 = new Date(`${AM.ed.periodo_hasta}T00:00:00Z`);
    for (let d = d0; d <= d1 && out.length < 400; d = new Date(d.getTime() + 86400000)) out.push(d.toISOString().slice(0, 10));
    return out;
  }
  // Cantidad por día (y por equipo) de los equipos del ítem elegido (o de todos los que tienen ítem).
  function serieDiaria(res, itemId) {
    const dias = diasPeriodo();
    const pos = Object.fromEntries(dias.map((x, i) => [x, i]));
    const serie = dias.map((f) => ({ fecha: f, valor: 0, det: {} }));
    res.equipos.filter((q) => (itemId ? q.item_oc_cliente_id === Number(itemId) : q.item_oc_cliente_id)).forEach((q) => {
      q.partes.forEach((p) => {
        const i = pos[p.fecha];
        if (i === undefined) return;
        serie[i].valor += Number(p.cantidad) || 0;
        serie[i].det[q.ci] = (serie[i].det[q.ci] || 0) + (Number(p.cantidad) || 0);
      });
    });
    return serie;
  }
  function metasDeG(res, itemId) {
    const lineas = itemId ? res.items_oc.filter((x) => x.item_oc_cliente_id === Number(itemId)) : res.items_oc;
    const suma = (k) => (lineas.length && lineas.every((x) => x[k] != null) ? lineas.reduce((a, x) => a + x[k], 0) : null);
    return { minimo: suma('minimo'), objetivo: suma('objetivo') };
  }

  // Ejes comunes para series diarias.
  function marcoDiario(W, H, dias, max, pad) {
    const paso = pasoEje(max);
    const top = Math.ceil(max / paso) * paso || 1;
    const y = (v) => pad.t + (H - pad.t - pad.b) * (1 - v / top);
    let s = '';
    for (let v = 0; v <= top + 1e-9; v += paso) s += `<line x1="${pad.l}" x2="${W - pad.r}" y1="${y(v)}" y2="${y(v)}" stroke="${COL.grid}"/>${txt(pad.l - 6, y(v) + 4, nf(v, 1), { anchor: 'end' })}`;
    const cada = Math.max(1, Math.ceil(dias.length / Math.max(2, Math.floor((W - pad.l - pad.r) / 48))));
    const ancho = (W - pad.l - pad.r) / dias.length;
    dias.forEach((f, i) => { if (i % cada === 0) s += txt(pad.l + ancho * (i + 0.5), H - 6, `${f.slice(8, 10)}/${f.slice(5, 7)}`, { anchor: 'middle' }); });
    return { s, y, ancho, top };
  }

  function gAvance(res, g, W) {
    const serie = serieDiaria(res, g.item_oc_cliente_id);
    if (!serie.length) return vacioG('Período vacío.');
    const { minimo, objetivo } = metasDeG(res, g.item_oc_cliente_id);
    let acum = 0;
    const cum = serie.map((x) => (acum += x.valor));
    const H = 230;
    const pad = { l: 52, r: 14, t: 12, b: 24 };
    const m = marcoDiario(W, H, serie.map((x) => x.fecha), Math.max(acum, objetivo || 0, minimo || 0) * 1.05 || 1, pad);
    const x = (i) => pad.l + m.ancho * (i + 0.5);
    const hasta = serie.length - 1;
    let s = m.s;
    if (objetivo != null) {
      s += `<line x1="${x(0)}" y1="${m.y(0)}" x2="${x(hasta)}" y2="${m.y(objetivo)}" stroke="${COL.ritmo}" stroke-width="1.5" stroke-dasharray="5 4"/>`;
      s += `<line x1="${pad.l}" x2="${W - pad.r}" y1="${m.y(objetivo)}" y2="${m.y(objetivo)}" stroke="${COL.obj}" stroke-width="2"/>${txt(pad.l + 6, m.y(objetivo) - 5, `Objetivo ${nf(objetivo)}`, { color: COL.texto })}`;
    }
    if (minimo != null) s += `<line x1="${pad.l}" x2="${W - pad.r}" y1="${m.y(minimo)}" y2="${m.y(minimo)}" stroke="${COL.min}" stroke-width="2" stroke-dasharray="4 3"/>${txt(pad.l + 6, m.y(minimo) + 14, `Mínimo ${nf(minimo)}`, { color: COL.texto })}`;
    s += `<path d="${cum.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${m.y(v).toFixed(1)}`).join(' ')}" fill="none" stroke="${COL.barra}" stroke-width="2.5" stroke-linejoin="round"/>`;
    s += `<circle cx="${x(hasta)}" cy="${m.y(acum)}" r="4" fill="${COL.barra}" stroke="${tema('--surface', '#fff')}" stroke-width="2"/>${txt(x(hasta) - 8, m.y(acum) - 8, nf(acum), { anchor: 'end', peso: 700, color: COL.texto })}`;
    serie.forEach((d, i) => {
      const ritmo = objetivo != null && hasta > 0 ? ` · ritmo para el objetivo ${nf((objetivo * i) / hasta)}` : '';
      s += `<rect class="hit" x="${pad.l + m.ancho * i}" y="${pad.t}" width="${m.ancho}" height="${H - pad.t - pad.b}" fill="transparent"><title>${e(`${fFecha(d.fecha)}: ${nf(d.valor)} en el día · acumulado ${nf(cum[i])}${ritmo}`)}</title></rect>`;
    });
    return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${e(tituloG(g))}">${s}</svg>${ley([[COL.barra, 'Acumulado'], ...(objetivo != null ? [[COL.obj, 'Objetivo'], [COL.ritmo, 'Ritmo para llegar al objetivo', true]] : []), ...(minimo != null ? [[COL.min, 'Mínimo', true]] : [])])}`;
  }

  function gDiario(res, g, W) {
    const serie = serieDiaria(res, g.item_oc_cliente_id);
    if (!serie.length) return vacioG('Período vacío.');
    const H = 210;
    const pad = { l: 52, r: 14, t: 12, b: 24 };
    const max = Math.max(...serie.map((x) => x.valor));
    if (!max) return vacioG('Sin cantidades en el período.');
    const m = marcoDiario(W, H, serie.map((x) => x.fecha), max * 1.08, pad);
    const bw = Math.max(2, Math.min(28, m.ancho - 2));
    const dias = serie.filter((x) => x.valor).length;
    let s = m.s;
    serie.forEach((d, i) => {
      const x0 = pad.l + m.ancho * i + (m.ancho - bw) / 2;
      const det = Object.entries(d.det).map(([ci, v]) => `${ci}: ${nf(v)}`).join(' · ');
      if (d.valor) s += `<rect x="${x0}" y="${m.y(d.valor)}" width="${bw}" height="${Math.max(0, m.y(0) - m.y(d.valor))}" rx="${Math.min(3, bw / 2)}" fill="${COL.barra}"/>`;
      s += `<rect class="hit" x="${pad.l + m.ancho * i}" y="${pad.t}" width="${m.ancho}" height="${H - pad.t - pad.b}" fill="transparent"><title>${e(`${fFecha(d.fecha)}: ${nf(d.valor)}${det ? ` (${det})` : ''}`)}</title></rect>`;
    });
    return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${e(tituloG(g))}">${s}</svg><div class="legend"><span>${dias} días con cantidad · promedio ${nf(serie.reduce((a, x) => a + x.valor, 0) / (dias || 1))} por día trabajado</span></div>`;
  }

  // Barras horizontales (una fila por ítem o por equipo) con marcas opcionales de mínimo y objetivo.
  function barrasH(filas, W, conMetas) {
    if (!filas.length) return vacioG('Sin datos.');
    const izq = Math.min(220, Math.max(90, W * 0.3));
    const der = 92;
    const alto = 30;
    const H = filas.length * alto + 6;
    const max = Math.max(1e-9, ...filas.map((f) => Math.max(f.valor || 0, f.minimo || 0, f.objetivo || 0))) * 1.04;
    const x = (v) => izq + (W - izq - der) * (Math.max(0, v) / max);
    let s = '';
    filas.forEach((f, i) => {
      const y = i * alto + 3;
      const by = y + 8;
      const bh = alto - 16;
      const fin = x(Math.max(f.valor || 0, f.minimo || 0, f.objetivo || 0));
      s += `<g><title>${e(f.tip)}</title><rect class="hit" x="0" y="${y}" width="${W}" height="${alto}" fill="transparent"/>
        ${txt(izq - 8, by + bh / 2 + 4, corto(f.etiqueta, Math.floor(izq / 6.2)), { anchor: 'end', color: COL.texto })}
        <rect x="${izq}" y="${by}" width="${Math.max(f.valor ? 2 : 0, x(f.valor || 0) - izq)}" height="${bh}" rx="3" fill="${COL.barra}"/>
        ${conMetas && f.minimo != null ? `<line x1="${x(f.minimo)}" x2="${x(f.minimo)}" y1="${by - 4}" y2="${by + bh + 4}" stroke="${COL.min}" stroke-width="2" stroke-dasharray="3 2"/>` : ''}
        ${conMetas && f.objetivo != null ? `<line x1="${x(f.objetivo)}" x2="${x(f.objetivo)}" y1="${by - 4}" y2="${by + bh + 4}" stroke="${COL.obj}" stroke-width="2.5"/>` : ''}
        ${txt(fin + 6, by + bh / 2 + 4, f.texto, { color: COL.texto, peso: 700 })}</g>`;
    });
    return `<svg viewBox="0 0 ${W} ${H}" role="img">${s}</svg>`;
  }

  function gItems(res, g, W) {
    if (!res.items_oc.length) return vacioG('Ningún equipo tiene ítem de la OC.');
    const hayMin = res.items_oc.some((i) => i.minimo != null);
    const hayObj = res.items_oc.some((i) => i.objetivo != null);
    const filas = res.items_oc.map((i) => {
      const est = ESTADOS_META[i.estado_meta];
      return { etiqueta: `${i.numero} · ${i.descripcion || ''}`, valor: i.cantidad_periodo, minimo: i.minimo, objetivo: i.objetivo,
        texto: `${nf(i.cantidad_periodo)}${i.pct_objetivo != null ? ` · ${nf(i.pct_objetivo, 0)}%` : ''}${est ? ` ${est[1]}` : ''}`,
        tip: `Ítem ${i.numero}: ${nf(i.cantidad_periodo)} ${i.unidad || ''} · mínimo ${i.minimo == null ? '—' : nf(i.minimo)} · objetivo ${i.objetivo == null ? '—' : nf(i.objetivo)}${est ? ` · ${est[2]}` : ''}` };
    });
    return barrasH(filas, W, true) + ley([[COL.barra, 'Cantidad del período'], ...(hayMin ? [[COL.min, 'Mínimo', true]] : []), ...(hayObj ? [[COL.obj, 'Objetivo (% = avance)']] : []),
      ...(!hayMin && !hayObj ? [[COL.ritmo, 'Cargue mínimo y objetivo por ítem para ver las marcas']] : [])]);
  }

  function valorEquipo(res, ci, ind) {
    const q = res.equipos.find((x) => x.ci === ci) || {};
    const f = ((res.indicadores && res.indicadores.equipos) || []).find((x) => x.ci === ci) || { valores: {} };
    if (ind === 'cantidad') return q.total || 0;
    if (ind === 'dias' || ind === 'partes' || ind === 'consumo_lh') return f[ind];
    return f.valores[ind];
  }
  function gEquipos(res, g, W) {
    const ind = g.indicador || 'total_hs';
    const filas = res.equipos.map((q) => {
      const v = valorEquipo(res, q.ci, ind);
      return { etiqueta: `${q.ci} ${q.descripcion || ''}`, valor: v || 0, texto: v == null ? '—' : nf(v), tip: `${q.ci} ${q.descripcion || ''}: ${v == null ? 'sin dato' : nf(v)} ${nombreInd(ind)}${ind === 'cantidad' ? ` (${q.etiqueta_cantidad})` : ''}` };
    });
    if (!filas.length) return vacioG('Agregue equipos al acta.');
    if (!filas.some((f) => f.valor)) return vacioG(`Sin valores de ${nombreInd(ind).toLowerCase()} en el período.`);
    return barrasH(filas, W, false);
  }

  function gTarjetas(res) {
    const k = res.kpi || {};
    const ind = (res.indicadores && res.indicadores.general) || { valores: {} };
    const cant = res.items_oc.reduce((a, x) => a + (x.cantidad_periodo || 0), 0);
    const t = (l, v, cls = '', sub = '') => `<div class="card kpi ${cls === 'mal' ? 'is-danger' : cls === 'bien' ? 'is-success' : ''}"><span class="kpi-label">${e(l)}</span><span class="kpi-value sm">${v}</span>${sub ? `<span class="kpi-sub">${sub}</span>` : ''}</div>`;
    return `<div class="kpis kpis-tight">
      ${t('Importe a certificar', money(res.total_importe))}
      ${t('Cantidad a certificar', nf(cant), '', `${res.items_oc.length} ítem(s) · ${res.equipos.length} equipo(s)`)}
      ${k.pct_objetivo != null ? t('Avance sobre el objetivo', `${nf(k.pct_objetivo, 1)}%`, k.pct_objetivo >= 100 ? 'bien' : '', 'ítems con objetivo') : ''}
      ${k.items_con_meta ? t('Ítems bajo el mínimo', `${k.bajo_minimo ? '▼ ' : '✓ '}${k.bajo_minimo} de ${k.items_con_meta}`, k.bajo_minimo ? 'mal' : 'bien') : ''}
      ${k.items_con_meta ? t('Ítems que alcanzaron el objetivo', `${k.en_objetivo} de ${k.items_con_meta}`, k.en_objetivo && k.en_objetivo === k.items_con_meta ? 'bien' : '') : ''}
      ${ind.valores.total_hs != null ? t('Horas efectivas', nf(ind.valores.total_hs)) : ''}
      ${ind.consumo_lh != null ? t('Consumo general', `${nf(ind.consumo_lh)} L/h`) : ''}
      ${t('Partes del período', nf(res.total_partes, 0))}</div>`;
  }

  function dibujarG(g, res, W) {
    if (g.tipo === 'tarjetas') return gTarjetas(res);
    if (g.tipo === 'items_meta') return gItems(res, g, W);
    if (g.tipo === 'avance_item') return gAvance(res, g, W);
    if (g.tipo === 'equipos') return gEquipos(res, g, W);
    if (g.tipo === 'diario') return gDiario(res, g, W);
    return vacioG('Gráfico desconocido.');
  }

  function herramientasG(g, i, total) {
    const itemsRes = AM.res.items_oc.map((x) => [x.item_oc_cliente_id, `Ítem ${x.numero} · ${corto(x.descripcion || '', 30)}`]);
    const extra = g.tipo === 'equipos'
      ? `<select class="select" style="width:auto;height:28px;font-size:12px" data-am-g-ind="${i}" aria-label="Indicador">${opts(IND_G, g.indicador || 'total_hs')}</select>`
      : (g.tipo === 'avance_item' || g.tipo === 'diario' ? `<select class="select" style="width:auto;height:28px;font-size:12px" data-am-g-item="${i}" aria-label="Ítem">${opts([['', 'Todos los ítems']].concat(itemsRes), g.item_oc_cliente_id || '')}</select>` : '');
    return `<div class="head-actions" style="margin:-2px 0 8px">
      <button type="button" class="btn btn-sm btn-icon" data-am-g-mov="${i}" data-d="-1" ${i === 0 ? 'disabled' : ''} title="Subir" aria-label="Subir">${ic('up', 'i-sm')}</button>
      <button type="button" class="btn btn-sm btn-icon" data-am-g-mov="${i}" data-d="1" ${i === total - 1 ? 'disabled' : ''} title="Bajar" aria-label="Bajar">${ic('down', 'i-sm')}</button>
      <button type="button" class="btn btn-sm" data-am-g-ancho="${i}">${g.ancho === 'completo' ? 'Medio ancho' : 'Ancho completo'}</button>
      ${extra}<button type="button" class="btn btn-ghost btn-sm btn-icon" data-am-g-del="${i}" title="Quitar" aria-label="Quitar gráfico">${ic('trash', 'i-sm')}</button></div>`;
  }

  function renderKpi() {
    const box = $('am-kpi');
    if (!box || !AM.res) return;
    const lista = listaGraficos();
    if (!lista.length) {
      box.innerHTML = `<div class="empty-state">Sin gráficos. ${AM.acomodar ? 'Agregue uno con «Agregar».' : 'Use «Acomodar gráficos» para elegirlos.'}</div>`;
    } else {
      box.innerHTML = `<div class="chart-grid">${lista.map((g, i) => `<div class="card chart-card ${g.ancho === 'completo' ? 'is-full' : ''} ${AM.acomodar ? 'is-arranging' : ''}">
        <h4 class="card-title">${e(tituloG(g))}</h4>${AM.acomodar ? herramientasG(g, i, lista.length) : ''}<div data-am-g="${i}"></div></div>`).join('')}</div>`;
      box.querySelectorAll('[data-am-g]').forEach((el) => {
        const g = lista[Number(el.dataset.amG)];
        el.innerHTML = dibujarG(g, AM.res, Math.max(280, Math.round(el.clientWidth || 600)));
      });
    }
    const clic = (id, fn) => { if ($(id)) $(id).addEventListener('click', fn); };
    clic('am-g-acomodar', () => { AM.acomodar = true; renderResultado(); });
    clic('am-g-listo', () => { AM.acomodar = false; renderResultado(); });
    clic('am-g-reset', () => { cambiarGraficos(null); });
    clic('am-g-add', () => {
      const tipo = $('am-g-tipo').value;
      cambiarGraficos(listaGraficos().concat([{ tipo, ancho: tipo === 'tarjetas' || tipo === 'diario' || tipo === 'avance_item' ? 'completo' : 'medio', ...(tipo === 'equipos' ? { indicador: 'total_hs' } : {}) }]));
    });
    if (!AM.acomodar) return;
    const copia = () => listaGraficos().map((g) => ({ ...g }));
    box.querySelectorAll('[data-am-g-mov]').forEach((b) => b.addEventListener('click', () => {
      const l = copia();
      const i = Number(b.dataset.amGMov);
      const j = i + Number(b.dataset.d);
      [l[i], l[j]] = [l[j], l[i]];
      cambiarGraficos(l);
    }));
    box.querySelectorAll('[data-am-g-ancho]').forEach((b) => b.addEventListener('click', () => {
      const l = copia();
      const g = l[Number(b.dataset.amGAncho)];
      g.ancho = g.ancho === 'completo' ? 'medio' : 'completo';
      cambiarGraficos(l);
    }));
    box.querySelectorAll('[data-am-g-del]').forEach((b) => b.addEventListener('click', () => {
      const l = copia();
      l.splice(Number(b.dataset.amGDel), 1);
      cambiarGraficos(l);
    }));
    box.querySelectorAll('[data-am-g-ind]').forEach((s) => s.addEventListener('change', () => {
      const l = copia();
      l[Number(s.dataset.amGInd)].indicador = s.value;
      cambiarGraficos(l);
    }));
    box.querySelectorAll('[data-am-g-item]').forEach((s) => s.addEventListener('change', () => {
      const l = copia();
      l[Number(s.dataset.amGItem)].item_oc_cliente_id = s.value ? Number(s.value) : null;
      cambiarGraficos(l);
    }));
  }

  // Un borrador nuevo guarda los gráficos al crearse; un acta ya guardada (borrador o cerrada) los guarda enseguida.
  function cambiarGraficos(lista) {
    const d = AM.ed;
    d.graficos = lista;
    renderKpi();
    if (!d.id) { AM.sucio = true; return; }
    clearTimeout(AM.timerG);
    AM.timerG = setTimeout(() => {
      api(`${API}/actas/${d.id}/graficos`, 'PUT', { graficos: lista })
        .then((r) => { if (lista === null && AM.ed && AM.ed.id === d.id) d.graficos = r.graficos; })
        .catch((err) => toast(`No se pudo guardar la disposición de los gráficos: ${err.message}`, 'error'));
    }, 400);
  }
  let timerResize = null;
  window.addEventListener('resize', () => { clearTimeout(timerResize); timerResize = setTimeout(() => { if ($('am-kpi')) renderKpi(); }, 200); });

  // ---------------------------------------------------------------- acciones
  async function guardar() {
    const d = AM.ed;
    await ejecutar(async () => {
      const a = d.id ? await api(`${API}/actas/${d.id}`, 'PUT', payload()) : await api(`${API}/actas`, 'POST', payload());
      await abrirActa(a.id);
    }, 'Acta guardada.');
  }

  function cerrarActa() {
    const d = AM.ed;
    const sinItem = d.equipos.filter((x) => !x.item_oc_cliente_id).map((x) => x.ci);
    const aviso = sinItem.length ? `\n\nAtención: ${sinItem.join(', ')} no tienen ítem de la OC y no se certifican.` : '';
    if (!confirm(`Al cerrar, el acta guarda el resultado y queda lista para precargar el certificado. Los cambios posteriores en los partes no la afectan.${aviso}\n\n¿Cerrar el acta?`)) return;
    ejecutar(async () => {
      await api(`${API}/actas/${d.id}`, 'PUT', payload());
      await api(`${API}/actas/${d.id}/cerrar`, 'POST');
      await abrirActa(d.id);
    }, 'Acta cerrada. Ya se puede precargar el certificado en Seguimiento de Cliente.');
  }

  function imprimir() {
    const d = AM.ed;
    const res = AM.res;
    const o = AM.opciones.ordenes.find((x) => x.id === Number(d.orden_compra_cliente_id)) || {};
    const s = AM.opciones.seguimientos.find((x) => x.id === Number(d.seguimiento_cc_id)) || {};
    const w = window.open('', '_blank');
    if (!w) { toast('El navegador bloqueó la ventana de impresión.', 'error'); return; }
    AM.imprimiendo = true;
    w.document.write(`<!doctype html><html><head><meta charset="utf-8"><title>Acta de medición N° ${e(d.numero)}</title><style>
      body{font-family:Arial,sans-serif;font-size:11px;color:#111;margin:18px} table{border-collapse:collapse;width:100%;margin-bottom:12px}
      th,td{border:1px solid #888;padding:3px 5px;vertical-align:top;text-align:left} th{background:#dce8f7} .num{text-align:right} .center{text-align:center}
      tr.tot td{background:#eef4fc;font-weight:bold} h1{font-size:17px;margin:0 0 4px} h2{font-size:13px;margin:16px 0 6px} .muted{color:#555;font-weight:normal} .chip{font-size:10px}
      .enc{display:flex;gap:16px;align-items:center;margin-bottom:10px} .firmas{display:flex;justify-content:space-around;margin-top:50px;text-align:center}
      .firmas div{min-width:220px;border-top:1px solid #555;padding-top:6px} @page{size:landscape;margin:12mm} .table-scroll{overflow:visible}
      ${KPI_CSS} .chart-card{break-inside:avoid}</style></head><body>
      <div class="enc"><img src="/Imagenes/09-smg.png" alt="" style="height:46px"><div><h1>ACTA DE MEDICIÓN N° ${e(d.numero)}${d.estado === 'ANULADA' ? ' (ANULADA)' : ''}</h1>
      <div><b>Cliente:</b> ${e(s.cliente || s.cliente_codigo || '')} &nbsp; <b>OC:</b> ${e(ocTxt(o))} &nbsp; <b>Período:</b> ${fFecha(d.periodo_desde)} al ${fFecha(d.periodo_hasta)} &nbsp; <b>Fecha:</b> ${fFecha(d.fecha)}</div>
      ${d.observaciones ? `<div><b>Observaciones:</b> ${e(d.observaciones)}</div>` : ''}</div></div>
      <h2>A certificar por ítem de la OC</h2>${tablaResumen(res)}
      ${listaGraficos().length ? `<h2>Gráficos KPI</h2><div class="chart-grid">${listaGraficos().map((g) => `<div class="chart-card ${g.ancho === 'completo' ? 'is-full' : ''}"><h4>${e(tituloG(g))}</h4>${dibujarG(g, res, g.ancho === 'completo' ? 980 : 480)}</div>`).join('')}</div>` : ''}
      ${res.indicadores && res.indicadores.equipos.length ? `<h2>Indicadores por equipo</h2>${tablaIndicadores(res)}` : ''}
      ${res.equipos.map((eq) => `<h2>${e(eq.ci)} ${e(eq.descripcion)} <span class="muted">${eq.centro_costo ? `· CC ${e(eq.centro_costo)} ` : ''}· ${e(eq.item_oc ? `ítem ${eq.item_oc}` : 'sin ítem de OC')}</span></h2>${tablaEquipo(eq, res.columnas)}`).join('')}
      <div class="firmas"><div><b>Contratista</b></div><div><b>Cliente</b></div></div></body></html>`);
    AM.imprimiendo = false;
    w.document.close();
    w.focus();
    setTimeout(() => w.print(), 400);
  }

  // ================================================================= Entrada
  window.amAbrir = async () => {
    if (!root()) return;
    encabezado();
    try {
      if (!AM.opciones) AM.opciones = await api(`${API}/opciones`);
      if (AM.ed && AM.ed.id) await abrirActa(AM.ed.id); else await vistaLista();
    } catch (err) { $('am-body').innerHTML = `<div class="callout bad">${ic('alert')}<div>${e(err.message)}</div></div>`; }
  };

  // Llamada desde el editor de certificados (Seguimiento de Cliente › Certificados › Nuevo).
  window.amPrecargarCertificado = async () => {
    const ed = typeof certState !== 'undefined' ? certState.ed : null;
    if (!ed) return;
    let lista;
    try { lista = await api(`/operaciones/seguimiento-cliente/${certState.sid}/actas-medicion?oc=${ed.alcance === 'OC' ? (ed.oc || 0) : 0}`); } catch (err) { toast(err.message, 'error'); return; }
    if (!lista.length) { toast('No hay actas de medición cerradas para este seguimiento (cree el acta en Operaciones › Actas de Medición y ciérrela).'); return; }
    modal('Precargar desde acta de medición', `<p class="help">Se cargan las cantidades del período en los ítems de la OC elegidos para cada equipo del acta. Todo queda editable.</p>
      <div class="table-scroll"><table class="tbl tbl-static tbl-auto"><thead><tr><th>OC</th><th>Acta</th><th>Período</th><th>Estado</th><th>Certificado vigente</th><th></th></tr></thead><tbody>
      ${lista.map((a) => `<tr><td>${e(a.contrato)}</td><td >N° ${a.numero}</td><td>${fFecha(a.periodo_desde)} al ${fFecha(a.periodo_hasta)}</td><td>${badge(a.estado)}</td>
        <td>${certTxt(a.certificado_vigente)}${a.coincide_oc ? '' : ' <span class="muted">(otra OC)</span>'}</td>
        <td><button type="button" class="btn btn-sm btn-primary" data-am-pc="${a.acta_id}" ${a.certificado_vigente && a.certificado_vigente.id !== ed.id ? 'disabled title="Ya tiene un certificado vigente"' : ''}>Precargar</button></td></tr>`).join('')}
      </tbody></table></div>`, '<button class="btn" type="button" data-am-cerrar>Cerrar</button>', 'md');
    $('am-modal-b').querySelectorAll('[data-am-pc]').forEach((b) => b.addEventListener('click', async () => {
      try {
        const p = await api(`/operaciones/seguimiento-cliente/${certState.sid}/actas-medicion/${b.dataset.amPc}/precarga`);
        if (ed.modalidad !== 'ITEMS' && !certState.resumen.modalidad_fija) ed.modalidad = 'ITEMS';
        if (ed.modalidad !== 'ITEMS') { toast('El seguimiento certifica en modalidad global: la precarga por ítems no aplica.', 'error'); return; }
        if (p.ordenes.length === 1) {
          const o = certState.resumen.ordenes.find((x) => x.orden_compra_cliente_id === p.ordenes[0]);
          if (o) { ed.alcance = 'OC'; ed.oc = o.orden_compra_cliente_id; ed.moneda = o.moneda; }
        }
        ed.carga = {};
        p.lineas.forEach((l) => { ed.carga[`i${l.item_oc_cliente_id}`] = { valor: String(l.cantidad_periodo), obs: l.observacion.slice(0, 500) }; });
        ed.desde = p.periodo_desde;
        ed.hasta = p.periodo_hasta;
        ed.observaciones = ed.observaciones && !ed.observaciones.includes(p.observaciones) ? `${ed.observaciones}\n${p.observaciones}` : (ed.observaciones || p.observaciones);
        ed.amActaId = p.acta_id;
        cerrarModal();
        certRenderEditor();
        certCalcular();
        const avisos = [];
        if (!p.lineas.length) avisos.push('ningún equipo del acta tiene ítem de la OC');
        if (p.items_sin_vinculo.length) avisos.push(`equipos sin ítem de la OC: ${p.items_sin_vinculo.join(', ')}`);
        toast(`Precargado desde el acta N° ${p.numero}.${avisos.length ? ` Atención: ${avisos.join('; ')}.` : ''}`, avisos.length ? 'warning' : 'success');
      } catch (err) { toast(err.message, 'error'); }
    }));
  };

  window.amVincularCertificado = async (actaId, certificadoId) => {
    try { await api(`/operaciones/seguimiento-cliente/${certState.sid}/actas-medicion/${actaId}/vincular`, 'POST', { certificado_id: certificadoId }); } catch (err) { toast(`El certificado se guardó pero no se pudo vincular al acta: ${err.message}`, 'error'); }
  };
})();
