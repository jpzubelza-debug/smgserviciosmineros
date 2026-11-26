/* Límite de filas visibles en los cuadros con tabla de Operaciones (#op-content).
   Agrega debajo de cada .table-wrap (salvo [data-sin-limite]) una barra "Mostrando X de Y · Filas: [n]".
   Oculta con .row-limit-hide las filas que pasan el límite, sin borrarlas del DOM, y respeta
   los filtros existentes: solo cuenta las filas que no están ya ocultas (style.display o hidden). */
(function () {
  const OPCIONES = [10, 30, 50, 100, 0]; // 0 = Todas
  const POR_DEFECTO = 30;
  const MINIMO_BARRA = 10; // con esta cantidad de filas o menos no se muestra la barra
  const CLASE_OCULTA = 'row-limit-hide';
  const CLASE_BARRA = 'tabla-filas-bar';

  const raiz = document.getElementById('op-content');
  if (!raiz) return;

  function filasDe(wrap) {
    const filas = [];
    wrap.querySelectorAll('table').forEach((tabla) => {
      if (tabla.parentElement && tabla.parentElement.closest('table')) return; // tablas anidadas
      Array.from(tabla.tBodies).forEach((tb) => filas.push(...tb.rows));
    });
    return filas;
  }

  function ocultaPorFiltro(fila) {
    return fila.hidden || fila.style.display === 'none';
  }

  function crearBarra(wrap) {
    const barra = document.createElement('div');
    barra.className = CLASE_BARRA;
    barra.hidden = true;
    const texto = document.createElement('span');
    texto.className = 'tabla-filas-texto';
    const label = document.createElement('label');
    label.append('Filas:');
    const select = document.createElement('select');
    OPCIONES.forEach((n) => {
      const op = document.createElement('option');
      op.value = String(n);
      op.textContent = n === 0 ? 'Todas' : String(n);
      select.appendChild(op);
    });
    select.value = String(POR_DEFECTO);
    label.appendChild(select);
    barra.append(texto, label);
    select.addEventListener('change', () => {
      aplicar(wrap, barra);
      wrap.scrollTop = 0;
    });
    wrap.insertAdjacentElement('afterend', barra);
    return barra;
  }

  function barraDe(wrap) {
    const sig = wrap.nextElementSibling;
    if (sig && sig.classList.contains(CLASE_BARRA)) return sig;
    return crearBarra(wrap);
  }

  function aplicar(wrap, barra) {
    const limite = Number(barra.querySelector('select').value) || 0;
    const filas = filasDe(wrap);
    let visibles = 0;
    let mostradas = 0;
    filas.forEach((fila) => {
      if (ocultaPorFiltro(fila)) {
        if (fila.classList.contains(CLASE_OCULTA)) fila.classList.remove(CLASE_OCULTA);
        return;
      }
      visibles += 1;
      const ocultar = limite > 0 && visibles > limite;
      if (fila.classList.contains(CLASE_OCULTA) !== ocultar) fila.classList.toggle(CLASE_OCULTA, ocultar);
      if (!ocultar) mostradas += 1;
    });
    const texto = `Mostrando ${mostradas} de ${visibles}`;
    const span = barra.querySelector('.tabla-filas-texto');
    if (span.textContent !== texto) span.textContent = texto;
    const ocultarBarra = visibles <= MINIMO_BARRA;
    if (barra.hidden !== ocultarBarra) barra.hidden = ocultarBarra;
  }

  function aplicarTodo() {
    // Barras huérfanas (su cuadro fue reemplazado)
    raiz.querySelectorAll('.' + CLASE_BARRA).forEach((barra) => {
      const prev = barra.previousElementSibling;
      if (!prev || !prev.classList.contains('table-wrap')) barra.remove();
    });
    raiz.querySelectorAll('.table-wrap').forEach((wrap) => {
      if (!wrap.querySelector('table')) return;
      if (wrap.hasAttribute('data-sin-limite')) return; // tablas con paginación propia (p. ej. historial de partes)
      aplicar(wrap, barraDe(wrap));
    });
  }

  function esDeLaBarra(nodo) {
    return nodo && nodo.nodeType === 1
      ? nodo.classList.contains(CLASE_BARRA) || !!nodo.closest('.' + CLASE_BARRA)
      : !!(nodo && nodo.parentElement && nodo.parentElement.closest('.' + CLASE_BARRA));
  }

  function mutacionPropia(m) {
    if (esDeLaBarra(m.target)) return true;
    if (m.type === 'childList') {
      const nodos = [...m.addedNodes, ...m.removedNodes];
      return nodos.length > 0 && nodos.every((n) => n.nodeType === 1 && n.classList.contains(CLASE_BARRA));
    }
    return false;
  }

  let timer = null;
  const observer = new MutationObserver((mutaciones) => {
    if (mutaciones.every(mutacionPropia)) return;
    clearTimeout(timer);
    timer = setTimeout(aplicarTodo, 50);
  });
  observer.observe(raiz, { childList: true, subtree: true, attributes: true, attributeFilter: ['style', 'hidden'] });

  aplicarTodo();
})();
