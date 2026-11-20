/* Operaciones › Actas de Medición.
 * Un acta = OC del cliente + período + equipos (CI) + columnas del parte diario. La cantidad de cada equipo es la suma de
 * una columna de sus partes del período y va al ítem de la OC elegido; desde un acta cerrada se precarga el certificado.
 * Todo cálculo lo hace el servidor (actas_medicion.py). Usa los estilos cert-* del panel de Certificados y showToast / opPuede.
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
  const campo = (etq, html, ancho = '') => `<div class="cert-field" ${ancho ? `style="grid-column:span ${ancho}"` : ''}><label>${e(etq)}</label>${html}</div>`;
  const tarjeta = (titulo, cuerpo, acciones = '', id = '') => `<div class="cert-card" style="margin-bottom:12px" ${id ? `id="${id}"` : ''}>
    <div class="cert-card-h"><h3>${titulo}</h3><span class="cert-actions">${acciones}</span></div><div class="cert-card-b">${cuerpo}</div></div>`;
  const badge = (estado) => `<span class="cert-badge cert-b-${estado === 'ANULADA' ? 'ANULADO' : (estado === 'CERRADA' ? 'EMITIDO' : 'BORRADOR')}">${e(estado)}</span>`;
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

  function encabezado() {
    root().innerHTML = `<div class="seguimiento-header"><div><h2>Actas de Medición</h2>
        <p>Partes diarios de los equipos elegidos en un período, vinculados a los ítems de una orden de compra para certificar.</p></div></div>
      <div class="seguimiento-main"><div id="am-body"></div></div>`;
  }

  // ================================================================= LISTA
  async function vistaLista() {
    AM.ed = null;
    AM.res = null;
    encabezado();
    const body = $('am-body');
    body.innerHTML = '<p class="cert-muted">Cargando…</p>';
    const actas = await api(`${API}/actas${AM.filtroSeg ? `?seguimiento=${AM.filtroSeg}` : ''}`);
    body.innerHTML = tarjeta('Actas', `
      <div class="cert-actions" style="margin-bottom:10px">${campo('Seguimiento de cliente', `<select id="am-f-seg" style="min-width:320px">${opts([['', 'Todos']].concat(AM.opciones.seguimientos.map((s) => [s.id, segTxt(s)])), AM.filtroSeg)}</select>`)}</div>
      <div class="cert-wrap"><table class="cert-t"><thead><tr><th>N°</th><th>Cliente</th><th>OC</th><th>Período</th><th>Equipos</th><th>Estado</th><th>Certificado</th><th></th></tr></thead><tbody>
      ${actas.map((a) => `<tr><td class="c">${a.numero}</td><td>${e(a.cliente || '')}</td><td>${e(a.orden_compra || `OC ${a.oc_numero}`)}</td>
        <td>${fFecha(a.periodo_desde)} al ${fFecha(a.periodo_hasta)}</td><td class="c">${a.equipos}</td><td>${badge(a.estado)}</td><td>${certTxt(a.certificado)}</td>
        <td class="c"><button type="button" class="cert-btn sm" data-am-abrir="${a.id}">Abrir</button></td></tr>`).join('')
      || '<tr><td colspan="8" class="cert-muted">Todavía no hay actas. Use «+ Nueva acta».</td></tr>'}
      </tbody></table></div>`, puede('generar') ? '<button type="button" class="cert-btn primary" id="am-nueva">+ Nueva acta</button>' : '');
    $('am-f-seg').addEventListener('change', (ev) => { AM.filtroSeg = ev.target.value; vistaLista().catch((err) => toast(err.message, 'error')); });
    body.querySelectorAll('[data-am-abrir]').forEach((b) => b.addEventListener('click', () => abrirActa(Number(b.dataset.amAbrir)).catch((err) => toast(err.message, 'error'))));
    if ($('am-nueva')) $('am-nueva').addEventListener('click', nuevaActa);
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
    const acciones = [
      '<button type="button" class="cert-btn" id="am-volver">← Actas</button>',
      editable() ? `<button type="button" class="cert-btn primary" id="am-guardar">${nuevo ? 'Crear acta' : 'Guardar'}</button>` : '',
      d.id && d.estado === 'BORRADOR' && puede('aprobar') ? '<button type="button" class="cert-btn primary" id="am-cerrar" title="Guarda el resultado y habilita la precarga del certificado">Cerrar acta</button>' : '',
      d.id && d.estado === 'CERRADA' && puede('aprobar') && !vigente ? '<button type="button" class="cert-btn" id="am-reabrir">Reabrir</button>' : '',
      d.id && d.estado !== 'ANULADA' && puede('anular') && !vigente ? '<button type="button" class="cert-btn danger" id="am-anular">Anular</button>' : '',
      d.id && d.estado === 'BORRADOR' && puede('generar') ? '<button type="button" class="cert-btn danger" id="am-eliminar">Eliminar</button>' : '',
      d.id ? `<a class="cert-btn" href="${API}/actas/${d.id}/excel">Excel</a>` : '',
      d.id ? '<button type="button" class="cert-btn" id="am-imprimir">Imprimir</button>' : '',
    ].join('');
    let estadoTxt = '';
    if (d.estado === 'CERRADA') estadoTxt = `Cerrada por ${e(d.cerrada_por)} el ${e(d.cerrada_en)}: el resultado quedó guardado y no cambia aunque cambien los partes.`;
    else if (d.estado === 'ANULADA') estadoTxt = `Anulada por ${e(d.anulada_por)}: ${e(d.motivo_anulacion)}`;
    else estadoTxt = 'Borrador: se calcula en vivo desde los partes diarios.';

    const datos = `<div class="cert-actions" style="margin-bottom:10px">${badge(d.estado)} <span class="cert-muted">${estadoTxt}</span>
        ${(d.certificados || []).length ? `<span class="cert-muted">· Certificado: ${(d.certificados || []).map(certTxt).join(', ')}</span>` : ''}</div>
      <div class="cert-grid" style="grid-template-columns:repeat(6,minmax(0,1fr))">
        ${campo('Seguimiento de cliente', `<select id="am-seg" ${ro || (d.id ? 'disabled' : '')}>${opts([['', '— elegir —']].concat(AM.opciones.seguimientos.map((s) => [s.id, segTxt(s)])), d.seguimiento_cc_id)}</select>`, 2)}
        ${campo('Orden de compra', `<select id="am-oc" ${ro || (d.id ? 'disabled' : '')}>${opts([['', ocs.length ? '— elegir —' : 'sin OC en el seguimiento']].concat(ocs.map((o) => [o.id, ocTxt(o)])), d.orden_compra_cliente_id)}</select>`, 2)}
        ${campo('N° de acta', `<input id="am-num" type="number" min="1" value="${e(d.numero)}" ${ro}>`)}
        ${campo('Fecha', `<input id="am-fecha" type="date" value="${e(d.fecha)}" ${ro}>`)}
        ${campo('Período desde', `<input id="am-desde" type="date" value="${e(d.periodo_desde)}" ${ro}>`)}
        ${campo('Período hasta', `<input id="am-hasta" type="date" value="${e(d.periodo_hasta)}" ${ro}>`)}
        ${campo('Observaciones', `<input id="am-obs" value="${e(d.observaciones)}" ${ro}>`, 4)}</div>`;

    const listo = d.orden_compra_cliente_id && d.periodo_desde && d.periodo_hasta;
    $('am-body').innerHTML = `
      ${tarjeta(nuevo ? 'Nueva acta de medición' : `Acta N° ${e(d.numero)}`, datos, acciones)}
      ${listo ? tarjeta('Equipos', '<div id="am-equipos"></div>', editable() ? `<button type="button" class="cert-btn sm primary" id="am-eq-partes">+ Equipos con partes en el período</button>` : '') : ''}
      ${listo ? tarjeta('Mínimo y objetivo por ítem', '<div id="am-metas"></div>', '<span class="cert-muted">Referencias de gestión: no cambian lo que se certifica</span>') : ''}
      ${listo ? tarjeta('Columnas del parte diario', '<div id="am-columnas"></div>') : ''}
      ${listo ? '<div id="am-res"></div>' : '<p class="cert-muted">Elija el seguimiento, la orden de compra y el período para continuar.</p>'}`;

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
    const opItems = [['', '— sin ítem (no se certifica) —']].concat(items.map((i) => [i.id, `${i.numero} · ${i.descripcion || ''}${i.unidad ? ` (${i.unidad})` : ''}`]));
    const opCant = [['', `(${etiqueta(d.campo_cantidad)})`]].concat(numericos().map((c) => [c.campo, c.etiqueta]));
    const filas = d.equipos.map((x, i) => {
      const r = porCi[String(x.ci).toUpperCase()];
      return `<tr><td><b>${e(x.ci)}</b></td><td>${e(descEquipo(x.ci))}</td>
        <td><select data-am-eq-item="${i}" ${ro} style="min-width:260px">${opts(opItems, x.item_oc_cliente_id)}</select></td>
        <td><select data-am-eq-campo="${i}" ${ro}>${opts(opCant, x.campo_cantidad || '')}</select></td>
        <td class="c">${r ? r.partes.length : ''}</td><td class="r"><b>${r ? nf(r.total) : ''}</b></td>
        <td class="c">${ro ? '' : `<button type="button" class="cert-btn sm danger" data-am-eq-del="${i}" title="Quitar">×</button>`}</td></tr>`;
    }).join('');
    $('am-equipos').innerHTML = `${items.length ? '' : '<div class="cert-alert warn" style="margin-bottom:8px"><span class="ic">!</span><div>La OC no tiene ítems cargados: cárguelos en Seguimiento de Cliente para poder certificar.</div></div>'}
      <div class="cert-wrap"><table class="cert-t"><thead><tr><th>CI</th><th>Equipo</th><th>Ítem de la OC donde se certifica</th><th>Cantidad = suma de</th><th>Partes</th><th>Cantidad</th><th></th></tr></thead>
      <tbody>${filas || '<tr><td colspan="7" class="cert-muted">Sin equipos. Agréguelos con «+ Equipos con partes en el período» o escribiendo el CI.</td></tr>'}</tbody></table></div>
      ${ro ? '' : `<div class="cert-actions" style="margin-top:8px"><input id="am-eq-ci" list="am-dl-eq" placeholder="CI del equipo (ej. M-25)" autocomplete="off" style="width:220px">
        <button type="button" class="cert-btn sm" id="am-eq-add">Agregar</button>
        <datalist id="am-dl-eq">${AM.opciones.equipos.map((x) => `<option value="${e(x.codigo)}">${e(x.descripcion)}</option>`).join('')}</datalist></div>`}`;
    const box = $('am-equipos');
    box.querySelectorAll('[data-am-eq-item]').forEach((s) => s.addEventListener('change', () => { d.equipos[Number(s.dataset.amEqItem)].item_oc_cliente_id = s.value ? Number(s.value) : null; AM.sucio = true; recalcular(); }));
    box.querySelectorAll('[data-am-eq-campo]').forEach((s) => s.addEventListener('change', () => { d.equipos[Number(s.dataset.amEqCampo)].campo_cantidad = s.value || null; AM.sucio = true; recalcular(); }));
    box.querySelectorAll('[data-am-eq-del]').forEach((b) => b.addEventListener('click', () => { d.equipos.splice(Number(b.dataset.amEqDel), 1); AM.sucio = true; renderEquipos(); recalcular(); }));
    const agregar = () => {
      const ci = $('am-eq-ci').value.trim().toUpperCase();
      if (!ci) return;
      if (d.equipos.some((x) => String(x.ci).toUpperCase() === ci)) { toast(`${ci} ya está en el acta.`); return; }
      d.equipos.push({ ci, item_oc_cliente_id: null, campo_cantidad: null });
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
    modal(`Equipos con partes del ${fFecha(d.periodo_desde)} al ${fFecha(d.periodo_hasta)}`, lista.length ? `
      <div class="cert-actions" style="margin-bottom:8px"><input id="am-eq-buscar" placeholder="Buscar CI, equipo o cliente…" style="width:300px">
        <span class="cert-muted">${lista.length} equipos con partes</span></div>
      <div class="cert-wrap" style="max-height:55vh;overflow:auto"><table class="cert-t"><thead><tr><th></th><th>CI</th><th>Equipo</th><th>Cliente en los partes</th><th>Partes</th><th>Total hs</th></tr></thead><tbody>
      ${lista.map((x) => `<tr data-am-buscar="${e(`${x.ci} ${x.descripcion} ${x.clientes.join(' ')}`.toLowerCase())}"><td class="c"><input type="checkbox" data-am-ci="${e(x.ci)}" ${ya.has(x.ci) ? 'checked disabled' : ''}></td>
        <td><b>${e(x.ci)}</b></td><td>${e(x.descripcion)}</td><td>${e(x.clientes.join(', '))}</td><td class="c">${x.partes}</td><td class="r">${nf(x.total_hs)}</td></tr>`).join('')}
      </tbody></table></div>` : '<p class="cert-muted">No hay partes cargados en ese período.</p>',
    `<button class="cert-btn" type="button" data-am-cerrar>Cancelar</button>${lista.length ? '<button class="cert-btn primary" type="button" id="am-eq-ok">Agregar seleccionados</button>' : ''}`, 'md');
    if (!lista.length) return;
    $('am-eq-buscar').addEventListener('input', (ev) => {
      const q = ev.target.value.trim().toLowerCase();
      $('am-modal-b').querySelectorAll('[data-am-buscar]').forEach((tr) => { tr.style.display = !q || tr.dataset.amBuscar.includes(q) ? '' : 'none'; });
    });
    $('am-eq-ok').addEventListener('click', () => {
      const nuevos = [...$('am-modal-b').querySelectorAll('[data-am-ci]:checked:not(:disabled)')].map((c) => c.dataset.amCi);
      nuevos.forEach((ci) => d.equipos.push({ ci, item_oc_cliente_id: null, campo_cantidad: null }));
      cerrarModal();
      if (nuevos.length) { AM.sucio = true; renderEquipos(); recalcular(); toast(`${nuevos.length} equipo(s) agregados. Elija el ítem de la OC de cada uno.`); }
    });
  }

  // ---------------------------------------------------------------- columnas
  function renderColumnas() {
    const d = AM.ed;
    const ro = editable() ? '' : 'disabled';
    const elegidas = new Set(d.columnas);
    $('am-columnas').innerHTML = `<div class="cert-grid" style="grid-template-columns:repeat(3,minmax(0,1fr));margin-bottom:10px">
        ${campo('Cantidad a certificar = suma de la columna', `<select id="am-campo" ${ro}>${opts(numericos().map((c) => [c.campo, c.etiqueta]), d.campo_cantidad)}</select>`)}
        <div class="cert-field" style="grid-column:span 2"><label>&nbsp;</label><span class="cert-muted">Cada equipo puede usar otra columna (columna «Cantidad = suma de» en Equipos).</span></div></div>
      <div class="cert-field"><label>Columnas que se muestran en el detalle de partes</label>
      <div style="display:flex;flex-wrap:wrap;gap:6px 14px;margin-top:4px">${campos().map((c) => `<label style="display:inline-flex;align-items:center;gap:5px;font-weight:400;white-space:nowrap">
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
      equipos: d.equipos.map((x) => ({ ci: x.ci, item_oc_cliente_id: x.item_oc_cliente_id || null, campo_cantidad: x.campo_cantidad || null })),
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
    return `<div class="cert-wrap"><table class="cert-t"><thead><tr><th>Ítem OC</th><th>Descripción</th><th>Unidad</th><th>Equipos</th><th>Cantidad</th><th>Precio</th><th>Importe</th>${conMeta ? '<th>Mín. / Obj.</th><th>Cumplimiento</th>' : ''}</tr></thead><tbody>
      ${res.items_oc.map((i) => `<tr><td class="c">${e(i.numero)}</td><td>${e(i.descripcion)}</td><td class="c">${e(i.unidad || '')}</td><td>${e(i.equipos.join(', '))}</td>
        <td class="r"><b>${nf(i.cantidad_periodo)}</b></td><td class="r">${money(i.precio)}</td><td class="r">${money(i.importe)}</td>
        ${conMeta ? `<td class="r">${i.minimo == null ? '—' : nf(i.minimo)} / ${i.objetivo == null ? '—' : nf(i.objetivo)}</td><td>${chipMeta(i)}</td>` : ''}</tr>`).join('')
      || `<tr><td colspan="${n}" class="cert-muted">Ningún equipo tiene ítem de la OC asignado.</td></tr>`}</tbody>
      ${res.items_oc.length ? `<tfoot><tr class="tot"><td colspan="6">Total</td><td class="r">${money(res.total_importe)}</td>${conMeta ? '<td colspan="2"></td>' : ''}</tr></tfoot>` : ''}</table></div>`;
  }

  function tablaIndicadores(res) {
    const ind = res.indicadores;
    if (!ind || !ind.equipos.length) return '';
    const cols = ind.columnas;
    const celdas = (f, general) => `<td class="r">${general ? '' : f.dias}</td><td class="r">${general ? '' : f.partes}</td>${cols.map((c) => `<td class="r">${e(nf(f.valores[c.campo]))}</td>`).join('')}<td class="r">${f.consumo_lh == null ? '—' : e(nf(f.consumo_lh))}</td>`;
    return `<div class="cert-wrap"><table class="cert-t"><thead><tr><th>Equipo</th><th>Días</th><th>Partes</th>${cols.map((c) => `<th>${e(c.etiqueta)}</th>`).join('')}<th>Consumo (L/h)</th></tr></thead><tbody>
      ${ind.equipos.map((f) => `<tr><td><b>${e(f.ci)}</b> <span class="cert-muted">${e(f.descripcion)}</span></td>${celdas(f)}</tr>`).join('')}</tbody>
      <tfoot><tr class="tot"><td>General</td>${celdas(ind.general, true)}</tr></tfoot></table></div>`;
  }

  function tablaEquipo(eq, cols) {
    const celda = (c, v) => {
      if (c.campo.startsWith('fecha')) return `<td>${e(fFecha(String(v ?? '').slice(0, 10)))}</td>`;
      return c.numerico ? `<td class="r">${e(nf(v))}</td>` : `<td>${e(v ?? '')}</td>`;
    };
    return `<div class="cert-wrap"><table class="cert-t ajustar"><thead><tr>${cols.map((c) => `<th>${e(c.etiqueta)}</th>`).join('')}<th>Cantidad<br><span class="cert-muted">${e(eq.etiqueta_cantidad)}</span></th></tr></thead><tbody>
      ${eq.partes.map((p) => `<tr>${cols.map((c) => celda(c, p.valores[c.campo])).join('')}<td class="r"><b>${nf(p.cantidad)}</b></td></tr>`).join('')
      || `<tr><td colspan="${cols.length + 1}" class="cert-muted">Sin partes en el período.</td></tr>`}</tbody>
      ${eq.partes.length ? `<tfoot><tr class="tot">${cols.map((c, i) => `<td class="r">${i === 0 ? 'TOTAL' : (c.campo in eq.totales ? e(nf(eq.totales[c.campo])) : '')}</td>`).join('')}<td class="r">${nf(eq.total)}</td></tr></tfoot>` : ''}
      </table></div>`;
  }

  function renderResultado() {
    const res = AM.res;
    if (!$('am-res')) return;
    if (!res) { $('am-res').innerHTML = ''; return; }
    const avisos = res.avisos.length ? `<div class="cert-alert warn" style="margin-bottom:10px"><span class="ic">!</span><div>${res.avisos.map(e).join('<br>')}</div></div>` : '';
    const detalle = res.equipos.map((eq) => `<details style="margin-bottom:8px" ${res.equipos.length <= 2 ? 'open' : ''}>
        <summary style="cursor:pointer;padding:6px 0"><b>${e(eq.ci)}</b> ${e(eq.descripcion)} · <span class="cert-muted">${e(eq.item_oc ? `ítem ${eq.item_oc}` : 'sin ítem de OC')}</span>
        · ${eq.partes.length} partes · <b>${nf(eq.total)}</b> ${e(eq.etiqueta_cantidad)}</summary>${tablaEquipo(eq, res.columnas)}</details>`).join('');
    const puedeAcomodar = AM.ed.estado !== 'ANULADA' && puede('generar');
    const accKpi = AM.acomodar
      ? `<select id="am-g-tipo" style="font-size:12px">${opts(Object.entries(TIPOS_G), 'items_meta')}</select><button type="button" class="cert-btn sm" id="am-g-add">+ Agregar</button>
         <button type="button" class="cert-btn sm" id="am-g-reset" title="Vuelve a los gráficos de siempre">Restablecer</button><button type="button" class="cert-btn sm primary" id="am-g-listo">Listo</button>`
      : (puedeAcomodar ? '<button type="button" class="cert-btn sm" id="am-g-acomodar">Acomodar gráficos</button>' : '');
    $('am-res').innerHTML = `${tarjeta('A certificar por ítem de la OC', avisos + tablaResumen(res), `<span class="cert-muted">${res.total_partes} partes${AM.sucio ? ' · cambios sin guardar' : ''}</span>`)}
      ${tarjeta('Gráficos KPI del acta', '<div id="am-kpi"></div>', accKpi)}
      ${res.indicadores && res.indicadores.equipos.length ? tarjeta('Indicadores por equipo', tablaIndicadores(res), '<span class="cert-muted">Consumo = combustible / horas efectivas</span>') : ''}
      ${tarjeta('Detalle de partes por equipo', detalle || '<p class="cert-muted">Agregue equipos para ver sus partes.</p>')}`;
    renderKpi();
  }


  // ================================================================= MÍNIMO Y OBJETIVO POR ÍTEM
  const ESTADOS_META = {
    BAJO_MINIMO: ['ANULADO', '▼', 'Bajo el mínimo'], BAJO_OBJETIVO: ['BORRADOR', '●', 'Bajo el objetivo'],
    CUMPLE_MINIMO: ['EMITIDO', '✓', 'Cumple el mínimo'], OBJETIVO: ['EMITIDO', '★', 'Alcanzó el objetivo'],
  };
  const chipMeta = (i) => {
    const x = i && ESTADOS_META[i.estado_meta];
    return x ? `<span class="cert-badge cert-b-${x[0]}" title="${e(x[2])}">${x[1]} ${e(x[2])}${i.pct_objetivo != null ? ` · ${nf(i.pct_objetivo, 1)}%` : ''}</span>` : '<span class="cert-muted">sin meta</span>';
  };
  const metaDe = (itemId) => (AM.ed.metas || []).find((m) => m.item_oc_cliente_id === itemId);
  const lineaRes = (itemId) => ((AM.res && AM.res.items_oc) || []).find((x) => x.item_oc_cliente_id === itemId);

  function renderMetas() {
    const d = AM.ed;
    const box = $('am-metas');
    if (!box) return;
    const ro = editable() ? '' : 'disabled';
    const items = itemsDe(d.orden_compra_cliente_id);
    if (!items.length) { box.innerHTML = '<p class="cert-muted">La OC no tiene ítems cargados.</p>'; return; }
    // En un acta cerrada se muestran los valores con que se cerró (quedaron en el resultado).
    const valor = (it, k) => {
      const r = d.estado !== 'BORRADOR' ? lineaRes(it.id) : null;
      const v = r && r[k] !== undefined ? r[k] : (metaDe(it.id) || {})[k];
      return v == null ? '' : v;
    };
    box.innerHTML = `<div class="cert-wrap"><table class="cert-t"><thead><tr><th>Ítem OC</th><th>Descripción</th><th>Unidad</th><th>Cantidad del período</th>
      <th>Mínimo</th><th>Objetivo</th><th style="min-width:180px">Avance</th><th>Cumplimiento</th></tr></thead><tbody>
      ${items.map((it) => `<tr><td class="c">${e(it.numero)}</td><td>${e(it.descripcion || '')}</td><td class="c">${e(it.unidad || '')}</td>
        <td class="r"><b data-am-mt-cant="${it.id}"></b></td>
        <td><input type="number" min="0" step="any" style="width:100px" data-am-mt="${it.id}" data-k="minimo" value="${e(valor(it, 'minimo'))}" ${ro}></td>
        <td><input type="number" min="0" step="any" style="width:100px" data-am-mt="${it.id}" data-k="objetivo" value="${e(valor(it, 'objetivo'))}" ${ro}></td>
        <td data-am-mt-barra="${it.id}"></td><td data-am-mt-chip="${it.id}"></td></tr>`).join('')}
      </tbody></table></div>
      <p class="cert-muted" style="margin:6px 0 0">Deje vacío lo que no aplique. El estado compara la cantidad del período del ítem (suma de sus equipos) contra el mínimo y el objetivo.</p>`;
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
      if (chip) chip.innerHTML = r ? chipMeta(r) : '<span class="cert-muted">sin equipos</span>';
      const barra = box.querySelector(`[data-am-mt-barra="${it.id}"]`);
      if (barra) barra.innerHTML = r && (r.objetivo || r.minimo) ? barraMeta(r) : '';
    });
  }

  function barraMeta(r) {
    const tope = Math.max(r.cantidad_periodo || 0, r.objetivo || 0, r.minimo || 0) * 1.05 || 1;
    const pct = (v) => `${Math.min(100, (v / tope) * 100).toFixed(1)}%`;
    return `<div style="position:relative;height:12px;background:#e6edf5;border-radius:3px" title="Cantidad ${nf(r.cantidad_periodo)} · mínimo ${r.minimo == null ? '—' : nf(r.minimo)} · objetivo ${r.objetivo == null ? '—' : nf(r.objetivo)}">
      <i style="position:absolute;left:0;top:0;bottom:0;width:${pct(r.cantidad_periodo || 0)};background:${COL.barra};border-radius:3px"></i>
      ${r.minimo != null ? `<i style="position:absolute;top:-3px;bottom:-3px;left:${pct(r.minimo)};border-left:2px dashed ${COL.min}"></i>` : ''}
      ${r.objetivo != null ? `<i style="position:absolute;top:-3px;bottom:-3px;left:${pct(r.objetivo)};border-left:2px solid ${COL.obj}"></i>` : ''}</div>`;
  }

  // ================================================================= GRÁFICOS KPI
  // Cada acta guarda qué gráficos ve y en qué orden (null = los de siempre). Se dibujan en SVG, sin librerías.
  const COL = { barra: '#1565c0', min: '#b91c1c', obj: '#15803d', ritmo: '#8a99ad', grid: '#e3e9f1', eje: '#5b6b80', texto: '#0f2440' };
  const TIPOS_G = { tarjetas: 'Tarjetas de resumen', items_meta: 'Cantidad por ítem vs. mínimo y objetivo', avance_item: 'Avance acumulado del período',
    equipos: 'Indicador por equipo', diario: 'Cantidad diaria' };
  const IND_G = [['cantidad', 'Cantidad a certificar'], ['total_hs', 'Horas efectivas'], ['gasoil', 'Combustible (L)'], ['consumo_lh', 'Consumo (L/h)'],
    ['dias', 'Días con partes'], ['partes', 'Partes'], ['aceite', 'Aceite (L)'], ['km_total', 'Km recorridos'], ['viajes', 'Viajes'],
    ['hs_disposicion', 'Hs a disposición'], ['hs_mantenimiento', 'Hs mantenimiento'], ['hs_reparaciones', 'Hs reparaciones']];
  let GRAFICOS_DEFECTO = [{ tipo: 'tarjetas', ancho: 'completo' }, { tipo: 'items_meta', ancho: 'medio' },
    { tipo: 'equipos', indicador: 'total_hs', ancho: 'medio' }, { tipo: 'diario', ancho: 'completo' }];
  const KPI_CSS = `.am-kpi-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
    .am-kpi-g{border:1px solid var(--c-line);border-radius:8px;padding:10px 12px;background:var(--c-card);min-width:0}
    .am-kpi-g.completo{grid-column:1/-1} .am-kpi-g h4{margin:0 0 8px;font-size:13px;color:var(--c-text)}
    .am-kpi-g.mover{outline:2px dashed #93b4dc;outline-offset:2px}
    .am-kpi-tools{display:flex;gap:4px;flex-wrap:wrap;align-items:center;margin:-2px 0 8px} .am-kpi-tools select{max-width:220px}
    .am-kpi-g svg{display:block;width:100%;height:auto;overflow:visible} .am-kpi-g svg .hit:hover{fill:rgba(21,101,192,.08)}
    .am-ley{display:flex;gap:14px;flex-wrap:wrap;font-size:11px;color:var(--c-muted);margin-top:6px}
    .am-ley span{display:inline-flex;align-items:center;gap:5px} .am-ley i{display:inline-block;width:14px;height:0;border-top:3px solid}
    .am-tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px}
    .am-tile{border:1px solid var(--c-line);border-radius:8px;padding:8px 10px} .am-tile .v{font-size:20px;font-weight:800;color:var(--c-text);font-variant-numeric:tabular-nums}
    .am-tile .l{font-size:11px;color:var(--c-muted)} .am-tile.mal{border-color:#f0b4b4;background:#fdf3f3} .am-tile.bien{border-color:#a9dcb9;background:#f1faf4}
    @media (max-width:900px){.am-kpi-grid{grid-template-columns:1fr}}`;
  if (!document.getElementById('am-kpi-css')) {
    const st = document.createElement('style');
    st.id = 'am-kpi-css';
    st.textContent = KPI_CSS;
    document.head.appendChild(st);
  }

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
  const vacioG = (t) => `<p class="cert-muted" style="padding:18px 4px;text-align:center">${e(t)}</p>`;
  const ley = (lista) => `<div class="am-ley">${lista.map(([c, t, dash]) => `<span><i style="border-top-color:${c};${dash ? 'border-top-style:dashed' : ''}"></i>${e(t)}</span>`).join('')}</div>`;
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
    s += `<circle cx="${x(hasta)}" cy="${m.y(acum)}" r="4" fill="${COL.barra}" stroke="#fff" stroke-width="2"/>${txt(x(hasta) - 8, m.y(acum) - 8, nf(acum), { anchor: 'end', peso: 700, color: COL.texto })}`;
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
    return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${e(tituloG(g))}">${s}</svg><div class="am-ley"><span>${dias} días con cantidad · promedio ${nf(serie.reduce((a, x) => a + x.valor, 0) / (dias || 1))} por día trabajado</span></div>`;
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
      ...(!hayMin && !hayObj ? [['#c3d0e0', 'Cargue mínimo y objetivo por ítem para ver las marcas']] : [])]);
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
    const t = (l, v, cls = '', sub = '') => `<div class="am-tile ${cls}"><div class="l">${e(l)}</div><div class="v">${v}</div>${sub ? `<div class="l">${sub}</div>` : ''}</div>`;
    return `<div class="am-tiles">
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
      ? `<select style="font-size:12px" data-am-g-ind="${i}">${opts(IND_G, g.indicador || 'total_hs')}</select>`
      : (g.tipo === 'avance_item' || g.tipo === 'diario' ? `<select style="font-size:12px" data-am-g-item="${i}">${opts([['', 'Todos los ítems']].concat(itemsRes), g.item_oc_cliente_id || '')}</select>` : '');
    return `<div class="am-kpi-tools">
      <button type="button" class="cert-btn sm" data-am-g-mov="${i}" data-d="-1" ${i === 0 ? 'disabled' : ''} title="Subir">↑</button>
      <button type="button" class="cert-btn sm" data-am-g-mov="${i}" data-d="1" ${i === total - 1 ? 'disabled' : ''} title="Bajar">↓</button>
      <button type="button" class="cert-btn sm" data-am-g-ancho="${i}">${g.ancho === 'completo' ? '⇤ Medio ancho' : '⇥ Ancho completo'}</button>
      ${extra}<button type="button" class="cert-btn sm danger" data-am-g-del="${i}" title="Quitar">×</button></div>`;
  }

  function renderKpi() {
    const box = $('am-kpi');
    if (!box || !AM.res) return;
    const lista = listaGraficos();
    if (!lista.length) {
      box.innerHTML = `<p class="cert-muted">Sin gráficos. ${AM.acomodar ? 'Agregue uno con «+ Agregar».' : 'Use «Acomodar gráficos» para elegirlos.'}</p>`;
    } else {
      box.innerHTML = `<div class="am-kpi-grid">${lista.map((g, i) => `<div class="am-kpi-g ${g.ancho === 'completo' ? 'completo' : ''} ${AM.acomodar ? 'mover' : ''}">
        <h4>${e(tituloG(g))}</h4>${AM.acomodar ? herramientasG(g, i, lista.length) : ''}<div data-am-g="${i}"></div></div>`).join('')}</div>`;
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
    w.document.write(`<!doctype html><html><head><meta charset="utf-8"><title>Acta de medición N° ${e(d.numero)}</title><style>
      body{font-family:Arial,sans-serif;font-size:11px;color:#111;margin:18px} table{border-collapse:collapse;width:100%;margin-bottom:12px}
      th,td{border:1px solid #888;padding:3px 5px;vertical-align:top} th{background:#dce8f7} td.r{text-align:right} td.c{text-align:center}
      tr.tot td{background:#eef4fc;font-weight:bold} h1{font-size:17px;margin:0 0 4px} h2{font-size:13px;margin:16px 0 6px} .cert-muted{color:#555;font-weight:normal}
      .enc{display:flex;gap:16px;align-items:center;margin-bottom:10px} .firmas{display:flex;justify-content:space-around;margin-top:50px;text-align:center}
      .firmas div{min-width:220px;border-top:1px solid #555;padding-top:6px} @page{size:landscape;margin:12mm} .cert-wrap{overflow:visible}
      :root{--c-line:#c3d0e0;--c-card:#fff;--c-text:#111;--c-muted:#555} ${KPI_CSS} .am-kpi-g{break-inside:avoid}</style></head><body>
      <div class="enc"><img src="/Imagenes/09-smg.png" alt="" style="height:46px"><div><h1>ACTA DE MEDICIÓN N° ${e(d.numero)}${d.estado === 'ANULADA' ? ' (ANULADA)' : ''}</h1>
      <div><b>Cliente:</b> ${e(s.cliente || s.cliente_codigo || '')} &nbsp; <b>OC:</b> ${e(ocTxt(o))} &nbsp; <b>Período:</b> ${fFecha(d.periodo_desde)} al ${fFecha(d.periodo_hasta)} &nbsp; <b>Fecha:</b> ${fFecha(d.fecha)}</div>
      ${d.observaciones ? `<div><b>Observaciones:</b> ${e(d.observaciones)}</div>` : ''}</div></div>
      <h2>A certificar por ítem de la OC</h2>${tablaResumen(res)}
      ${listaGraficos().length ? `<h2>Gráficos KPI</h2><div class="am-kpi-grid">${listaGraficos().map((g) => `<div class="am-kpi-g ${g.ancho === 'completo' ? 'completo' : ''}"><h4>${e(tituloG(g))}</h4>${dibujarG(g, res, g.ancho === 'completo' ? 980 : 480)}</div>`).join('')}</div>` : ''}
      ${res.indicadores && res.indicadores.equipos.length ? `<h2>Indicadores por equipo</h2>${tablaIndicadores(res)}` : ''}
      ${res.equipos.map((eq) => `<h2>${e(eq.ci)} ${e(eq.descripcion)} <span class="cert-muted">· ${e(eq.item_oc ? `ítem ${eq.item_oc}` : 'sin ítem de OC')}</span></h2>${tablaEquipo(eq, res.columnas)}`).join('')}
      <div class="firmas"><div><b>Contratista</b></div><div><b>Cliente</b></div></div></body></html>`);
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
    } catch (err) { $('am-body').innerHTML = `<div class="cert-alert err"><span class="ic">!</span><div>${e(err.message)}</div></div>`; }
  };

  // Llamada desde el editor de certificados (Seguimiento de Cliente › Certificados › Nuevo).
  window.amPrecargarCertificado = async () => {
    const ed = typeof certState !== 'undefined' ? certState.ed : null;
    if (!ed) return;
    let lista;
    try { lista = await api(`/operaciones/seguimiento-cliente/${certState.sid}/actas-medicion?oc=${ed.alcance === 'OC' ? (ed.oc || 0) : 0}`); } catch (err) { toast(err.message, 'error'); return; }
    if (!lista.length) { toast('No hay actas de medición cerradas para este seguimiento (cree el acta en Operaciones › Actas de Medición y ciérrela).'); return; }
    modal('Precargar desde acta de medición', `<p class="cert-muted">Se cargan las cantidades del período en los ítems de la OC elegidos para cada equipo del acta. Todo queda editable.</p>
      <div class="cert-wrap"><table class="cert-t"><thead><tr><th>OC</th><th>Acta</th><th>Período</th><th>Estado</th><th>Certificado vigente</th><th></th></tr></thead><tbody>
      ${lista.map((a) => `<tr><td>${e(a.contrato)}</td><td class="c">N° ${a.numero}</td><td>${fFecha(a.periodo_desde)} al ${fFecha(a.periodo_hasta)}</td><td>${badge(a.estado)}</td>
        <td>${certTxt(a.certificado_vigente)}${a.coincide_oc ? '' : ' <span class="cert-muted">(otra OC)</span>'}</td>
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
